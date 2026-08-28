"""Line suppression, connected components, and snapping a rough drag to a symbol.

Raster.gray -> InkLayers -> list[Candidate], then a human's sloppy drag box -> Selection.
Line suppression is ported from scratch/spike6.py.

The user's box is the boundary. Everything substantially inside it is the symbol -- a symbol
is often several blobs (a marker's circle and the letter inside it never touch; a door's arc
and leaf are separate), and rather than trying to infer which blobs belong together, the tool
takes what was enclosed.

"Substantially inside" is measured on a blob's ink, not on its bounding box. The two agree
for solid glyphs and disagree for the sparse ones a drawing is full of -- triangles, arcs,
leaders -- where the bbox is mostly the empty space the shape does not fill.

The one thing removed is foreign text: blobs belonging to a line of text that continues past
the box edge. Three letters clipped off the end of a note are not part of the symbol; a label
like `X/TY` sitting complete above a marker is. "Does the run finish inside the box" is the
whole test, and it needs no notion of what a symbol looks like.

Raster-only module: must never import pymupdf, directly or transitively.
Enforced by tests/test_raster_only.py.
"""

from __future__ import annotations

import argparse
import hashlib
from dataclasses import dataclass
from typing import Callable, Sequence

import cv2
import numpy as np

from takeoff import spaces
from takeoff.schema import InkLayers, Raster

INK_THRESHOLD = 25          # on ink = 255 - gray, per scratch/spike6.py
STRUCTURE_LENGTH_IN = 0.30  # ~3x symbol size; a straight run this long is structure

# Line suppression is meant to remove context, not to dismember symbols -- but a wall or a
# grid line drawn across a glyph takes a slice of the glyph with it, and what is left is two
# or more pieces of a symbol that no longer looks like itself.
#
# The repair restores removed ink that sits in a SMALL GAP between two surviving pieces. It
# can only put back pixels that were ink to begin with, and only across a gap this wide, so
# it cannot invent a symbol or join two that the drawing kept apart. The width is set by what
# suppression actually takes: a 2-3 px line plus the 3x3 dilation around it.
#
# Measured on T5: repair takes the occluded A/T9 marker from unfindable to 0.904 and counted,
# rejoins the A/T10 marker into one component (it had needed group matching), lifts B/T12,
# and pushes the letter-A false positives DOWN. It is not free for every detector, though --
# see SymbolClass.repair_gap_px, which lets a class opt out.
REPAIR_GAP_PX = 10

# A generic band, not a per-class one. Per-class size policy belongs to classes.py and
# arrives at Gate 4; pinning class sizes here would invert that ordering.
SYMBOL_BAND_IN = (0.027, 0.67)

INSIDE_FRACTION = 0.6       # how much of a blob's ink must be in the drag box to count as inside
TOUCH_TOLERANCE_PX = 2      # blobs this close to the largest one are part of the same object

# Text-line grouping: similar height, shared baseline, no more than a character width apart.
RUN_HEIGHT_RATIO = (0.45, 2.2)
RUN_VERTICAL_OVERLAP = 0.4
RUN_GAP_FACTOR = 1.0
RUN_MARGIN_FACTOR = 2.0     # how far past the box to look for a clipped word's continuation

# (x, y, w, h), matching cv2's connected-component stats.
BBox = tuple[int, int, int, int]


def ink_layers(
    raster: Raster,
    ink_threshold: int = INK_THRESHOLD,
    structure_length_in: float = STRUCTURE_LENGTH_IN,
    repair_gap_px: int | None = REPAIR_GAP_PX,
) -> InkLayers:
    """Split ink into structure and symbols. Ported from scratch/spike6.py.

    Structure is found by morphological opening with a long horizontal and a long vertical
    kernel: anything with a straight run of `structure_length_in` is a wall, grid line or
    border, not a symbol. The result is dilated slightly so a symbol sitting on a wall does
    not keep a sliver of that wall attached to it.
    """
    ink = (255 - raster.gray).astype(np.uint8)
    binary = ink > ink_threshold
    b8 = binary.astype(np.uint8)

    length = max(3, int(structure_length_in * raster.dpi))
    horizontal = cv2.morphologyEx(
        b8, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (length, 1))
    )
    vertical = cv2.morphologyEx(
        b8, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, length))
    )
    structure = cv2.dilate(horizontal | vertical, np.ones((3, 3), np.uint8)).astype(bool)
    symbols = binary & ~structure

    # None is how a class says "whatever the default is" -- accepted here so no caller has
    # to translate it, and `SymbolClass.repair_gap_px` can stay tri-state.
    gap = REPAIR_GAP_PX if repair_gap_px is None else repair_gap_px
    if gap > 0:
        # Close small gaps in the symbol layer, then keep only the closed pixels that were
        # real ink. A gap left by a line crossing a glyph gets its ink back; a gap the
        # drawing intended -- a marker and its label 14 px apart -- is wider than this and
        # stays open.
        kernel = np.ones((gap, gap), np.uint8)
        closed = cv2.morphologyEx(symbols.astype(np.uint8), cv2.MORPH_CLOSE, kernel).astype(bool)
        symbols = symbols | (closed & binary)

    return InkLayers(ink=ink, binary=binary, structure=structure, symbols=symbols)


