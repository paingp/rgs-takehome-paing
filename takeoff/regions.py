"""Sheet -> named regions, and which of them hold drawings.

A sheet is not all drawing. T4 carries two plan viewports, two columns of general notes, a
legend and a title-block strip, and only the viewports can contain an instance of anything.
Measured on T4, 2,100 of 5,775 candidates (36%) are sheet furniture -- text the detector
groups, size-gates and sometimes scores, to no possible end.

The geometry is ported from scratch/viewport2.py, which segments correctly on pages 4 and 5:
remove the long straight runs that draw the sheet border and the title-block rules, dilate
what is left by a gutter so a paragraph coalesces into a block, and label. Its caption() is
NOT ported -- it passes a raster-derived rect into get_text(clip=...), which reads page_pt,
and that bug is documented in spaces.py.

WHY NOT THE TEXT LAYER. It would be easier and it would be wrong. A scan has no text layer,
so a region gate built on one would give a PDF and a photograph of the same sheet different
candidates -- and the whole architecture rests on the detector being unable to tell them
apart. Everything here is measured off the raster.

WHAT SEPARATES TEXT FROM DRAWING. Not text density: viewport2.py calls a region text above
150 characters per square inch, and by that test every region on both T4 and T5 is a drawing
(measured 7-9 per in2 in the plan viewports, 44-49 in the notes columns -- the signal is real
but the constant came from somewhere else).

What does separate them is that set type is all one height and a drawing is not. Share of a
region's components sitting within 20% of that region's median component height:

    T4 plan viewports      0.31, 0.48        T4 notes columns    0.86, 0.88, 0.91
    T5 plan viewport       0.46              T4/T5 title strips  0.66, 0.73, 0.76
                                             T5 note blocks      0.97, 0.97

Drawings 0.31-0.48, text 0.66-0.97, so the gate sits at 0.57. That margin is 0.18 and it is
drawn from two sheets; it wants re-deriving from ground truth like everything else here.

Raster-only module: must never import pymupdf, directly or transitively.
Enforced by tests/test_raster_only.py.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from takeoff.candidates import BBox
from takeoff.schema import Raster

# Segmentation runs downsampled. It is looking for paragraphs and viewports, not glyphs, and
# a third of the resolution makes the morphology 9x cheaper for an answer that does not move.
SEGMENT_DPI = 100

# A straight run this long is a sheet border or a title-block rule, never content.
RULE_LENGTH_IN = 6.0

# How far apart two pieces of one block may sit. Wider than a line gap, narrower than the
# space between a viewport and the notes column beside it.
GUTTER_IN = 0.30

# Smaller than this and it is a stray mark, not a region of the sheet.
MIN_REGION_IN = 1.2

# Height uniformity: how close to the median a component must be to count as "same height",
# and the share above which a region reads as set type. See the module docstring.
HEIGHT_TOLERANCE = 0.20
TEXT_UNIFORMITY = 0.57

# Below this many components the uniformity share is noise. Such a region stays unknown, and
# unknown is treated as drawing everywhere downstream -- refusing to guess must never cost a
# symbol.
MIN_SAMPLE = 20

DRAWING, TEXT, UNKNOWN = "drawing", "text", "unknown"


@dataclass(frozen=True)
class Region:
    """One block of the sheet, and what kind of thing it holds."""

    bbox_px: BBox
    kind: str
    fill: float                # ink over area, after the rules are removed
    uniformity: float          # share of components at the region's median height
    components: int

    @property
    def is_drawing(self) -> bool:
        """Unknown counts as drawing: never exclude ink on a statistic we could not take."""
        return self.kind != TEXT

    @property
    def area_px(self) -> int:
        return self.bbox_px[2] * self.bbox_px[3]

    def contains(self, x: float, y: float) -> bool:
        bx, by, bw, bh = self.bbox_px
        return bx <= x < bx + bw and by <= y < by + bh


def _blocks(raster: Raster) -> list[tuple[BBox, float]]:
    """Bounding boxes of the sheet's blocks, in the raster's own pixels."""
    factor = max(1, int(round(raster.dpi / SEGMENT_DPI)))
    dpi = raster.dpi / factor
    small = cv2.resize(
        raster.gray,
        (raster.gray.shape[1] // factor, raster.gray.shape[0] // factor),
        interpolation=cv2.INTER_AREA,
    )

    binary = (small < 235).astype(np.uint8)
    length = max(3, int(RULE_LENGTH_IN * dpi))
    rules = cv2.dilate(
        cv2.morphologyEx(binary, cv2.MORPH_OPEN,
                         cv2.getStructuringElement(cv2.MORPH_RECT, (length, 1)))
        | cv2.morphologyEx(binary, cv2.MORPH_OPEN,
                           cv2.getStructuringElement(cv2.MORPH_RECT, (1, length))),
        np.ones((5, 5), np.uint8),
    )
    content = binary & (1 - rules)

    gutter = max(1, int(GUTTER_IN * dpi))
    count, _, stats, _ = cv2.connectedComponentsWithStats(
        cv2.dilate(content, np.ones((gutter, gutter), np.uint8)), 8
    )

    floor = (MIN_REGION_IN * dpi) ** 2
    out: list[tuple[BBox, float]] = []
    for i in range(1, count):
        x, y, w, h, _ = (int(v) for v in stats[i])
        if w * h < floor:
            continue
        # Undo the dilation halo, so the box is the content's own extent.
        x, y = x + gutter // 2, y + gutter // 2
        w, h = max(w - gutter, 1), max(h - gutter, 1)
        ink = int(content[y : y + h, x : x + w].sum())
        out.append(((x * factor, y * factor, w * factor, h * factor), ink / max(w * h, 1)))
    return out


def classify(heights: np.ndarray) -> tuple[str, float]:
    """Is a block set type or drawing, from its components' heights alone."""
    if len(heights) < MIN_SAMPLE:
        return UNKNOWN, 0.0
    median = float(np.median(heights))
    if median <= 0:
        return UNKNOWN, 0.0
    uniformity = float(np.mean(np.abs(heights - median) <= HEIGHT_TOLERANCE * median))
    return (TEXT if uniformity > TEXT_UNIFORMITY else DRAWING), uniformity


