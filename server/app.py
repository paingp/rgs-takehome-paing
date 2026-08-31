"""FastAPI app: serves the viewer, DZI tiles, and (later) the detection API.

A thin adapter over takeoff/. Any logic that belongs to detection belongs in takeoff/.

Page numbers are 1-based everywhere in the HTTP API, matching the CLI and the sheet numbers
a person reads off the drawing set. The 0-based page index stays inside takeoff/.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import threading
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from eval import harness, report as report_mod, suites
from takeoff import candidates as cand
from takeoff import banding, classes, detect, doors, layout, raster, regions, schema, spaces

# The drawing the project ships with. Anything else a person uploads lands beside it and is
# treated identically -- the goal is to count symbols on ANY rasterized drawing, so a scan is
# a first-class input rather than a special case.
BUNDLED_SOURCE = Path("Skanksa.pdf")
DOCUMENTS_DIR = Path("documents")
STATIC = Path(__file__).parent / "static"

# Uploads arrive as a raw request body rather than multipart, which keeps `python-multipart`
# out of a deliberately pinned stack. One drawing per request is all this needs.
MAX_UPLOAD_BYTES = 512 * 1024 * 1024
UPLOAD_SUFFIXES = frozenset({".pdf"}) | raster.IMAGE_SUFFIXES
TILE_NAME = re.compile(r"^\d+_\d+\.png$")

app = FastAPI(title="Symbol Spotter")
app.mount("/static", StaticFiles(directory=STATIC), name="static")

# Pyramid builds run off-thread so a 15 s build does not block the event loop. State is
# per-process and deliberately not persisted: the COMPLETE marker on disk is the real
# source of truth, this is only progress reporting for a build in flight.
_builds: dict[int, dict] = {}
_builds_lock = threading.Lock()

# Candidate generation costs ~1.4 s on a cached raster and its result is immutable for a
# given (pdf, page, dpi), so it is held per process. The raster itself is disk-cached.
_candidates: dict[tuple[str, int, int], tuple[object, list[cand.Candidate]]] = {}
_candidates_lock = threading.Lock()

# Components too big to be a symbol, kept apart from the candidates on purpose: they are only
# ever searched INSIDE, never scored whole or grouped. See `candidates.host_blobs`.
_hosts: dict[tuple[str, int, int], list[cand.Candidate]] = {}
_hosts_lock = threading.Lock()

# The ink layers behind each segmentation, kept so a drag can be re-read at finer grain than
# the sheet-wide candidate pool allows. See `candidates.fine_candidates`.
_layers: dict[tuple[str, int, int], object] = {}
_layers_lock = threading.Lock()

_words: dict[tuple[str, int], list[layout.Word]] = {}
_words_lock = threading.Lock()

_regions: dict[tuple[str, int], list[regions.Region]] = {}
_regions_lock = threading.Lock()

# One reference entry per registered class, built from ITS OWN anchor page. Identification
# needs these to work on any sheet but the anchor's, and takeoff/ cannot render a second page
# -- that is this layer's job. Held per process; a class's reference never changes.
_library: dict[str, object] = {}
_library_lock = threading.Lock()

# Reading a sheet -- the candidate pass on every segmentation a class might want, plus the
# reference library -- is ~23 s on a first drag and ~0.3 s afterwards. None of it depends on
# WHERE the drag is, so it is done when the sheet is opened rather than when a person has
# finished dragging a box and is waiting for an answer. Progress is reported so the viewer
# can say the sheet is still being read instead of looking broken.
_warm: dict[tuple[str, int], dict] = {}
_warm_lock = threading.Lock()

# One lock per thing being built, so a drag that lands while the sheet is still being read
# WAITS for that pass instead of starting a second one over the same pixels. The cache locks
# above are held only across a dict access; without these, warming a sheet in the background
# and dragging on it immediately means two threads doing the same 4 s pass at once, on a
# machine that has just been asked to do it as fast as possible.
_builders: dict[object, threading.Lock] = {}
_builders_guard = threading.Lock()


def _builder_lock(key: object) -> threading.Lock:
    with _builders_guard:
        return _builders.setdefault(key, threading.Lock())

ACCENT_BGR = (178, 114, 0)  # #0072B2, the counted-band colour, in OpenCV's channel order
PREVIEW_TARGET_PX = 260
PREVIEW_PAD_PX = 4

# Feet of building per inch of paper, at the 1/8in = 1ft-0in these plans use.
# Deferred as a real setting (decision 13); hard-coded here so a door's radius
# can be reported as a width a person recognises.
PLAN_SCALE_FT_PER_IN = 8.0


class DragBox(BaseModel):
    """A rough drag, in the tile pyramid's image pixels -- what the viewer works in."""

    bbox_image_px: tuple[float, float, float, float] = Field(
        ..., description="x, y, w, h in DZI image pixels"
    )


class TruthInstanceIn(BaseModel):
    """One confirmed instance, in the tile pyramid's image pixels -- what the viewer works in."""

    class_id: str
    bbox_image_px: tuple[float, float, float, float]
    label: str | None = None
    occluded: bool = False


class TruthRequest(BaseModel):
    """The whole page's annotations, replacing whatever was stored.

    Whole-page rather than incremental on purpose: the viewer holds the complete picture
    while a person is working, and a partial update would leave no way to record that an
    instance was DELETED without inventing a tombstone.
    """

    instances: list[TruthInstanceIn]

    # Classes the annotator has passed over here, whether or not they found any. This is how
    # "no elevation markers on T4" gets said at all -- without it that claim and an
    # unannotated sheet are the same request, and the harness cannot grade either.
    reviewed_classes: list[str] = []


class CountRequest(BaseModel):
    """Count one class on a page.

    With `bbox_image_px`, the template is whatever the person just selected. Without it, the
    template is rebuilt from the class's registered anchor -- the same glyph, reached without
    a browser, which is what the tests use.
    """

    # Optional, and normally absent. What is being counted is decided by what was selected,
    # because a second input that can disagree with the drag is a bug waiting to happen --
    # selecting a marker while this said "door_swing" applied the door's thresholds to a
    # triangle and labelled every result a door. Supply it only to pin the class deliberately.
    class_id: str | None = None
    bbox_image_px: tuple[float, float, float, float] | None = None
    keep_rejected: bool = False

    # Pieces of the selection that are NOT part of the symbol, as the boxes `/select`
    # reported. The drag says where to look; this says which of what was found belongs.
    #
    # Boxes rather than indices on purpose: identifying the class can re-snap the drag on that
    # class's own segmentation, and the pieces are not the same objects afterwards. A box
    # still points at the same ink.
    exclude_parts_image_px: list[tuple[float, float, float, float]] = []

    # And the other direction: pieces the RULE set aside that the person wants back. A
    # marker's `C/T9` label is dropped by default and that is right for counting, but a
    # person who meant to include it must be able to say so. Without this the default is the
    # only reachable answer, which is not a choice.
    include_parts_image_px: list[tuple[float, float, float, float]] = []


def documents() -> dict[str, Path]:
    """Every drawing the tool can open, keyed by content hash.

    The hash is the id on purpose: uploading the same drawing twice is the same document, and
    every cache under `cache/` is already keyed the same way, so a re-upload costs nothing.
    """
    found: dict[str, Path] = {}
    for path in [BUNDLED_SOURCE, *sorted(DOCUMENTS_DIR.glob("*"))]:
        if not path.is_file() or path.suffix.lower() not in UPLOAD_SUFFIXES:
            continue
        try:
            found[raster.source_hash(path)] = path
        except OSError:  # pragma: no cover - unreadable file mid-listing
            continue
    return found


