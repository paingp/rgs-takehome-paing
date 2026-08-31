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
from dataclasses import dataclass, replace
from typing import Callable, Sequence

import cv2
import numpy as np

from takeoff import spaces
from takeoff.schema import InkLayers, Raster

# On ink = 255 - gray, from spike6.py, and measured on architectural sheets where linework is
# nearly black: the T5 elevation marker's darkest pixel is 0 and its median 142, so it is
# caught whatever the threshold.
#
# Electrical sheets are not drawn that way. The duplex receptacle on E4 has a darkest pixel of
# 202 and a MEDIAN OF 232, against a cut at gray < 230, so this catches 76 of its ~279 pixels
# and leaves the glyph in nine fragments of 11-18 px. Nothing downstream recovers from that:
# a template built from one fragment matched 2,312 things on the sheet.
#
# It is tempting to just lower it -- ink coverage barely moves on T4 and T5, and E4's
# candidate count FALLS as fragments rejoin. That reading is wrong, and the harness caught it:
# at 15 the extra faint ink merges neighbouring components, and T5 loses two doors and a
# marker (31 -> 29 and 10 -> 9). Candidate counts are not a proxy for accuracy.
#
# So it stays where it is proven and a class that is drawn lightly opts down instead, the way
# a class that is swept opts out of repair -- see SymbolClass.ink_threshold. Lineweight is a
# property of the symbol's CAD layer, which makes it a class's business rather than a sheet's.
#
# A scan will need better than any fixed number. Otsu is not it as things stand: over
# non-paper pixels it picks 142-172 here, which would erase the light linework entirely.
INK_THRESHOLD = 25
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

# How many characters make a line. Below this there is nothing to distinguish a letter from a
# dash, a tick or any other small piece of a symbol -- the demolition door is nine dashes that
# chain with nothing, and every one of them is the symbol.
MIN_RUN_MEMBERS = 2

# How much smaller than the symbol every character of a run must be before the run is read as
# a caption rather than as part of the symbol. Captions are set small beside a glyph they
# annotate: the `C/T9` beside the T5 marker runs 65-179 ink px against the triangle's 1,331,
# and the `FILM` printed under it 72-262. A supply diffuser's corner brackets are 157-183
# against a 275 px primary -- 1.5x, nothing like a caption -- so they stay.
#
# This only ever applies to pieces that ALREADY chain into a line of characters, which is what
# keeps it away from the demolition door: its dashes are 37x smaller than its leaf and chain
# with nothing, so no size rule is ever consulted for them.
RUN_SIZE_RATIO = 3.0

# (x, y, w, h), matching cv2's connected-component stats.
BBox = tuple[int, int, int, int]


