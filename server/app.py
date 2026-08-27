"""FastAPI app: serves the viewer, DZI tiles, and (later) the detection API.

A thin adapter over takeoff/. Any logic that belongs to detection belongs in takeoff/.

Page numbers are 1-based everywhere in the HTTP API, matching the CLI and the sheet numbers
a person reads off the drawing set. The 0-based page index stays inside takeoff/.
"""

from __future__ import annotations

import base64
import hashlib
import re
import threading
from pathlib import Path

import cv2
import numpy as np
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from takeoff import candidates as cand
from takeoff import classes, detect, doors, layout, raster, spaces

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

_words: dict[tuple[str, int], list[layout.Word]] = {}
_words_lock = threading.Lock()

# One reference entry per registered class, built from ITS OWN anchor page. Identification
# needs these to work on any sheet but the anchor's, and takeoff/ cannot render a second page
# -- that is this layer's job. Held per process; a class's reference never changes.
_library: dict[str, object] = {}
_library_lock = threading.Lock()

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


def _candidates_for(
    number: int, repair_gap_px: int | None = None, doc: str | None = None
) -> tuple[raster.Raster, list[cand.Candidate]]:
    """Raster and candidate list for a page, built once per (page, repair) per process.

    Repair is keyed in because it changes the segmentation, and different detectors want
    different segmentations: a matched glyph wants the pieces a wall broke apart put back,
    a swept arc wants to stay a thin curve. See SymbolClass.repair_gap_px.
    """
    source = _source(doc)
    index = _index(number, doc)
    gap = cand.REPAIR_GAP_PX if repair_gap_px is None else repair_gap_px
    key = (str(source), number, gap)
    with _candidates_lock:
        hit = _candidates.get(key)
    if hit is not None:
        return hit  # type: ignore[return-value]

    r = raster.render(source, index, dpi=_declared_dpi(source))
    found = cand.find_candidates(r, cand.ink_layers(r, repair_gap_px=gap))
    with _candidates_lock:
        _candidates[key] = (r, found)
    return r, found


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


def _class_library() -> dict:
    """Reference entries for every registered class, each from its own anchor page."""
    with _library_lock:
        if _library:
            return dict(_library)

    built: dict[str, object] = {}
    for symbol in classes.all_classes():
        index = symbol.anchor.page_index
        try:
            ref_source = Path(symbol.anchor.source)
            ref = raster.render(ref_source, index, dpi=_declared_dpi(ref_source))
            found = cand.find_candidates(
                ref, cand.ink_layers(ref, repair_gap_px=(
                    cand.REPAIR_GAP_PX if symbol.repair_gap_px is None
                    else symbol.repair_gap_px))
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
    }


@app.post("/api/pages/{number}/select")
def select(number: int, drag: DragBox, doc: str | None = None) -> dict:
    """Resolve a rough drag box to the component group that is the symbol."""
    r, found = _candidates_for(number, doc=doc)
    drag_px = _to_detection_px(drag.bbox_image_px, r)
    selection = cand.snap(found, drag_px, dpi=r.dpi)

    if selection.is_empty:
        return {"found": False, "reason": "no symbol-sized ink inside that box"}

    width_in, height_in = selection.size_in
    return {
        "found": True,
        "bbox_image_px": _to_image_px(selection.bbox_px, r),
        "component_count": len(selection.members),
        "size_px": list(selection.size_px),
        "size_in": [round(width_in, 4), round(height_in, 4)],
        "ink_px": selection.area_px,
        "preview_png": _preview_png(selection, r),
    }


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


@app.get("/api/classes")
def symbol_classes() -> dict:
    """The registry. Adding a symbol is an entry in takeoff/classes.py, not a route."""
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
        selection = cand.snap(found, _to_detection_px(request.bbox_image_px, r), dpi=r.dpi)
        if selection.is_empty:
            return {"found": False, "reason": "no symbol-sized ink in that box"}
        if symbol is None:
            symbol, identified_as = detect.identify(
                selection, r, found, references=_class_library()
            )
        # Once the class is known, redo the selection on the segmentation THAT class wants.
        # The identification above ran on the default one, which is fine -- it only had to
        # recognise the symbol -- but the count has to be made on the ink the class's
        # detector is calibrated against.
        if symbol.repair_gap_px is not None:
            r, found = _candidates_for(number, symbol.repair_gap_px, doc=doc)
            selection = cand.snap(found, _to_detection_px(request.bbox_image_px, r), dpi=r.dpi)
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
        entry = detect.build_entry(symbol, r, found)
        source = "anchor"

    detections = detect.detect(r, found, [entry], keep_rejected=request.keep_rejected)

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
        "diagnostics": detect.diagnose(r, found, entry, detections),
        "counts": detect.summarise(detections),
        "detections": [_detection_payload(d, r, _words_for(number, r, doc)) for d in detections],
    }


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