def _source(doc: str | None) -> Path:
    """Resolve a document id to a path. No id means the drawing that ships with the tool."""
    if doc is None:
        return BUNDLED_SOURCE
    known = documents()
    if doc not in known:
        raise HTTPException(404, f"no document {doc!r}; upload it first")
    return known[doc]


def _describe(doc_id: str, path: Path) -> dict:
    kind = "image" if raster.is_image(path) else "pdf"
    return {
        "id": doc_id,
        "name": path.name,
        "kind": kind,
        "pages": raster.page_count(path),
        "has_text_layer": raster.has_text_layer(path),
        # A PDF states its physical size, so this is derived. A scan does not, so it is
        # declared -- and it only affects the candidate size band and the readouts in feet,
        # because templates and radius bands are measured from the selection.
        "dpi": raster.DETECTION_DPI if kind == "pdf" else raster.DEFAULT_IMAGE_DPI,
        "bundled": path == BUNDLED_SOURCE,
    }


def _class_segmentation(symbol: classes.SymbolClass) -> tuple[int, int]:
    """The repair gap and ink threshold this class wants, defaults filled in.

    One place, because the answer is needed wherever a class's own ink is built: the count
    endpoint, the anchor path, identification and the template library. Four copies of the
    same two-line default is how one of them ends up stale.
    """
    return (
        cand.REPAIR_GAP_PX if symbol.repair_gap_px is None else symbol.repair_gap_px,
        cand.INK_THRESHOLD if symbol.ink_threshold is None else symbol.ink_threshold,
    )


def _wants_own_ink(symbol: classes.SymbolClass) -> bool:
    return symbol.repair_gap_px is not None or symbol.ink_threshold is not None


def _candidates_for(
    number: int,
    repair_gap_px: int | None = None,
    doc: str | None = None,
    ink_threshold: int | None = None,
) -> tuple[raster.Raster, list[cand.Candidate]]:
    """Raster and candidate list for a page, built once per segmentation per process.

    Repair and the ink threshold are both keyed in because both change the segmentation, and
    different classes want different ones: a matched glyph wants the pieces a wall broke apart
    put back, a swept arc wants to stay a thin curve, and a lightly drawn glyph needs a lower
    cut or it arrives in fragments. See SymbolClass.repair_gap_px and .ink_threshold.
    """
    source = _source(doc)
    index = _index(number, doc)
    gap = cand.REPAIR_GAP_PX if repair_gap_px is None else repair_gap_px
    cut = cand.INK_THRESHOLD if ink_threshold is None else ink_threshold
    key = (str(source), number, gap, cut)
    with _candidates_lock:
        hit = _candidates.get(key)
    if hit is not None:
        return hit  # type: ignore[return-value]

    with _builder_lock(key):
        # Re-check: another thread may have finished this exact pass while we queued.
        with _candidates_lock:
            hit = _candidates.get(key)
        if hit is not None:
            return hit  # type: ignore[return-value]

        r = raster.render(source, index, dpi=_declared_dpi(source))
        layers = cand.ink_layers(r, ink_threshold=cut, repair_gap_px=gap)
        found = cand.find_candidates(r, layers)
        with _candidates_lock:
            _candidates[key] = (r, found)
        with _layers_lock:
            _layers[key] = layers
        with _hosts_lock:
            _hosts[key] = cand.host_blobs(r, layers)
        return r, found


def _fine_for(
    number: int,
    drag_px: cand.BBox,
    r: raster.Raster,
    doc: str | None = None,
    repair_gap_px: int | None = None,
    ink_threshold: int | None = None,
) -> list[cand.Candidate]:
    """Sub-candidate ink inside one drag, on the segmentation that drag is being read on.

    The sheet-wide size floor exists because a sheet holds millions of specks. Inside a box a
    person drew there is no such problem, and applying the floor there loses real symbols --
    E4's duplex receptacle is nine fragments at the default cut and five are under it. See
    `candidates.fine_candidates`.
    """
    source = _source(doc)
    gap = cand.REPAIR_GAP_PX if repair_gap_px is None else repair_gap_px
    cut = cand.INK_THRESHOLD if ink_threshold is None else ink_threshold
    key = (str(source), number, gap, cut)
    with _layers_lock:
        layers = _layers.get(key)
    if layers is None:
        _candidates_for(number, gap, doc=doc, ink_threshold=cut)
        with _layers_lock:
            layers = _layers.get(key)
    if layers is None:
        return []
    return cand.fine_candidates(r, drag_px, layers=layers)


def _hosts_for(number: int, doc: str | None = None, **kw) -> list[cand.Candidate]:
    """Components too big to be a symbol, which is where fused instances hide.

    Cached alongside the candidates and on the same key, because they come from the same ink
    and a class that segments differently must not be handed another class's hosts.
    """
    source = _source(doc)
    gap = cand.REPAIR_GAP_PX if kw.get("repair_gap_px") is None else kw["repair_gap_px"]
    cut = cand.INK_THRESHOLD if kw.get("ink_threshold") is None else kw["ink_threshold"]
    key = (str(source), number, gap, cut)
    with _hosts_lock:
        hit = _hosts.get(key)
    if hit is None:
        _candidates_for(number, doc=doc, repair_gap_px=kw.get("repair_gap_px"),
                        ink_threshold=kw.get("ink_threshold"))
        with _hosts_lock:
            hit = _hosts.get(key, [])
    return hit


def _regions_for(number: int, doc: str | None = None) -> list[regions.Region]:
    """The page's blocks, segmented once per page per process.

    Keyed without the repair gap: repair closes gaps a few pixels wide inside a glyph and
    cannot move a paragraph, so the segmentation is the same either way and one pass serves
    every class.
    """
    source = _source(doc)
    key = (str(source), number)
    with _regions_lock:
        hit = _regions.get(key)
    if hit is not None:
        return hit

    r, found = _candidates_for(number, doc=doc)
    found_regions = regions.segment(r, found)
    with _regions_lock:
        _regions[key] = found_regions
    return found_regions


def _counting_regions(
    number: int, r: raster.Raster, selection_px: tuple[float, float] | None,
    doc: str | None = None,
) -> list[regions.Region] | None:
    """The segmentation to count within, or None to count the whole sheet.

    A person who drags a symbol out of the legend means it -- the legend is set type by every
    measure, and gating it away would answer their gesture with nothing. So the gate is
    dropped entirely when the selection came from inside a text block, rather than counting
    on a pool the selection is not in.
    """
    found_regions = _regions_for(number, doc)
    if selection_px is not None:
        if regions.kind_at(found_regions, *selection_px) == regions.TEXT:
            return None
    return found_regions


def _declared_dpi(source: Path) -> int:
    return raster.DEFAULT_IMAGE_DPI if raster.is_image(source) else raster.DETECTION_DPI


def _words_for(number: int, r: raster.Raster, doc: str | None = None) -> list[layout.Word]:
    """The page's text layer in raster pixels, read once per process.

    Empty for a scan, which has none. A detection then arrives without a caption rather than
    the count failing.
    """
    source = _source(doc)
    key = (str(source), number)
    with _words_lock:
        hit = _words.get(key)
    if hit is not None:
        return hit
    found = layout.words_px(source, r.page_index, r.dpi, r.origin_sheet_pt)
    with _words_lock:
        _words[key] = found
    return found


