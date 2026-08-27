"""Sheet -> named regions.

Region *geometry* comes from the raster (dilate-and-label with rule removal, per
scratch/viewport2.py). Only the caption and scale *text* comes from the text layer --
sheet metadata, not symbol detection. For a scanned set, OCR over the raster substitutes.

The same rule covers the words this module hands back for annotating a detection. A marker's
sheet reference -- `C\\T9`, `B/T10` -- is a caption on the drawing, not evidence that a marker
is there: detection has already finished by the time anything here is consulted, and it ran on
pixels. Keeping the join on this side of the boundary is what lets `detect.py` stay free of
pymupdf while a detection still arrives at the viewer carrying its label.

`get_text` reports page_pt, always. It is transformed to sheet_pt and then to raster px here,
once, so no caller is ever handed a coordinate in the space that fails silently.

May import pymupdf.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path

import pymupdf

from takeoff import raster, spaces
from takeoff.spaces import PageSpace, Point, Rect

# How far from a glyph a word may sit and still be about it, as a multiple of the glyph's
# larger dimension. A sheet reference is set beside the marker it belongs to, roughly a
# glyph-width away; much beyond this and the nearest text is whatever the plan put nearby.
NEAR_RADIUS_FACTOR = 0.6


@dataclass(frozen=True)
class Word:
    """One word from the text layer, already in raster pixels."""

    text: str
    bbox_px: Rect        # x0, y0, x1, y1

    @property
    def centre_px(self) -> Point:
        return ((self.bbox_px[0] + self.bbox_px[2]) / 2, (self.bbox_px[1] + self.bbox_px[3]) / 2)


def words_px(
    pdf_path: str | Path,
    page_index: int,
    dpi: float,
    origin_sheet_pt: Point = (0.0, 0.0),
) -> list[Word]:
    """Every word on a page, in the pixel space of a raster rendered at `dpi`.

    Empty for a scan. That is not a failure -- captions are read off a text layer and a
    scanned drawing has none -- so a detection on an image simply arrives without a label
    rather than the count falling over. Reading captions off a scan needs OCR, which the
    tool does not do; `raster.has_text_layer` is how a caller tells the difference and can
    say so rather than implying the drawing had no captions on it.
    """
    if raster.is_image(pdf_path):
        return []

    with pymupdf.open(pdf_path) as doc:
        page = doc[page_index]
        space = PageSpace.from_page(page)
        raw = [(tuple(w[:4]), w[4]) for w in page.get_text("words")]

    out: list[Word] = []
    for box_page_pt, text in raw:
        if not text.strip():
            continue
        box_px = spaces.sheet_rect_to_px(
            space.page_rect_to_sheet(box_page_pt), dpi, origin_sheet_pt
        )
        out.append(Word(text=text, bbox_px=box_px))
    return out


def words_near(
    words: list[Word],
    bbox_px: tuple[float, float, float, float],
    radius_factor: float = NEAR_RADIUS_FACTOR,
    limit: int = 4,
) -> list[Word]:
    """Words sitting beside a box, nearest centre first.

    Measured centre to centre rather than edge to edge: a word that overlaps the glyph is
    usually the drawing under it, while the caption sits clear and close. Ranking by centre
    keeps the caption ahead of a long note whose corner happens to clip the box.
    """
    x, y, w, h = bbox_px
    cx, cy = x + w / 2, y + h / 2
    radius = max(w, h) * (1 + 2 * radius_factor)

    scored: list[tuple[float, Word]] = []
    for word in words:
        wx, wy = word.centre_px
        distance = math.hypot(wx - cx, wy - cy)
        if distance <= radius:
            scored.append((distance, word))

    scored.sort(key=lambda pair: pair[0])
    return [word for _, word in scored[:limit]]


def label_for(
    words: list[Word],
    bbox_px: tuple[float, float, float, float],
    pattern: str | None = None,
    **kwargs,
) -> str | None:
    """The glyph's caption, or None when nothing nearby looks like one.

    Plain proximity is not enough. On T5 the marker at (7502, 2646) has the dimension string
    `4"` closer to its centre than its own reference `B/T10`, so "nearest word" returns a
    confidently wrong caption on 1 marker in 7. What a caption looks like is per-symbol
    knowledge, so it arrives as `SymbolClass.label_pattern` and this function stays generic.

    Without a pattern this falls back to the nearest word, which is right often enough to be
    useful and wrong quietly enough that a class meant for counting should set one.
    """
    near = words_near(words, bbox_px, limit=12, **kwargs)
    if pattern is None:
        return near[0].text if near else None

    matcher = re.compile(pattern)
    return next((w.text for w in near if matcher.search(w.text)), None)