def ink_layers(
    raster: Raster,
    ink_threshold: int | None = INK_THRESHOLD,
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
    # None means "whatever the default is", matching repair_gap_px below. Both come straight
    # off a SymbolClass, where None is how a class says it has no opinion, and a caller that
    # had to translate one but not the other is a trap -- it raises on the tri-state field
    # that happens to be checked second.
    binary = ink > (INK_THRESHOLD if ink_threshold is None else ink_threshold)
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


def host_blobs(
    raster: Raster,
    layers: InkLayers | None = None,
    size_band_in: tuple[float, float] = SYMBOL_BAND_IN,
    max_ink_px: int | None = None,
) -> list[Candidate]:
    """Components too BIG to be a symbol, which is where fused instances hide.

    `find_candidates` keeps a size band, and everything above it is dropped before anything
    downstream can see it. That is right for counting -- a wall network is not an instance of
    anything -- but it is exactly wrong for occlusion: a symbol drawn touching a wall belongs
    to the wall's component, so the thing that has to be searched is the thing the band
    throws away.

    Measured on E4: 25 of 36 missed duplex receptacles are joined to surrounding geometry,
    and their host components run 108-421 px on the larger side against a band that stops at
    201. The fused search had been filtering a pool those hosts were never in, which is why
    raising its own cap recovered only 4 of them.

    `max_ink_px` bounds the cost and is read from `stats` before any mask is materialised: a
    sheet's full wall network is one component of hundreds of thousands of pixels, and
    building a Candidate for it would cost more memory than the whole rest of the pass.
    """
    if layers is None:
        layers = ink_layers(raster)

    hi = int(round(size_band_in[1] * raster.dpi))

    count, labels, stats, centroids = cv2.connectedComponentsWithStats(
        layers.symbols.astype(np.uint8), 8
    )
    _, raw_labels = cv2.connectedComponents(layers.binary.astype(np.uint8), 8)

    out: list[Candidate] = []
    for i in range(1, count):
        x, y, w, h, area = (int(v) for v in stats[i])
        if max(w, h) <= hi:
            continue
        if max_ink_px is not None and area > max_ink_px:
            continue
        mask = labels[y : y + h, x : x + w] == i
        out.append(
            Candidate(
                id=candidate_id(raster.page_index, (x, y, w, h), area),
                bbox_px=(x, y, w, h),
                centroid_px=(float(centroids[i][0]), float(centroids[i][1])),
                mask=mask,
                patch=raster.gray[y : y + h, x : x + w].copy(),
                area_px=area,
                raw_id=int(
                    np.bincount(raw_labels[y : y + h, x : x + w][mask]).argmax()
                ),
            )
        )
    return out


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

    # Pieces the box enclosed that the rule judged not to be the symbol -- today, a line of
    # characters that does not include it. They are NOT in `mask` and not in the template.
    # They travel so the viewer can draw them and a person can overrule the rule: a marker
    # dragged together with its `C/T9` reference should show the label greyed out and let it
    # be switched back on, rather than deleting it where nobody can see what happened.
    set_aside: tuple[Candidate, ...] = ()

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

    @property
    def parts_px(self) -> tuple[BBox, ...]:
        """Each piece the drag enclosed, largest ink first -- what the template will hold.

        The order is what `without` indexes into and what the viewer numbers its outlines by,
        so it has to be stable: `snap` sorts by ink descending and this reads that order back.
        """
        return tuple(c.bbox_px for c in self.members)

    @property
    def set_aside_px(self) -> tuple[BBox, ...]:
        """Each piece the rule removed, largest ink first. Drawn, not counted."""
        return tuple(c.bbox_px for c in self.set_aside)

    def plus(self, add: Sequence[int]) -> "Selection":
        """The same drag, with these set-aside pieces treated as the symbol after all.

        The inverse of `without`, and the reason `set_aside` is carried at all. Indices are
        into `set_aside_px`.
        """
        add = set(add)
        extra = [c for i, c in enumerate(self.set_aside) if i in add]
        if not extra:
            return self
        return _selection_of(
            [*self.members, *extra],
            self.bbox_px,
            self.dpi,
            set_aside=[c for i, c in enumerate(self.set_aside) if i not in add],
        )

    def without(self, drop: Sequence[int]) -> "Selection":
        """The same drag, minus the pieces at these indices.

        The drag says where to look and this says which of what was found is the symbol. It
        exists because no measurable rule separates a symbol's own parts from an annotation
        lying beside it: a diffuser's four quadrants, a demolition door's nine dashes and a
        marker's `C/T9` label are indistinguishable by joined ink, by relative size, and by
        text-run grouping -- all three were tried. So the tool keeps everything the box held,
        shows it, and a person removes what does not belong.
        """
        drop = set(drop)
        kept = [c for i, c in enumerate(self.members) if i not in drop]
        if not kept:
            return self
        # What was dropped joins the set-aside pile rather than disappearing: the viewer keeps
        # drawing it, so a click can be taken back.
        removed = [c for i, c in enumerate(self.members) if i in drop]
        return _selection_of(kept, self.bbox_px, self.dpi,
                             set_aside=[*self.set_aside, *removed])


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


def _runs_along(a: BBox, b: BBox, axis: int) -> bool:
    """Do these two boxes read as neighbouring characters set along one axis?

    `axis` 0 is a line of text running left to right, 1 is one running down the sheet. The
    test is the same either way with the roles of the two dimensions swapped: characters are
    a similar size ACROSS the line, they share the line's centre, and they sit no more than a
    character apart ALONG it.
    """
    across = 1 - axis
    size_a, size_b = a[2 + across], b[2 + across]
    if not size_a or not size_b:
        return False
    ratio = size_a / size_b
    if not RUN_HEIGHT_RATIO[0] <= ratio <= RUN_HEIGHT_RATIO[1]:
        return False

    lo = max(a[across], b[across])
    hi = min(a[across] + size_a, b[across] + size_b)
    if hi - lo < RUN_VERTICAL_OVERLAP * min(size_a, size_b):
        return False

    gap = max(a[axis], b[axis]) - min(a[axis] + a[2 + axis], b[axis] + b[2 + axis])
    return gap <= RUN_GAP_FACTOR * max(size_a, size_b)


def _ink_touches(a: Candidate, b: Candidate, tol: int = TOUCH_TOLERANCE_PX) -> bool:
    """Is a's ink within `tol` pixels of b's ink?

    On the INK, not the bounding boxes. For the sparse shapes a drawing is made of the two
    disagree badly, and the disagreement is not academic: the T5 elevation marker is a
    left-pointing hatched triangle whose box is mostly the empty wedge beneath it, and the
    word `FILM` printed in that wedge touches the box while being nowhere near the glyph.
    Protecting those letters from the text filter on that basis put four of them in the
    template and took the marker's count to zero.
    """
    ax, ay, aw, ah = a.bbox_px
    bx, by, bw, bh = b.bbox_px

    pad = np.zeros((ah + 2 * tol, aw + 2 * tol), np.uint8)
    pad[tol : tol + ah, tol : tol + aw] = a.mask
    grown = cv2.dilate(pad, np.ones((2 * tol + 1, 2 * tol + 1), np.uint8)).astype(bool)
    gx, gy = ax - tol, ay - tol

    x0, y0 = max(gx, bx), max(gy, by)
    x1 = min(gx + grown.shape[1], bx + bw)
    y1 = min(gy + grown.shape[0], by + bh)
    if x1 <= x0 or y1 <= y0:
        return False

    return bool((grown[y0 - gy : y1 - gy, x0 - gx : x1 - gx]
                 & b.mask[y0 - by : y1 - by, x0 - bx : x1 - bx]).any())


def _same_text_run(a: Candidate, b: Candidate) -> bool:
    """Do these two blobs read as neighbouring characters on one line of text?

    Similar size across the line, sharing its centre, separated by no more than a character.
    That is enough to chain a word or a sentence together, and not enough to chain a symbol's
    geometry to the text beside it -- a marker circle is several times a letter's height, so
    the size ratio rules it out before position is even considered.

    BOTH ORIENTATIONS. This used to test a horizontal line only, which on a rotated sheet is
    most of the labels: the `C/T9` beside the T5 marker is five characters stacked DOWN the
    page at a constant x, and not one adjacent pair chained. The rule that removes a note the
    drag clipped could therefore never see a vertical note, which is the majority of them
    here -- the sheets are 36x24 landscape drawn on a rotated page.
    """
    return _runs_along(a.bbox_px, b.bbox_px, 0) or _runs_along(a.bbox_px, b.bbox_px, 1)


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
    """Default rule. The box is the boundary; a line of text that is not the symbol is not.

    Everything substantially inside the box is the symbol, except characters that chain into
    a LINE and that line does not include the symbol itself. Three letters clipped off a note
    are not part of a door; neither is a `C/T9` sheet reference that happens to sit fully
    inside a generous drag around the marker it labels.

    It used to be narrower -- only a run that continued past the box edge was removed -- and
    that is why a label enclosed by a sloppy drag became part of the template, matching only
    the one instance carrying that exact label: 10 markers counted became 1. Whether the run
    finishes inside the box turns out to say nothing about whether it is the symbol.

    Two things keep this from eating geometry. A run must be at least two characters, so the
    demolition door's nine separate dashes -- which chain with nothing -- all survive. And a
    run that includes the symbol is kept whole, which is what saves the supply diffuser: its
    four corner brackets are similar boxes in a row and do read as a "line", but the line
    contains the largest piece, so it is the symbol rather than a caption beside it.

    The largest and densest blobs are never removed. Without that, a receptacle glyph that
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

    # Touching the symbol means touching its INK. Bounding boxes protected whatever happened
    # to sit in a sparse glyph's empty space -- see `_ink_touches`.
    protected = {densest.id, largest.id}
    for anchor in (densest, largest):
        protected |= {c.id for c in members if _ink_touches(c, anchor)}
    primary = largest

    # Look just far enough beyond the box to find a clipped word's continuation.
    heights = sorted(c.bbox_px[3] for c in members)
    margin = int(max(12, RUN_MARGIN_FACTOR * heights[len(heights) // 2]))
    nearby = [
        c for c in candidates if _intersection_area(c.bbox_px, _grown(drag, margin)) > 0
    ]

    foreign: set[str] = set()
    for run in text_runs(nearby):
        if len(run) < MIN_RUN_MEMBERS:
            continue                       # one blob on its own is not a line of text
        caption = [c for c in run if c.id != primary.id]
        if len(caption) < MIN_RUN_MEMBERS:
            continue
        # A glyph can chain into a line of text beside it -- the T5 marker is 44 px tall and
        # the letters under it 24, which passes any height ratio loose enough for real text --
        # so the symbol is set aside and the rest judged on its own. Characters are small
        # beside the thing they annotate; a symbol's own parts are not.
        if any(c.area_px * RUN_SIZE_RATIO > primary.area_px for c in caption):
            continue
        if any(_ink_touches(c, primary) for c in caption):
            continue                       # joined to the symbol, so part of it
        foreign.update(c.id for c in caption)

    return [c for c in members if c.id in protected or c.id not in foreign]


def clipped_to(candidate: Candidate, drag: BBox) -> Candidate | None:
    """The part of a component that lies inside the drag box, or None if none of it does.

    THE BOX IS A CEILING, NOT A HINT. A component is kept when most of its ink is inside, and
    it used to be kept WHOLE -- so a supply diffuser with a duct line curling off one corner
    came back bigger than the box drawn round it, and no amount of care with the mouse could
    exclude the curl. Measured on M2: a tight 82x106 drag returned a 120x123 selection.

    The alternative was to refuse components that stick out, which loses the symbol instead of
    the curl. Cutting at the boundary keeps what was asked for and nothing else, and a person
    drawing a tight box is telling the tool exactly where the symbol ends.

    Occlusion still means foreign ink can lie INSIDE the box. That is what the part list is
    for -- this rule handles the edge, not the interior.
    """
    dx, dy, dw, dh = (int(v) for v in drag)
    cx, cy, cw, ch = candidate.bbox_px

    x0, y0 = max(cx, dx), max(cy, dy)
    x1, y1 = min(cx + cw, dx + dw), min(cy + ch, dy + dh)
    if x1 <= x0 or y1 <= y0:
        return None
    if (x0, y0, x1, y1) == (cx, cy, cx + cw, cy + ch):
        return candidate                                   # wholly inside: nothing to do

    mask = candidate.mask[y0 - cy : y1 - cy, x0 - cx : x1 - cx]
    ys, xs = np.nonzero(mask)
    if not len(xs):
        return None

    # Re-tighten: clipping can leave blank rows where the component's ink was all outside.
    tx0, tx1 = int(xs.min()), int(xs.max()) + 1
    ty0, ty1 = int(ys.min()), int(ys.max()) + 1
    mask = mask[ty0:ty1, tx0:tx1]
    bbox = (x0 + tx0, y0 + ty0, tx1 - tx0, ty1 - ty0)

    ys, xs = np.nonzero(mask)
    return replace(
        candidate,
        bbox_px=bbox,
        mask=mask,
        patch=candidate.patch[
            y0 - cy + ty0 : y0 - cy + ty1, x0 - cx + tx0 : x0 - cx + tx1
        ].copy(),
        area_px=int(mask.sum()),
        centroid_px=(bbox[0] + float(xs.mean()), bbox[1] + float(ys.mean())),
    )


def fine_candidates(
    raster: Raster,
    drag: BBox,
    layers: InkLayers | None = None,
    size_band_in: tuple[float, float] = SYMBOL_BAND_IN,
    margin_px: int = 4,
) -> list[Candidate]:
    """Ink inside a drag that is too SMALL to be a candidate anywhere else.

    `find_candidates` drops anything under 0.027 in -- 8 px at 300 DPI -- because a sheet
    holds millions of specks and a pool of those is useless. That floor is about the SHEET,
    though, and inside a box a person has drawn there is no such problem: they have already
    said where the symbol is.

    It cost a real symbol. E4's duplex receptacle is drawn on a thin CAD layer, so at the
    global ink threshold it is not one glyph but NINE fragments -- and five of them are under
    8 px. A drag around it returned a 31x31 selection of four pieces where the glyph is 47x31,
    with the two bars simply absent: not set aside, not clickable, gone. The count was right
    anyway, because identifying the class re-snaps on that class's own ink threshold where the
    glyph is whole, but what a person saw was the tool ignoring half of their box.

    Only the sub-band pieces are returned; everything at or above the floor is already in the
    normal pool. Labelling is confined to a window around the drag, which is what keeps this
    cheap.
    """
    if layers is None:
        layers = ink_layers(raster)

    lo = max(1, int(round(size_band_in[0] * raster.dpi)))
    dx, dy, dw, dh = (int(v) for v in drag)
    x0 = max(0, dx - margin_px)
    y0 = max(0, dy - margin_px)
    x1 = min(layers.symbols.shape[1], dx + dw + margin_px)
    y1 = min(layers.symbols.shape[0], dy + dh + margin_px)
    if x1 <= x0 or y1 <= y0:
        return []

    window = layers.symbols[y0:y1, x0:x1]
    count, labels, stats, centroids = cv2.connectedComponentsWithStats(
        window.astype(np.uint8), 8
    )
    raw = layers.binary[y0:y1, x0:x1]
    _, raw_labels = cv2.connectedComponents(raw.astype(np.uint8), 8)

    out: list[Candidate] = []
    for i in range(1, count):
        x, y, w, h, area = (int(v) for v in stats[i])
        if max(w, h) >= lo:
            continue                       # already a candidate in the normal pool
        mask = labels[y : y + h, x : x + w] == i
        out.append(
            Candidate(
                id=candidate_id(raster.page_index, (x0 + x, y0 + y, w, h), area),
                bbox_px=(x0 + x, y0 + y, w, h),
                centroid_px=(x0 + float(centroids[i][0]), y0 + float(centroids[i][1])),
                mask=mask,
                patch=raster.gray[y0 + y : y0 + y + h, x0 + x : x0 + x + w].copy(),
                area_px=area,
                raw_id=int(np.bincount(raw_labels[y : y + h, x : x + w][mask]).argmax()),
            )
        )
    return out


def snap(
    candidates: Sequence[Candidate],
    drag_bbox_px: BBox,
    dpi: int,
    rule: Rule = inside_minus_foreign_text,
    fine: Sequence[Candidate] = (),
) -> Selection:
    """Resolve a drag box to the symbol inside it.

    The result never extends past the drag. Pieces are cut at the boundary rather than kept
    whole -- see `clipped_to` -- so the returned bbox is the tight bounds of the ink inside
    the box, which is at most the box itself.

    Pieces the rule set aside travel with the selection instead of vanishing. A `C/T9` label
    caught by a generous drag is not the symbol and is dropped by default, but a person who
    meant to include it has to be able to see it and say so; silently deleting ink the box
    enclosed leaves them with nothing to click. See `Selection.set_aside`.

    `fine` is ink too small to be a candidate on the sheet but inside the box all the same --
    see `fine_candidates`. It always arrives SET ASIDE, never as a member. Sub-band ink is
    mostly the frayed edge of a stroke, and folding it into the template silently changes
    every registered class: on T5 it cost the elevation marker a count. What it must not do
    is stay invisible, because sometimes it is the symbol -- so it is offered, and a person
    decides.
    """
    members = rule(candidates, drag_bbox_px)
    kept_ids = {c.id for c in members}
    dropped = [
        c for c in union_inside(candidates, drag_bbox_px) if c.id not in kept_ids
    ]
    dropped += [c for c in fine if _inside_fraction(c, drag_bbox_px) >= INSIDE_FRACTION]

    clip = [clipped_to(c, drag_bbox_px) for c in members]
    aside = [clipped_to(c, drag_bbox_px) for c in dropped]
    return _selection_of(
        [c for c in clip if c is not None],
        drag_bbox_px,
        dpi,
        set_aside=[c for c in aside if c is not None],
    )


def _selection_of(
    members: Sequence[Candidate],
    fallback: BBox,
    dpi: int,
    set_aside: Sequence[Candidate] = (),
) -> Selection:
    """Bounds and composite ink for a set of pieces, largest ink first."""
    aside = tuple(sorted(set_aside, key=lambda c: -c.area_px))
    if not members:
        return Selection(members=(), bbox_px=fallback, mask=np.zeros((0, 0), bool), dpi=dpi,
                         set_aside=aside)

    x0 = min(c.bbox_px[0] for c in members)
    y0 = min(c.bbox_px[1] for c in members)
    x1 = max(c.bbox_px[0] + c.bbox_px[2] for c in members)
    y1 = max(c.bbox_px[1] + c.bbox_px[3] for c in members)

    mask = np.zeros((y1 - y0, x1 - x0), bool)
    for c in members:
        cx, cy, cw, ch = c.bbox_px
        mask[cy - y0 : cy - y0 + ch, cx - x0 : cx - x0 + cw] |= c.mask

    ordered = tuple(sorted(members, key=lambda c: -c.area_px))
    return Selection(members=ordered, bbox_px=(x0, y0, x1 - x0, y1 - y0), mask=mask, dpi=dpi,
                     set_aside=aside)


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