def _segmentations() -> list[tuple[int, int]]:
    """Every (repair gap, ink threshold) a registered class needs, default first.

    Default first because that is the reading most classes use, so identification answers
    from it without touching the others; and because a page that is only ever going to hold
    one class should not wait on segmentations built for the rest of the vocabulary.
    """
    wanted = [(cand.REPAIR_GAP_PX, cand.INK_THRESHOLD)]
    for symbol in classes.all_classes():
        one = _class_segmentation(symbol)
        if one not in wanted:
            wanted.append(one)
    return wanted


def _warm_worker(number: int, source: Path, doc: str | None) -> None:
    """Build everything a drag on this sheet will need, off the request thread."""
    key = (str(source), number)
    steps = 1 + len(_segmentations())

    def mark(done: int, message: str, state: str = "reading") -> None:
        with _warm_lock:
            _warm[key] = {"state": state, "progress": done / steps, "message": message}

    try:
        mark(0, "building the reference symbols")
        _class_library()
        for i, (gap, cut) in enumerate(_segmentations(), start=1):
            mark(i, f"pass {i} of {steps - 1}")
            _candidates_for(number, gap, doc=doc, ink_threshold=cut)
        with _warm_lock:
            _warm[key] = {"state": "ready", "progress": 1.0, "message": ""}
    except Exception as exc:  # a sheet that will not read must not wedge the viewer
        with _warm_lock:
            _warm[key] = {"state": "failed", "progress": 1.0, "message": str(exc)}


def _forget_warm() -> None:
    """A sheet is only "read" against the vocabulary it was read for.

    Adding or removing a class can change which segmentations exist -- a class drawn on a thin
    CAD layer asks for its own ink threshold -- so a page marked ready before the change may be
    missing one. Dropping the marks makes the next open warm it again; the candidate caches
    themselves stay, so re-warming only builds what is genuinely new.
    """
    with _warm_lock:
        _warm.clear()


def _warm_state(number: int, doc: str | None = None) -> dict:
    with _warm_lock:
        return dict(_warm.get((str(_source(doc)), number),
                              {"state": "cold", "progress": 0.0, "message": ""}))


def _class_library() -> dict:
    """Reference entries for every registered class, each from its own anchor page."""
    with _library_lock:
        if _library:
            return dict(_library)

    with _builder_lock("class-library"):
        with _library_lock:
            if _library:
                return dict(_library)
        return _build_class_library()


def _build_class_library() -> dict:
    built: dict[str, object] = {}
    for symbol in classes.all_classes():
        index = symbol.anchor.page_index
        try:
            ref_source = Path(symbol.anchor.source)
            ref = raster.render(ref_source, index, dpi=_declared_dpi(ref_source))
            gap, cut = _class_segmentation(symbol)
            found = cand.find_candidates(
                ref, cand.ink_layers(ref, ink_threshold=cut, repair_gap_px=gap)
            )
            built[symbol.id] = detect.build_entry(symbol, ref, found)
        except Exception:  # a bad anchor must not take the whole app down
            continue

    with _library_lock:
        _library.update(built)
    return dict(built)


def _to_image_px(bbox_px: tuple[float, float, float, float], r: raster.Raster) -> list[float]:
    """Detection px -> tile-pyramid image px, via sheet_pt rather than by assuming a scale.

    The two DPIs are equal today, so a caller that just passed the numbers through would be
    accidentally right. Going through spaces.rebase_px means a future DPI change moves the
    overlay correctly instead of silently misaligning every box.
    """
    x, y, w, h = bbox_px
    x0, y0, x1, y1 = spaces.rebase_px(
        (x, y, x + w, y + h), r.dpi, r.origin_sheet_pt, raster.VIEWER_DPI
    )
    return [x0, y0, x1 - x0, y1 - y0]


def _to_detection_px(bbox_image_px: tuple[float, float, float, float], r: raster.Raster) -> cand.BBox:
    x, y, w, h = bbox_image_px
    x0, y0, x1, y1 = spaces.rebase_px(
        (x, y, x + w, y + h), raster.VIEWER_DPI, (0.0, 0.0), r.dpi, r.origin_sheet_pt
    )
    return (int(round(x0)), int(round(y0)), int(round(x1 - x0)), int(round(y1 - y0)))