@dataclass(frozen=True)
class Candidate:
    """One connected component of symbol ink."""

    id: str
    bbox_px: BBox
    centroid_px: tuple[float, float]
    mask: np.ndarray     # bool,  cropped to bbox -- this component's pixels
    patch: np.ndarray    # uint8, greyscale crop -- for lifecycle grey level later
    area_px: int

    # Which component of the RAW ink this came from, before line suppression ran. Two
    # candidates sharing it were one blob on the drawing, and suppression pulled them apart
    # by removing a line that crossed the glyph. That is the difference between a symbol in
    # pieces and a symbol beside a label, and it needs no distance threshold to tell them
    # apart -- the drawing already said which ink was joined.
    raw_id: int = 0

    @property
    def max_dim_px(self) -> int:
        return max(self.bbox_px[2], self.bbox_px[3])

    def size_in(self, dpi: float) -> tuple[float, float]:
        return (self.bbox_px[2] / dpi, self.bbox_px[3] / dpi)


def candidate_id(page_index: int, bbox: BBox, area: int) -> str:
    """Stable across re-runs of the same raster: position and size, nothing incidental."""
    key = f"{page_index}:{bbox[0]}:{bbox[1]}:{bbox[2]}:{bbox[3]}:{area}"
    return hashlib.blake2b(key.encode(), digest_size=6).hexdigest()


def find_candidates(
    raster: Raster,
    layers: InkLayers | None = None,
    size_band_in: tuple[float, float] = SYMBOL_BAND_IN,
) -> list[Candidate]:
    """Every symbol-sized connected component on the raster.

    The band is on the component's larger dimension, so a long thin fragment is judged by
    its length rather than by its area.
    """
    if layers is None:
        layers = ink_layers(raster)

    lo = max(1, int(round(size_band_in[0] * raster.dpi)))
    hi = int(round(size_band_in[1] * raster.dpi))

    count, labels, stats, centroids = cv2.connectedComponentsWithStats(
        layers.symbols.astype(np.uint8), 8
    )
    # The same labelling over the ink as drawn, so a component can say what it was part of.
    _, raw_labels = cv2.connectedComponents(layers.binary.astype(np.uint8), 8)

    out: list[Candidate] = []
    for i in range(1, count):
        x, y, w, h, area = (int(v) for v in stats[i])
        if not lo <= max(w, h) <= hi:
            continue
        out.append(
            Candidate(
                id=candidate_id(raster.page_index, (x, y, w, h), area),
                bbox_px=(x, y, w, h),
                centroid_px=(float(centroids[i][0]), float(centroids[i][1])),
                mask=labels[y : y + h, x : x + w] == i,
                patch=raster.gray[y : y + h, x : x + w].copy(),
                area_px=area,
                raw_id=int(
                    np.bincount(
                        raw_labels[y : y + h, x : x + w][labels[y : y + h, x : x + w] == i]
                    ).argmax()
                ),
            )
        )
    return out


# ------------------------------------------------------------------ snapping a rough drag


@dataclass(frozen=True)
class Selection:
    """What a drag resolved to: the component group, its bounds, and its composite ink."""

    members: tuple[Candidate, ...]
    bbox_px: BBox
    mask: np.ndarray      # bool, the union of member masks over bbox_px
    dpi: int

    @property
    def is_empty(self) -> bool:
        return not self.members

    @property
    def size_px(self) -> tuple[int, int]:
        return (self.bbox_px[2], self.bbox_px[3])

    @property
    def size_in(self) -> tuple[float, float]:
        return (self.bbox_px[2] / self.dpi, self.bbox_px[3] / self.dpi)

    @property
    def area_px(self) -> int:
        return int(self.mask.sum())


