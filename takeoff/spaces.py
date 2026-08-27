"""ALL coordinate conversion lives here.

Three spaces, and mixing them fails silently rather than loudly. Measured on Skanksa.pdf,
page index 4 (sheet T5, rotation 270):

    page_pt    unrotated PDF space, the mediabox: 1728 x 2592 pt, portrait.
               `get_text` and `get_drawings` return coordinates here. Always.
    sheet_pt   rotated, visible space, `page.rect`: 2592 x 1728 pt, landscape.
               What `get_pixmap` renders and what a human sees. Clips are given here.
    px         pixels in a rendered Raster: sheet_pt scaled by dpi/72, minus the
               render's clip origin.

Both point spaces are 1728 x 2592 permutations of each other, so a coordinate from the
wrong one lands inside the page and produces confidently wrong output instead of an error.
`scratch/viewport2.py` has exactly this bug: it passes a raster-derived rect straight into
`get_text(clip=...)`, which reads page_pt. Its region geometry is sound; its captions are not.

page_pt <-> sheet_pt needs the page's rotation matrix, which is why this module is on the
short list allowed to import pymupdf. sheet_pt <-> px is pure arithmetic and needs nothing.

May import pymupdf.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only, keeps pymupdf out of runtime import
    import pymupdf

Point = tuple[float, float]
Rect = tuple[float, float, float, float]
Matrix = tuple[float, float, float, float, float, float]

PT_PER_INCH = 72.0


def _apply(m: Matrix, x: float, y: float) -> Point:
    """Apply a PDF matrix (a, b, c, d, e, f) to a point, PyMuPDF's convention."""
    a, b, c, d, e, f = m
    return (a * x + c * y + e, b * x + d * y + f)


def _apply_rect(m: Matrix, rect: Rect) -> Rect:
    """Transform a rect by transforming its corners and re-deriving min/max.

    A 90-degree rotation swaps the roles of x0/x1 and y0/y1, so transforming only the
    two corners of the rect in place would produce an inverted, empty rectangle.
    """
    x0, y0, x1, y1 = rect
    corners = [_apply(m, x, y) for x, y in ((x0, y0), (x1, y0), (x0, y1), (x1, y1))]
    xs = [p[0] for p in corners]
    ys = [p[1] for p in corners]
    return (min(xs), min(ys), max(xs), max(ys))


@dataclass(frozen=True)
class PageSpace:
    """The page's rotation, captured as plain numbers so nothing downstream needs pymupdf."""

    page_index: int
    rotation: int
    page_size_pt: Point
    sheet_size_pt: Point
    rot: Matrix
    derot: Matrix

    @classmethod
    def from_page(cls, page: "pymupdf.Page") -> "PageSpace":
        mb, rect = page.mediabox, page.rect
        rm, dm = page.rotation_matrix, page.derotation_matrix
        return cls(
            page_index=page.number,
            rotation=page.rotation,
            page_size_pt=(mb.width, mb.height),
            sheet_size_pt=(rect.width, rect.height),
            rot=(rm.a, rm.b, rm.c, rm.d, rm.e, rm.f),
            derot=(dm.a, dm.b, dm.c, dm.d, dm.e, dm.f),
        )

    def page_to_sheet(self, x: float, y: float) -> Point:
        return _apply(self.rot, x, y)

    def sheet_to_page(self, x: float, y: float) -> Point:
        return _apply(self.derot, x, y)

    def page_rect_to_sheet(self, rect: Rect) -> Rect:
        return _apply_rect(self.rot, rect)

    def sheet_rect_to_page(self, rect: Rect) -> Rect:
        return _apply_rect(self.derot, rect)


def sheet_to_px(x: float, y: float, dpi: float, origin_sheet_pt: Point = (0.0, 0.0)) -> Point:
    s = dpi / PT_PER_INCH
    return ((x - origin_sheet_pt[0]) * s, (y - origin_sheet_pt[1]) * s)


def px_to_sheet(x: float, y: float, dpi: float, origin_sheet_pt: Point = (0.0, 0.0)) -> Point:
    s = PT_PER_INCH / dpi
    return (x * s + origin_sheet_pt[0], y * s + origin_sheet_pt[1])


def sheet_rect_to_px(rect: Rect, dpi: float, origin_sheet_pt: Point = (0.0, 0.0)) -> Rect:
    x0, y0 = sheet_to_px(rect[0], rect[1], dpi, origin_sheet_pt)
    x1, y1 = sheet_to_px(rect[2], rect[3], dpi, origin_sheet_pt)
    return (x0, y0, x1, y1)


def px_rect_to_sheet(rect: Rect, dpi: float, origin_sheet_pt: Point = (0.0, 0.0)) -> Rect:
    x0, y0 = px_to_sheet(rect[0], rect[1], dpi, origin_sheet_pt)
    x1, y1 = px_to_sheet(rect[2], rect[3], dpi, origin_sheet_pt)
    return (x0, y0, x1, y1)


def rebase_px(
    rect_px: Rect,
    from_dpi: float,
    from_origin: Point,
    to_dpi: float,
    to_origin: Point = (0.0, 0.0),
) -> Rect:
    """Move a rect between two px spaces via sheet_pt, the space they share.

    The viewer's tile pyramid and the detection raster are both px spaces, and today they
    happen to use the same DPI -- so a caller could scale by 1.0 and be accidentally right.
    Routing through sheet_pt means changing either DPI cannot silently misalign an overlay.
    """
    return sheet_rect_to_px(px_rect_to_sheet(rect_px, from_dpi, from_origin), to_dpi, to_origin)


def inches_to_px(inches: float, dpi: float) -> float:
    return inches * dpi


def px_to_inches(px: float, dpi: float) -> float:
    return px / dpi


def pt_to_px(pt: float, dpi: float) -> float:
    """A length, not a position: no origin involved."""
    return pt * dpi / PT_PER_INCH


def px_to_pt(px: float, dpi: float) -> float:
    return px * PT_PER_INCH / dpi