def _preview_png(selection: cand.Selection, r: raster.Raster) -> str:
    """A magnified crop of the snapped glyph with its component boundary outlined.

    Integer nearest-neighbour upscaling on purpose: at these sizes a receptacle is 28 px, and
    smoothing it would show the user a cleaner glyph than the detector actually sees.
    """
    x, y, w, h = selection.bbox_px
    pad = PREVIEW_PAD_PX
    y0, y1 = max(y - pad, 0), min(y + h + pad, r.gray.shape[0])
    x0, x1 = max(x - pad, 0), min(x + w + pad, r.gray.shape[1])
    crop = r.gray[y0:y1, x0:x1]

    scale = max(1, min(12, PREVIEW_TARGET_PX // max(crop.shape[0], crop.shape[1], 1)))
    big = cv2.resize(
        crop, (crop.shape[1] * scale, crop.shape[0] * scale), interpolation=cv2.INTER_NEAREST
    )
    canvas = cv2.cvtColor(big, cv2.COLOR_GRAY2BGR)

    # Outline the ink that snapping actually claimed, placed back in the padded crop.
    stencil = np.zeros(crop.shape, np.uint8)
    stencil[y - y0 : y - y0 + h, x - x0 : x - x0 + w] = selection.mask.astype(np.uint8)
    stencil = cv2.resize(
        stencil, (canvas.shape[1], canvas.shape[0]), interpolation=cv2.INTER_NEAREST
    )
    contours, _ = cv2.findContours(stencil, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    cv2.drawContours(canvas, contours, -1, ACCENT_BGR, max(1, scale // 4))

    ok, buf = cv2.imencode(".png", canvas)
    if not ok:  # pragma: no cover - cv2 only fails here on a malformed array
        raise HTTPException(500, "could not encode preview")
    return "data:image/png;base64," + base64.b64encode(buf.tobytes()).decode()


def _index(number: int, doc: str | None = None) -> int:
    count = raster.page_count(_source(doc))
    if not 1 <= number <= count:
        raise HTTPException(404, f"page {number} out of range 1..{count}")
    return number - 1


def _build_worker(number: int, index: int, source: Path) -> None:
    def progress(fraction: float, message: str) -> None:
        with _builds_lock:
            _builds[number].update(progress=fraction, message=message)

    try:
        raster.build_dzi(source, index, progress=progress)
        with _builds_lock:
            _builds.pop(number, None)
    except Exception as exc:  # surfaced to the viewer rather than dying silently
        with _builds_lock:
            _builds[number] = {"state": "error", "progress": 0.0, "message": str(exc)}


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    return HTMLResponse((STATIC / "index.html").read_text(encoding="utf-8"))


@app.get("/api/documents")
def list_documents() -> dict:
    known = documents()
    return {"documents": [_describe(i, p) for i, p in sorted(known.items(), key=lambda kv: kv[1].name)]}


@app.post("/api/documents")
async def upload_document(request: Request, name: str) -> dict:
    """Take one drawing as a raw body. PDF or image; a scan is not a lesser input."""
    suffix = Path(name).suffix.lower()
    if suffix not in UPLOAD_SUFFIXES:
        raise HTTPException(
            400, f"{suffix or 'that'} is not a drawing; expected PDF or "
                 f"{', '.join(sorted(raster.IMAGE_SUFFIXES))}")

    data = await request.body()
    if not data:
        raise HTTPException(400, "empty upload")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"{len(data) / 1e6:.0f} MB exceeds the {MAX_UPLOAD_BYTES / 1e6:.0f} MB limit")

    DOCUMENTS_DIR.mkdir(parents=True, exist_ok=True)
    # Written under its own content hash, so the same drawing uploaded twice is one document
    # and every cache keyed on that hash is already warm.
    digest = hashlib.sha256(data).hexdigest()[:12]
    target = DOCUMENTS_DIR / f"{digest}{suffix}"
    if not target.exists():
        target.write_bytes(data)

    try:
        raster.page_count(target)          # cheap validity check
    except Exception as exc:
        target.unlink(missing_ok=True)
        raise HTTPException(400, f"could not open that file as a drawing: {exc}") from exc

    return _describe(raster.source_hash(target), target)


@app.get("/api/pages")
def pages(doc: str | None = None) -> dict:
    source = _source(doc)
    return {
        "pdf": source.name,
        "count": raster.page_count(source),
        "document": _describe(raster.source_hash(source), source),
    }


@app.get("/api/pages/{number}/status")
def status(number: int, doc: str | None = None) -> dict:
    source = _source(doc)
    index = _index(number, doc)
    with _builds_lock:
        pending = dict(_builds.get(number, {}))

    if raster.dzi_is_built(source, index):
        width, height = (
            int(v) for v in (raster.dzi_dir(source, index) / "COMPLETE").read_text().split()
        )
        return {
            "state": "ready",
            "progress": 1.0,
            "message": "",
            "width": width,
            "height": height,
            "dpi": raster.VIEWER_DPI,
            # The tile URLs OpenSeadragon derives from this must carry the document too,
            # or a second drawing would be served the first one's tiles.
            "dzi": f"/api/pages/{number}/sheet.dzi" + (f"?doc={doc}" if doc else ""),
        }
    if pending:
        return {"state": pending.get("state", "building"), **pending}
    return {"state": "idle", "progress": 0.0, "message": "tile pyramid not built"}


@app.post("/api/pages/{number}/build")
def build(number: int, doc: str | None = None) -> dict:
    source = _source(doc)
    index = _index(number, doc)
    if raster.dzi_is_built(source, index):
        return {"state": "ready"}
    with _builds_lock:
        if number not in _builds:
            _builds[number] = {"state": "building", "progress": 0.0, "message": "starting"}
            threading.Thread(target=_build_worker, args=(number, index, source), daemon=True).start()
    return {"state": "building"}


@app.post("/api/pages/{number}/warm")
def warm(number: int, doc: str | None = None) -> dict:
    """Read the sheet now, so the first drag does not have to wait for it.

    Idempotent and non-blocking: the viewer calls it as soon as a page is on screen and polls
    the same shape back. Everything it builds is already cached per process, so a second call
    while the first is running is a no-op rather than a second pass over the same pixels.
    """
    source = _source(doc)
    _index(number, doc)                      # 404 for a page that does not exist
    key = (str(source), number)
    with _warm_lock:
        if _warm.get(key, {}).get("state") in {"reading", "ready"}:
            return dict(_warm[key])
        _warm[key] = {"state": "reading", "progress": 0.0, "message": "starting"}
    threading.Thread(target=_warm_worker, args=(number, source, doc), daemon=True).start()
    return dict(_warm[key])


@app.get("/api/pages/{number}/warm")
def warm_state(number: int, doc: str | None = None) -> dict:
    return _warm_state(number, doc)


@app.get("/api/pages/{number}/sheet.dzi")
def descriptor(number: int, doc: str | None = None) -> Response:
    source = _source(doc)
    index = _index(number, doc)
    path = raster.dzi_dir(source, index) / "sheet.dzi"
    if not raster.dzi_is_built(source, index) or not path.exists():
        raise HTTPException(404, f"tile pyramid for page {number} is not built")
    return Response(path.read_text(), media_type="application/xml")


@app.get("/api/pages/{number}/sheet_files/{level}/{tile}")
def tile(number: int, level: int, tile: str, doc: str | None = None) -> FileResponse:
    """Serve one Deep Zoom tile. OpenSeadragon derives this path from the .dzi URL."""
    source = _source(doc)
    index = _index(number, doc)
    if not TILE_NAME.match(tile) or level < 0:
        raise HTTPException(400, "bad tile address")
    path = raster.dzi_dir(source, index) / "sheet_files" / str(level) / tile
    if not path.exists():
        raise HTTPException(404, "no such tile")
    # A built pyramid never changes in place: build_dzi rewrites the whole directory.
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "max-age=86400"})


@app.get("/api/pages/{number}/candidates")
def page_candidates(number: int, doc: str | None = None) -> dict:
    """Every symbol-sized component on the sheet, in tile-pyramid image pixels."""
    r, found = _candidates_for(number, doc=doc)
    return {
        "detection_dpi": r.dpi,
        "viewer_dpi": raster.VIEWER_DPI,
        "size_band_in": list(cand.SYMBOL_BAND_IN),
        "count": len(found),
        "boxes": [_to_image_px(c.bbox_px, r) for c in found],
        # What the count would actually consider. A candidate in the general notes is real
        # ink and stays visible here -- it is simply not somewhere an instance can be.
        "regions": [
            {
                "bbox_image_px": _to_image_px(g.bbox_px, r),
                "kind": g.kind,
                "components": g.components,
                "uniformity": round(g.uniformity, 3),
            }
            for g in _regions_for(number, doc)
        ],
        "countable": len(regions.countable(_regions_for(number, doc), found)),
    }


@app.post("/api/pages/{number}/select")
def select(number: int, drag: DragBox, doc: str | None = None) -> dict:
    """Resolve a rough drag box to the component group that is the symbol.

    Resolved on the segmentation that READS it, not on the default one.

    What a symbol is made of depends on the ink threshold, and the default is measured on
    architectural sheets. E4's duplex receptacle is drawn on a thin CAD layer: at the default
    cut its two bars produce no ink at all, so a drag around the whole glyph came back as a
    31x31 circle where the symbol is 47x31, and a person who had carefully boxed the whole
    thing was shown half of it. The count was right -- identifying the class re-snaps on that
    class's own ink -- so this was the preview disagreeing with the answer.

    So selection now runs the same probe identification does, and returns the reading that
    recognised something. It costs a candidate pass per segmentation on first use and nothing
    after; `/count` was already paying it.
    """
    r, found = _candidates_for(number, doc=doc)
    drag_px = _to_detection_px(drag.bbox_image_px, r)
    selection = cand.snap(found, drag_px, dpi=r.dpi,
                          fine=_fine_for(number, drag_px, r, doc=doc))

    if selection.is_empty:
        return {"found": False, "reason": "no symbol-sized ink inside that box"}

    # Only once there is something to identify. Probing blank paper asks three segmentations
    # about nothing and answers with whatever the fallback reading returns.
    symbol, _why, r, found, selection = _identify_anywhere(
        number, drag.bbox_image_px, r, found, selection, doc
    )
    if selection.is_empty:
        return {"found": False, "reason": "no symbol-sized ink inside that box"}

    width_in, height_in = selection.size_in
    return {
        "found": True,
        "read_as": symbol.id if symbol.id in classes.REGISTRY else None,
        "bbox_image_px": _to_image_px(selection.bbox_px, r),
        "component_count": len(selection.members),
        "size_px": list(selection.size_px),
        "size_in": [round(width_in, 4), round(height_in, 4)],
        "ink_px": selection.area_px,
        "preview_png": _preview_png(selection, r),
        # Every piece the box held, largest ink first, and whether it is currently part of
        # the symbol. Active pieces can be dropped; inactive ones -- a line of characters the
        # rule read as a caption -- can be switched back on. Both directions matter: no
        # measurement reliably separates a symbol's own parts from an annotation beside it,
        # so the rule's answer has to be visible and reversible rather than silent.
        "parts": [
            {"bbox_image_px": _to_image_px(c.bbox_px, r), "ink_px": c.area_px, "active": True}
            for c in selection.members
        ] + [
            {"bbox_image_px": _to_image_px(c.bbox_px, r), "ink_px": c.area_px, "active": False,
             "why": "reads as a label rather than part of the symbol"}
            for c in selection.set_aside
        ],
    }


def _with_part_choices(selection, exclude, include, r: raster.Raster):
    """Apply a person's verdict on the pieces: drop these, keep those.

    Both lists are BOXES rather than indices, and matched by centre: identifying the class can
    re-snap the drag on that class's own segmentation, so the pieces are different objects
    afterwards covering the same ink. A box still points at the same place.

    Includes are applied first. A piece the rule set aside becomes a member, and only then can
    the exclusions be read against the final member list -- doing it the other way round means
    a person cannot switch a caption on and a quadrant off in the same gesture.
    """
    if include:
        wanted = [_to_detection_px(b, r) for b in include]
        add = [
            i for i, c in enumerate(selection.set_aside)
            if any(x <= c.centroid_px[0] <= x + w and y <= c.centroid_px[1] <= y + h
                   for x, y, w, h in wanted)
        ]
        selection = selection.plus(add)
    if not exclude:
        return selection
    return _without_parts(selection, exclude, r)


def _without_parts(selection, boxes, r: raster.Raster):
    """Drop the pieces a person marked as not part of the symbol."""
    excluded = [_to_detection_px(b, r) for b in boxes]
    drop = []
    for i, c in enumerate(selection.members):
        cx, cy = c.centroid_px
        if any(x <= cx <= x + w and y <= cy <= y + h for x, y, w, h in excluded):
            drop.append(i)
    return selection.without(drop) if drop else selection


def _detection_payload(d: detect.Detection, r: raster.Raster, words: list[layout.Word]) -> dict:
    """Everything known about one instance, in units a person reads rather than raw pixels."""
    width_in, height_in = d.size_in(r.dpi)
    centre_x_in, centre_y_in = d.centre_in(r.dpi)
    near = layout.words_near(words, d.bbox_px)
    pattern = classes.REGISTRY[d.class_id].label_pattern if d.class_id in classes.REGISTRY else None
    label = layout.label_for(words, d.bbox_px, pattern=pattern)

    return {
        "id": d.id,
        "class_id": d.class_id,
        "status": d.status.value,
        "colour": d.colour,
        "reason": d.reason,
        "label": label,
        "nearby_text": [w.text for w in near],
        "match": d.match,
        "forward": d.forward,
        "backward": d.backward,
        "asymmetry": round(d.asymmetry, 4),
        "margin": d.margin,
        "runner_up": d.runner_up,
        "variant": d.variant_label,
        "size_px": list(d.size_px),
        "size_in": [round(width_in, 4), round(height_in, 4)],
        "ink_px": d.ink_px,
        "fill": round(d.ink_px / max(d.size_px[0] * d.size_px[1], 1), 4),
        "centre_in": [round(centre_x_in, 3), round(centre_y_in, 3)],
        "bbox_px": list(d.bbox_px),
        "bbox_image_px": _to_image_px(d.bbox_px, r),
    }


class NewClass(BaseModel):
    """A name for the symbol currently selected."""

    name: str
    page: int
    bbox_image_px: tuple[float, float, float, float]


@app.post("/api/classes")
def add_class(request: NewClass, doc: str | None = None) -> dict:
    """Name a selection, so it can be annotated against and graded.

    The built-in vocabulary is three symbols and that is a fair bet against no real drawing
    set: a mechanical sheet has diffusers and dampers, a plumbing sheet has fixtures, and none
    of them can be counted against a name until somebody supplies one. Until now an unknown
    symbol was counted `unnamed`, which is enough to get a number and not enough to record
    ground truth for it or to grade it -- both key on a class id.

    A new class is a NAME FOR A SELECTION, not a bare label. It stores the drag as its anchor,
    which is what lets it behave like a built-in everywhere: identified on other sheets, built
    into a template bank by the harness, offered when annotating. A class with no reference
    instance would have needed a guard in every consumer of the registry and still could not
    be counted.

    Its thresholds are the generic 0.90/0.80 -- the same numbers an unregistered symbol is
    already counted on. Naming a symbol changes what it is CALLED and what it can be graded
    against, not how it scores; re-derive the thresholds once it has ground truth.
    """
    name = request.name.strip()
    if not name:
        raise HTTPException(400, "a class needs a name")
    class_id = classes.slug(name)
    if not class_id:
        raise HTTPException(400, "that name has no letters or digits in it")
    if class_id in classes.REGISTRY:
        raise HTTPException(409, f"{classes.get(class_id).name!r} is already registered")

    r, _ = _candidates_for(request.page, doc=doc)
    source = _source(doc)
    symbol = classes.user_class(
        name,
        classes.TemplateAnchor(
            page_index=_index(request.page, doc),
            drag_bbox_px=_to_detection_px(request.bbox_image_px, r),
            dpi=r.dpi,
            source=str(source),
        ),
    )
    classes.register(symbol)
    classes.save_user_class(symbol)

    # The reference library is built once per process and now has one more entry in it.
    with _library_lock:
        _library.clear()
    _forget_warm()

    return {"id": symbol.id, "name": symbol.name, "detector": symbol.detector,
            "counted_at": symbol.counted_at, "review_floor": symbol.review_floor,
            "user_defined": True}


@app.delete("/api/classes/{class_id}")
def remove_class(class_id: str) -> dict:
    """Unregister a class a person added.

    Only theirs. A built-in ships with the tool and its anchor lives in `takeoff/classes.py`
    rather than in data, so removing one would be a code change pretending to be a button.

    Annotations already recorded against it are LEFT ALONE. They are somebody's work, and
    deleting them quietly would be the worst possible reading of "remove the class"; they keep
    their id, the editor still offers it as an unregistered label, and re-adding the same name
    picks them back up.
    """
    try:
        removed = classes.remove_user_class(class_id)
    except ValueError as exc:
        raise HTTPException(403, str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc

    with _library_lock:
        _library.clear()
    _forget_warm()
    return {"id": removed.id, "name": removed.name, "removed": True}


@app.get("/api/classes")
def symbol_classes() -> dict:
    """The registry: what ships with the tool, plus whatever a person has named."""
    return {
        "classes": [
            {
                "id": s.id,
                "name": s.name,
                # The viewer needs this to know whether a selection is required first: a
                # swept class has no template to pick.
                # "auto" means the selection decides; the viewer therefore asks for one
                # from every class, which is what keeps the gesture the same throughout.
                "detector": s.detector,
                "counted_at": s.counted_at,
                "review_floor": s.review_floor,
                "anchor_page": s.anchor.page_index + 1,
                "notes": s.notes,
                # Only a class a person added can be removed; the built-ins are the tool.
                "user_defined": classes.is_user_class(s.id),
            }
            for s in classes.all_classes()
        ]
    }


@app.post("/api/pages/{number}/count")
def count(number: int, request: CountRequest, doc: str | None = None) -> dict:
    """Detect and count one class on a sheet.

    The template comes from the live selection when the viewer sends one, so what a person
    dragged is literally what gets counted. Everything else -- orientations, the size gate,
    the two thresholds -- comes from the class registry.
    """
    r, found = _candidates_for(number, doc=doc)

    symbol, identified_as = None, None
    if request.class_id is not None:
        try:
            symbol = classes.get(request.class_id)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc

    if request.bbox_image_px is not None:
        drag_px = _to_detection_px(request.bbox_image_px, r)
        selection = cand.snap(found, drag_px, dpi=r.dpi,
                              fine=_fine_for(number, drag_px, r, doc=doc))
        if selection.is_empty:
            return {"found": False, "reason": "no symbol-sized ink in that box"}
        # Before identification, not after: what the person excluded is part of saying WHICH
        # symbol this is. Identify the glyph plus a label and it matches no registered class,
        # which costs that class its name, its caption pattern and its calibrated thresholds.
        if request.exclude_parts_image_px or request.include_parts_image_px:
            selection = _with_part_choices(
                selection, request.exclude_parts_image_px, request.include_parts_image_px, r)
            if selection.is_empty:
                return {"found": False, "reason": "every piece of that selection was excluded"}
        if symbol is None:
            symbol, identified_as, r, found, selection = _identify_anywhere(
                number, request.bbox_image_px, r, found, selection, doc
            )
        # Once the class is known, redo the selection on the segmentation THAT class wants.
        # Identification may already have landed on it, in which case this is a no-op; when
        # a class_id was named outright it is what moves the count onto the right ink.
        if _wants_own_ink(symbol):
            gap, cut = _class_segmentation(symbol)
            r, found = _candidates_for(number, gap, doc=doc, ink_threshold=cut)
            drag_px = _to_detection_px(request.bbox_image_px, r)
            selection = cand.snap(
                found, drag_px, dpi=r.dpi,
                fine=_fine_for(number, drag_px, r, doc=doc,
                               repair_gap_px=gap, ink_threshold=cut))
            if request.exclude_parts_image_px or request.include_parts_image_px:
                selection = _with_part_choices(
                    selection, request.exclude_parts_image_px,
                    request.include_parts_image_px, r)
            if selection.is_empty:
                return {"found": False, "reason": "no symbol-sized ink in that box"}

        # The page's raw ink has to travel with the selection: it is what tells an arc
        # whether it pivots on something drawn. Without it every chair back on T4 reads as
        # a door.
        entry = detect.entry_from_selection(
            symbol.id, selection, page_index=r.page_index, symbol=symbol,
            page_ink=doors.page_ink_from(r.gray),
        )
        source = "selection"
    else:
        if symbol is None:
            raise HTTPException(400, "select a symbol, or name a class_id to count")
        if r.page_index != symbol.anchor.page_index:
            raise HTTPException(
                400,
                f"{symbol.id!r} anchors on sheet {symbol.anchor.page_index + 1}; "
                f"select the symbol on this sheet to count it here",
            )
        # The anchor is a selection like any other, so it has to be read on the segmentation
        # THAT class wants -- the same move the selection branch makes above. At the default
        # gap a swing arc merges with its jamb, stops being a thin component, and door_swing
        # falls back to template matching: 15 detections instead of the arc path's 31.
        if _wants_own_ink(symbol):
            gap, cut = _class_segmentation(symbol)
            r, found = _candidates_for(number, gap, doc=doc, ink_threshold=cut)
        entry = detect.build_entry(symbol, r, found)
        source = "anchor"

    # Count inside the sheet's drawing blocks. On T4 that is 47% fewer candidates to group
    # and size-gate; on T5, where the plan fills the sheet, it is 14% and changes nothing.
    # Either way the counts are identical -- what it removes is work, not symbols.
    centre = None
    if request.bbox_image_px is not None:
        box = _to_detection_px(request.bbox_image_px, r)
        centre = (box[0] + box[2] / 2.0, box[1] + box[3] / 2.0)
    scope = _counting_regions(number, r, centre, doc)

    detections = detect.detect(
        r, found, [entry], keep_rejected=request.keep_rejected, regions=scope,
        hosts=_hosts_for(number, doc=doc,
                         repair_gap_px=entry.symbol.repair_gap_px,
                         ink_threshold=entry.symbol.ink_threshold),
    )

    # What the selection turned out to be. The person made one gesture either way, so the
    # panel says which reading it produced and why rather than leaving that invisible.
    reason = entry.profile.reason if entry.profile else None
    if entry.template is None:
        band = entry.radius_band_in
        template_info = {
            "source": source,
            "detector": entry.detector,
            "reason": reason,
            "radius_band_in": [round(band[0], 4), round(band[1], 4)],
            "width_band_ft": [round(band[0] * PLAN_SCALE_FT_PER_IN, 2),
                              round(band[1] * PLAN_SCALE_FT_PER_IN, 2)],
            "trimmed": False,
            "context_blobs": 0,
            "context_ink_px": 0,
        }
    else:
        width_in, height_in = entry.template.size_in
        template_info = {
            "source": source,
            "detector": entry.detector,
            "reason": reason,
            "size_px": list(entry.template.size_px),
            "size_in": [round(width_in, 4), round(height_in, 4)],
            "ink_px": entry.template.ink_px,
            "variants": len(entry.bank),
            "trimmed": entry.template.trimmed,
            "context_blobs": entry.template.context_blobs,
            "context_ink_px": entry.template.context_ink_px,
            "bbox_image_px": _to_image_px(entry.template.source_bbox_px, r),
        }

    return {
        "found": True,
        "class_id": symbol.id,
        "class_name": symbol.name,
        "identified_as": identified_as,
        "registered": symbol.id in classes.REGISTRY,
        "template": template_info,
        "scope": {
            "regions": 0 if scope is None else len(scope),
            "drawing_regions": 0 if scope is None else len(regions.drawing_regions(scope)),
            "candidates": len(found) if scope is None else len(regions.countable(scope, found)),
            "of": len(found),
        },
        "diagnostics": detect.diagnose(r, found, entry, detections),
        "counts": detect.summarise(detections),
        "detections": [_detection_payload(d, r, _words_for(number, r, doc)) for d in detections],
    }


def _identify_anywhere(
    number: int, bbox_image_px, r: raster.Raster, found: list[cand.Candidate],
    selection: cand.Selection, doc: str | None,
):
    """Identify a selection on every segmentation a registered class uses, not just one.

    A class turns repair off because repair destroys the ink it is recognised BY. A door's
    swing is a thin curve, and closing gaps merges it into the jamb beside it -- so on the
    default segmentation a door arc is not a candidate at all, the profiler sees only the
    keynote bubble and its letters, and reads the selection as a shape. Every door dragged
    in the viewer came back "not a symbol registered yet", losing the class name, its caption
    pattern and its calibrated thresholds, while `-m eval.suites` counted them correctly --
    because the harness builds each class on its own segmentation and the server did not.

    Segmentations are tried default-first, so nothing changes for a class that uses it, and
    the first REGISTERED match wins. Falling back to the default reading keeps an unknown
    symbol countable without a name, which is the behaviour identify() already promises.

    A segmentation is a repair gap AND an ink threshold, because both decide what ink a class
    is even made of. Drag a duplex receptacle at the default cut and there is no glyph to
    identify -- it is nine fragments of a dozen pixels.
    """
    library = _class_library()
    for gap, cut in _segmentations():
        page, pool = _candidates_for(number, gap, doc=doc, ink_threshold=cut)
        drag_px = _to_detection_px(bbox_image_px, page)
        here = cand.snap(pool, drag_px, dpi=page.dpi,
                         fine=_fine_for(number, drag_px, page, doc=doc,
                                        repair_gap_px=gap, ink_threshold=cut))
        if here.is_empty:
            continue
        guess, why = detect.identify(here, page, pool, references=library)
        if guess.id in classes.REGISTRY:
            return guess, why, page, pool, here

    guess, why = detect.identify(selection, r, found, references=library)
    return guess, why, r, found, selection


@app.get("/api/pages/{number}/crop")
def crop(number: int, x: float, y: float, w: float, h: float, pad: int = 10, doc: str | None = None) -> dict:
    """A magnified crop of one region, for the detail panel.

    Served on demand rather than embedded in the count response: nine data-URI previews are
    harmless, but a sheet with three hundred receptacles would make that response enormous
    for pixels nobody has looked at yet.
    """
    r, _ = _candidates_for(number, doc=doc)
    bx, by, bw, bh = _to_detection_px((x, y, w, h), r)

    y0, y1 = max(by - pad, 0), min(by + bh + pad, r.gray.shape[0])
    x0, x1 = max(bx - pad, 0), min(bx + bw + pad, r.gray.shape[1])
    if y1 <= y0 or x1 <= x0:
        raise HTTPException(400, "crop falls outside the sheet")

    patch = r.gray[y0:y1, x0:x1]
    scale = max(1, min(12, PREVIEW_TARGET_PX // max(patch.shape[0], patch.shape[1], 1)))
    big = cv2.resize(
        patch, (patch.shape[1] * scale, patch.shape[0] * scale), interpolation=cv2.INTER_NEAREST
    )
    canvas = cv2.cvtColor(big, cv2.COLOR_GRAY2BGR)

    # Frame where the detection sits inside the padded crop, so the glyph is distinguishable
    # from whatever the padding dragged in beside it.
    cv2.rectangle(
        canvas,
        ((bx - x0) * scale, (by - y0) * scale),
        ((bx - x0 + bw) * scale, (by - y0 + bh) * scale),
        ACCENT_BGR,
        max(1, scale // 4),
    )

    ok, buf = cv2.imencode(".png", canvas)
    if not ok:  # pragma: no cover - cv2 only fails here on a malformed array
        raise HTTPException(500, "could not encode crop")
    return {
        "png": "data:image/png;base64," + base64.b64encode(buf.tobytes()).decode(),
        "scale": scale,
    }


@app.get("/api/pages/{number}/grade")
def read_grade(number: int, doc: str | None = None) -> dict:
    """The last graded run of this page, in image pixels.

    Read from disk rather than recomputed. Grading a sheet costs a render, a segmentation and
    a full detection pass -- 17 s on T5, 53 s on T4 -- which is not something to do because
    somebody pressed a button, and it would make the viewer a second place where a run
    happens. `-m eval.suites --page N` is where a run happens. This shows what it found, and
    carries `run_at` so a stale report can be recognised as one.
    """
    source = _source(doc)
    path = suites.report_path(raster.source_hash(source), number)
    if not path.exists():
        return {
            "graded": False,
            "how": f"python -m eval.suites --page {number}"
                   + ("" if doc is None else f" --source documents/{doc}"),
        }

    r, _ = _candidates_for(number, doc=doc)
    run = json.loads(path.read_text(encoding="utf-8"))
    return {
        "graded": True,
        "live": False,
        "page": run.get("page"),
        "source": run.get("source"),
        "run_at": run.get("run_at"),
        "not_graded": run.get("not_graded", []),
        "classes": _grade_view(run, r),
    }


class EvaluatedDetection(BaseModel):
    """One result as the viewer holds it. Detection pixels, so nothing is converted twice."""

    class_id: str
    bbox_px: tuple[int, int, int, int]
    status: str
    match: float = 0.0
    reason: str | None = None
    variant: str = ""

    # What the reviewer decided: "kept", "dropped", or absent if they have not yet. The band
    # this came from stops mattering once somebody has looked at it -- an instance held for
    # confirmation and then confirmed is a find, not a half-find.
    verdict: str | None = None


class EvaluateRequest(BaseModel):
    detections: list[EvaluatedDetection] = []

    # The page's annotations AS THE VIEWER HOLDS THEM, which is not always what is on disk.
    # Accepting a match records the instance, so a review scored against the saved file would
    # count every instance the reviewer had just confirmed as missing -- the button would
    # punish somebody for using it and then not pressing Save. Falls back to the stored file
    # when the viewer sends nothing.
    truth: list[TruthInstanceIn] | None = None


@app.post("/api/pages/{number}/evaluate")
def evaluate(number: int, request: EvaluateRequest, doc: str | None = None) -> dict:
    """Score what is on screen against this page's recorded ground truth.

    The counting already happened -- the viewer is holding the results -- so this grades them
    rather than running the detector again. That is the difference between a button a person
    presses after counting and `-m eval.suites --page N`, which counts the whole sheet from
    scratch for every registered class and takes minutes.

    Only the classes actually counted are scored. Grading a class nobody asked for would
    report every one of its annotations as a miss, against a detector that was never run.
    """
    source = _source(doc)
    counted_classes = {d.class_id for d in request.detections}
    if not counted_classes:
        return {"graded": False, "live": True,
                "how": "count a symbol first, then evaluate what it found"}

    r, _ = _candidates_for(number, doc=doc)
    truth = _truth_for_evaluation(request, number, source, r)
    if truth is None or not truth.instances:
        return {"graded": False, "live": True,
                "how": "annotate this page and press Save truth first"}

    def as_detection(d: EvaluatedDetection) -> detect.Detection:
        return detect.Detection(
            id=f"{d.class_id}:{d.bbox_px}",
            class_id=d.class_id,
            bbox_px=tuple(d.bbox_px),
            # The harness measures from the middle of the box, never the ink centroid, so
            # this is the real centre and not a placeholder. See harness's WHICH CENTRE.
            centroid_px=(d.bbox_px[0] + d.bbox_px[2] / 2.0,
                         d.bbox_px[1] + d.bbox_px[3] / 2.0),
            match=d.match,
            margin=None,
            status=banding.Status(d.status),
            reason=d.reason,
            variant_label=d.variant,
            runner_up=None,
        )

    # The reviewer's verdicts ARE the answer here, not the bands. An unreviewed match counts
    # as neither: the viewer will not let Evaluate run with any left, and silently treating
    # one as accepted or rejected would invent a verdict nobody gave.
    accepted = [as_detection(d) for d in request.detections if d.verdict == "kept"]
    rejected = [as_detection(d) for d in request.detections if d.verdict == "dropped"]
    if not (accepted or rejected):
        # Scoring this would report every recorded instance as missed, against a person who
        # has not answered yet -- a zero that looks like a detector failure and is really an
        # empty question. The viewer will not send one; an API caller might.
        return {"graded": False, "live": True,
                "how": "accept or reject the matches first — this scores a review"}

    scores = {cid: harness.score_review(accepted, rejected, truth, cid)
              for cid in sorted(counted_classes)}
    skipped = [
        f"{cid}: annotated here but not counted in this run"
        for cid in sorted({t.class_id for t in truth.instances} - counted_classes)
    ]
    return {
        "graded": True,
        "live": True,
        "page": number,
        "source": source.name,
        # Stamped the same way `eval.suites` stamps a written report, so the viewer's "graded
        # N minutes ago" reads the same whichever produced it.
        "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "not_graded": skipped,
        "classes": {cid: _review_view(score, r) for cid, score in scores.items()},
    }


def _truth_for_evaluation(
    request: EvaluateRequest, number: int, source: Path, r: raster.Raster
):
    """What the review is scored against: the viewer's annotations, or the saved ones.

    The viewer's, when it sends them. Accepting a match records the instance in the page's
    ground truth immediately, and saving is a separate gesture -- so scoring against the file
    would count every instance somebody had just confirmed as one the tool missed.
    """
    if request.truth is None:
        return schema.load_truth(raster.source_hash(source), number)
    return schema.GroundTruth(
        document=raster.source_hash(source),
        page=number,
        dpi=r.dpi,
        instances=tuple(
            schema.TruthInstance(
                class_id=i.class_id,
                bbox_px=_to_detection_px(i.bbox_image_px, r),
                label=i.label,
                occluded=i.occluded,
            )
            for i in request.truth
        ),
    )


def _review_view(score: harness.ReviewScore, r: raster.Raster) -> dict:
    """One class's finished review, in the viewer's pixels.

    The six numbers a person asked for after counting, and the boxes behind them. Occlusion is
    reported as found-of-recorded and deliberately WITHOUT a false-positive count: a false
    positive sits on no instance, so there is no instance to say whether it was occluded.
    """
    return {
        "detected": len(score.detected),
        "present": score.present,
        "missed": len(score.missed),
        "occluded_detected": score.occluded_detected,
        "occluded_present": score.occluded_present,
        "false_positives": score.false_positives,
        "average_precision": score.average_precision,
        "recall": score.recall,
        "precision": score.precision,
        "boxes": [
            {"kind": "matched", "bbox_image_px": _to_image_px(m.detection.bbox_px, r),
             "truth_image_px": _to_image_px(m.truth.bbox_px, r), "match": m.detection.match,
             "occluded": m.truth.occluded, "label": m.truth.label,
             "distance_px": round(m.distance_px, 1)}
            for m in score.detected
        ] + [
            {"kind": "missed", "bbox_image_px": _to_image_px(t.bbox_px, r),
             "occluded": t.occluded, "label": t.label}
            for t in score.missed
        ] + [
            {"kind": "spurious", "bbox_image_px": _to_image_px(d.bbox_px, r),
             "match": d.match, "variant": d.variant_label, "reason": d.reason}
            for d in score.wrong
        ],
    }


def _grade_view(run: dict, r: raster.Raster) -> dict:
    """A report's boxes in image pixels, one flat list per class.

    Shared by the stored run and the live evaluation so the two cannot drift: the viewer
    draws off `kind` and never has to know which produced it.
    """
    graded = {}
    for class_id, row in run.get("classes", {}).items():
        # One flat list of boxes, each carrying what it is. The viewer draws off `kind` and
        # never has to know which key of the report a box came from.
        boxes = [
            {"kind": "matched", "bbox_image_px": _to_image_px(m["detection_px"], r),
             "truth_image_px": _to_image_px(m["truth_px"], r), "match": m["match"],
             "occluded": m["occluded"], "label": m["label"], "distance_px": m["distance_px"]}
            for m in row.get("matched", ())
        ]
        boxes += [
            {"kind": "missed", "bbox_image_px": _to_image_px(m["truth_px"], r),
             "occluded": m["occluded"], "label": m["label"]}
            for m in row.get("missed", ())
        ]
        boxes += [
            {"kind": "spurious", "bbox_image_px": _to_image_px(m["detection_px"], r),
             "match": m["match"], "variant": m["variant"]}
            for m in row.get("spurious", ())
        ]
        # A review hit that landed on a missed instance carries its truth box too, so the
        # reviewer sees what the tool found rather than only where it was looking.
        boxes += [
            {"kind": "recovered", "bbox_image_px": _to_image_px(m["detection_px"], r),
             "truth_image_px": _to_image_px(m["truth_px"], r), "match": m["match"],
             "occluded": m["occluded"], "reason": m["reason"],
             "distance_px": m["distance_px"]}
            for m in row.get("recovered", ())
        ]
        boxes += [
            {"kind": "review_spurious", "bbox_image_px": _to_image_px(m["detection_px"], r),
             "match": m["match"], "reason": m["reason"]}
            for m in row.get("review_spurious", ())
        ]
        # The same summary a live review reports, so the panel has one renderer rather than
        # two that drift. A stored run has no verdicts -- nobody has looked -- so the counted
        # band stands in for "accepted", which is exactly what `score_class` already grades.
        # The review split survives as its own line beside it; collapsing it here would throw
        # away the one number that makes occlusion work visible.
        # `missed` in a stored report is `not_found` -- the instances with NOTHING pointing at
        # them -- so an occluded instance recovered into review lives under `recovered` and has
        # to be counted from there, or the occluded denominator silently loses it. On T5 that
        # is both of the two occluded markers.
        occluded_present = sum(
            len([m for m in row.get(key, ()) if m.get("occluded")])
            for key in ("matched", "missed", "recovered")
        )
        ranked = sorted(
            [(m.get("match", 0.0), True) for m in row.get("matched", ())]
            + [(m.get("match", 0.0), False) for m in row.get("spurious", ())],
            key=lambda pair: -pair[0],
        )
        present = row.get("true_positives", 0) + row.get("false_negatives", 0)
        graded[class_id] = {
            **{k: v for k, v in row.items()
               if k not in ("matched", "missed", "spurious",
                            "recovered", "review_spurious")},
            "detected": row.get("true_positives", 0),
            "present": present,
            "missed": row.get("false_negatives", 0),
            "occluded_detected": len(
                [m for m in row.get("matched", ()) if m.get("occluded")]),
            "occluded_present": occluded_present,
            "average_precision": harness.average_precision(ranked, present),
            "boxes": boxes,
        }
    return graded


@app.get("/api/pages/{number}/truth")
def read_truth(number: int, doc: str | None = None) -> dict:
    """Reviewed annotations for this page, in image pixels.

    `annotated` distinguishes a page nobody has looked at from one confirmed to be empty.
    They are different answers: the first cannot be scored, the second scores a detector that
    reports anything at all.
    """
    source = _source(doc)
    r, _ = _candidates_for(number, doc=doc)
    truth = schema.load_truth(raster.source_hash(source), number)
    if truth is None:
        return {"annotated": False, "instances": []}

    return {
        "annotated": True,
        "document": truth.document,
        "reviewed_classes": list(truth.graded_classes),
        "instances": [
            {
                "class_id": i.class_id,
                "label": i.label,
                "occluded": i.occluded,
                "source": i.source,
                "bbox_image_px": _to_image_px(i.bbox_px, r),
            }
            for i in truth.instances
        ],
    }


@app.post("/api/pages/{number}/truth")
def write_truth(number: int, request: TruthRequest, doc: str | None = None) -> dict:
    """Store this page's annotations. The tool is the annotation tool; nothing is typed."""
    source = _source(doc)
    r, _ = _candidates_for(number, doc=doc)

    truth = schema.GroundTruth(
        document=raster.source_hash(source),
        page=number,
        dpi=r.dpi,
        reviewed_classes=tuple(sorted(set(request.reviewed_classes))),
        instances=tuple(
            schema.TruthInstance(
                class_id=i.class_id,
                bbox_px=_to_detection_px(i.bbox_image_px, r),
                label=i.label,
                occluded=i.occluded,
            )
            for i in request.instances
        ),
    )
    path = schema.save_truth(truth)
    return {
        "saved": str(path),
        "instances": len(truth.instances),
        "reviewed_classes": list(truth.graded_classes),
    }