def _intersection_area(a: BBox, b: BBox) -> int:
    ax0, ay0, ax1, ay1 = a[0], a[1], a[0] + a[2], a[1] + a[3]
    bx0, by0, bx1, by1 = b[0], b[1], b[0] + b[2], b[1] + b[3]
    w = min(ax1, bx1) - max(ax0, bx0)
    h = min(ay1, by1) - max(ay0, by0)
    return w * h if w > 0 and h > 0 else 0


def _inside_fraction(candidate: Candidate, drag: BBox) -> float:
    """How much of a blob's INK the drag box encloses.

    Measured on ink rather than on bounding-box area, because for the sparse shapes on a
    drawing the two disagree badly. A hatched elevation marker beside a door on T5 is a
    left-pointing triangle: its bbox is mostly the empty wedge above and below the apex, so
    a box that clips only the tip encloses 72% of the bbox but 58% of the ink, and the whole
    marker -- hatching and all, 40 px outside the box -- was being taken into the door's
    template. The same gap shows on arcs, leaders and any diagonal.
    """
    total = int(candidate.mask.sum())
    if total <= 0:
        return 0.0

    x, y, w, h = candidate.bbox_px
    ix0, iy0 = max(x, drag[0]), max(y, drag[1])
    ix1, iy1 = min(x + w, drag[0] + drag[2]), min(y + h, drag[1] + drag[3])
    if ix1 <= ix0 or iy1 <= iy0:
        return 0.0
    return int(candidate.mask[iy0 - y : iy1 - y, ix0 - x : ix1 - x].sum()) / total


def _grown(box: BBox, by: int) -> BBox:
    return (box[0] - by, box[1] - by, box[2] + 2 * by, box[3] + 2 * by)


Rule = Callable[[Sequence[Candidate], BBox], list[Candidate]]


def _same_text_run(a: Candidate, b: Candidate) -> bool:
    """Do these two blobs read as neighbouring characters on one line of text?

    Similar height, sharing a baseline, separated by no more than a character width. That is
    enough to chain a word or a sentence together, and not enough to chain a symbol's
    geometry to the text beside it -- a marker circle is several times a letter's height, so
    the height ratio rules it out before position is even considered.
    """
    ax, ay, aw, ah = a.bbox_px
    bx, by, bw, bh = b.bbox_px
    if not ah or not bh:
        return False
    ratio = ah / bh
    if not RUN_HEIGHT_RATIO[0] <= ratio <= RUN_HEIGHT_RATIO[1]:
        return False
    overlap = min(ay + ah, by + bh) - max(ay, by)
    if overlap < RUN_VERTICAL_OVERLAP * min(ah, bh):
        return False
    gap = max(ax, bx) - min(ax + aw, bx + bw)
    return gap <= RUN_GAP_FACTOR * max(ah, bh)