def segment(raster: Raster, candidates=None) -> list[Region]:
    """The sheet's blocks, largest first, each labelled drawing / text / unknown.

    `candidates` are what the heights are taken from. They are passed in rather than found
    here because the caller has already paid for them, and because a class that segments its
    ink differently has to be judged on the ink it actually uses.
    """
    if candidates is None:
        from takeoff.candidates import find_candidates  # local: keeps the import graph acyclic
        candidates = find_candidates(raster)

    centres = np.array([c.centroid_px for c in candidates], float).reshape(-1, 2)
    heights = np.array([c.bbox_px[3] for c in candidates], float)

    regions: list[Region] = []
    for bbox, fill in _blocks(raster):
        x, y, w, h = bbox
        inside = (
            (centres[:, 0] >= x) & (centres[:, 0] < x + w)
            & (centres[:, 1] >= y) & (centres[:, 1] < y + h)
        )
        kind, uniformity = classify(heights[inside])
        regions.append(
            Region(bbox_px=bbox, kind=kind, fill=fill,
                   uniformity=uniformity, components=int(inside.sum()))
        )
    regions.sort(key=lambda r: -r.area_px)
    return regions


def kind_at(regions: list[Region], x: float, y: float) -> str:
    """What kind of block a point falls in. Ink outside every block stays unknown.

    Smallest region wins, so a detail drawn inside a viewport is judged on its own terms.
    """
    hits = [r for r in regions if r.contains(x, y)]
    if not hits:
        return UNKNOWN
    return min(hits, key=lambda r: r.area_px).kind


# How much of a sheet this filter may remove before it is disbelieved. Measured: it removes
# 47% of T4 and 14% of T5, and on both the counts are identical with it and without -- it
# takes work away, not symbols. On E4 it removes 91%, because the classifier reads the entire
# electrical plan as a notes column and deletes every receptacle on the sheet.
MAX_REMOVED = 0.75


def countable(regions: list[Region], candidates) -> list:
    """The candidates a count should consider: everything not sitting in set type.

    Deliberately a filter over candidates rather than a filter inside `find_candidates`.
    Selection still sees the whole sheet, so a legend entry can still be dragged and a
    template built from it -- what this removes is the general notes from the pool that
    gets grouped, size-gated and swept.

    AND IT REFUSES TO DELETE THE SHEET. This is an optimisation: on the sheets it was
    measured against it removes half the work and changes no count. That makes it exactly the
    kind of thing that must fail safe, because a segmentation that has misread a drawing does
    not announce itself -- it silently returns fewer candidates, and a class whose instances
    all lived in the deleted part comes back as an honest-looking zero.

    E4 is that case. Its plan is drawn in a screened lineweight with device glyphs and text at
    similar heights, so the height-uniformity classifier reads all 50 MP of it as set type and
    leaves 593 of 6,565 candidates -- none of them receptacles. Past `MAX_REMOVED` the answer
    is not to trust the classifier harder; it is to count the whole sheet and pay the time.
    """
    text = [r for r in regions if r.kind == TEXT]
    if not text:
        return list(candidates)
    kept = [
        c for c in candidates
        if not any(r.contains(*c.centroid_px) for r in text)
    ]
    if candidates and len(kept) < (1 - MAX_REMOVED) * len(candidates):
        return list(candidates)
    return kept


def drawing_regions(found: list[Region]) -> list[Region]:
    """The blocks a count can find something in. Unknown counts as drawing."""
    return [r for r in found if r.is_drawing]