def text_runs(candidates: Sequence[Candidate]) -> list[list[Candidate]]:
    """Group blobs into lines of text by simple adjacency. Union-find over the neighbourhood."""
    parent = list(range(len(candidates)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(candidates)):
        for j in range(i + 1, len(candidates)):
            if _same_text_run(candidates[i], candidates[j]):
                parent[find(i)] = find(j)

    groups: dict[int, list[Candidate]] = {}
    for i, c in enumerate(candidates):
        groups.setdefault(find(i), []).append(c)
    return list(groups.values())


def union_inside(
    candidates: Sequence[Candidate], drag: BBox, inside: float = INSIDE_FRACTION
) -> list[Candidate]:
    """Everything substantially inside the drag box, unfiltered."""
    return [c for c in candidates if _inside_fraction(c, drag) >= inside]


def inside_minus_foreign_text(
    candidates: Sequence[Candidate],
    drag: BBox,
    inside: float = INSIDE_FRACTION,
) -> list[Candidate]:
    """Default rule. The user's box is the boundary; only foreign text is removed.

    Everything substantially inside the box is the symbol. The one exception is a blob that
    belongs to a line of text continuing past the box edge -- three letters clipped off the
    end of a note are not part of the symbol, while a label like `X/TY` sitting complete
    above a marker is. Whether the run is finished inside the box is the whole test, and it
    needs no notion of what a symbol looks like.

    The largest blob in the box is never removed. Without that, a receptacle glyph that
    happens to sit at text height beside a clipped note would be deleted along with it, and
    the result would be quietly wrong rather than visibly wrong.
    """
    members = union_inside(candidates, drag, inside)
    if not members:
        return []

    inside_ids = {c.id for c in members}

    # What a person pointed at is either the densest thing in the box or the biggest, and
    # for a thin curve those are different blobs. A door's arc carries 210 ink px while the
    # `EX` beside it carries 254, so ranking by ink alone makes the letter the primary, the
    # arc loses its protection, and the text filter deletes the symbol and keeps the label.
    # Protecting both readings costs nothing when they agree, which is most of the time.
    densest = max(members, key=lambda c: c.area_px)
    largest = max(members, key=lambda c: c.bbox_px[2] * c.bbox_px[3])

    protected = {densest.id, largest.id}
    for anchor in (densest, largest):
        protected |= {
            c.id
            for c in members
            if _intersection_area(c.bbox_px, _grown(anchor.bbox_px, TOUCH_TOLERANCE_PX)) > 0
        }
    primary = largest

    # Look just far enough beyond the box to find a clipped word's continuation.
    heights = sorted(c.bbox_px[3] for c in members)
    margin = int(max(12, RUN_MARGIN_FACTOR * heights[len(heights) // 2]))
    nearby = [
        c for c in candidates if _intersection_area(c.bbox_px, _grown(drag, margin)) > 0
    ]

    foreign: set[str] = set()
    for run in text_runs(nearby):
        if any(c.id not in inside_ids for c in run):
            foreign.update(c.id for c in run)

    return [c for c in members if c.id in protected or c.id not in foreign]


def snap(
    candidates: Sequence[Candidate],
    drag_bbox_px: BBox,
    dpi: int,
    rule: Rule = inside_minus_foreign_text,
) -> Selection:
    """Resolve a drag box to the symbol inside it.

    The returned bbox is the tight bounds of the ink that was kept, not the drag box -- the
    user declares the boundary, but a template needs its whitespace trimmed for the size
    measurement to mean anything.
    """
    members = rule(candidates, drag_bbox_px)
    if not members:
        return Selection(members=(), bbox_px=drag_bbox_px, mask=np.zeros((0, 0), bool), dpi=dpi)

    x0 = min(c.bbox_px[0] for c in members)
    y0 = min(c.bbox_px[1] for c in members)
    x1 = max(c.bbox_px[0] + c.bbox_px[2] for c in members)
    y1 = max(c.bbox_px[1] + c.bbox_px[3] for c in members)

    mask = np.zeros((y1 - y0, x1 - x0), bool)
    for c in members:
        cx, cy, cw, ch = c.bbox_px
        mask[cy - y0 : cy - y0 + ch, cx - x0 : cx - x0 + cw] |= c.mask

    ordered = tuple(sorted(members, key=lambda c: -c.area_px))
    return Selection(members=ordered, bbox_px=(x0, y0, x1 - x0, y1 - y0), mask=mask, dpi=dpi)


def _cli() -> None:
    from takeoff.raster import DETECTION_DPI, render  # local: raster.py imports pymupdf

    ap = argparse.ArgumentParser(description="Ink layers and candidate components for a sheet.")
    ap.add_argument("--pdf", default="Skanksa.pdf")
    ap.add_argument("--page", type=int, required=True, help="1-based sheet number")
    ap.add_argument("--dpi", type=int, default=DETECTION_DPI)
    args = ap.parse_args()

    raster = render(args.pdf, args.page - 1, dpi=args.dpi)
    layers = ink_layers(raster)
    found = find_candidates(raster, layers)

    w, h = raster.size_px
    print(f"  page {args.page}  {w} x {h} px @ {raster.dpi} DPI")
    print(
        f"  ink {int(layers.binary.sum()):,} px -> symbols {int(layers.symbols.sum()):,} "
        f"({layers.removed_fraction * 100:.0f}% removed as structure)"
    )
    print(f"  candidates in {SYMBOL_BAND_IN[0]}-{SYMBOL_BAND_IN[1]} in band: {len(found):,}")
    dims = sorted(c.max_dim_px for c in found)
    if dims:
        for label, value in (("min", dims[0]), ("median", dims[len(dims) // 2]), ("max", dims[-1])):
            print(f"    {label:6s} max-dim {value:4d} px = {value / raster.dpi:.3f} in")


if __name__ == "__main__":
    _cli()
