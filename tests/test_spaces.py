"""Coordinate conversion, tested the way it actually fails.

A rotation bug here does not raise. page_pt and sheet_pt are 1728 x 2592 permutations of one
another, so a coordinate from the wrong space lands inside the page and produces confidently
wrong output. Round-trip tests alone would not catch that -- they pass for any invertible
pair of transforms, including a wrong one. So there are two kinds of test here:

  * round-trip identity, over all 28 pages, for algebraic correctness
  * ink alignment, which anchors the algebra to the actual pixels, and is checked against
    the mixed-up interpretation to prove the test can tell them apart
"""

from __future__ import annotations

import numpy as np
import pymupdf
import pytest

from takeoff.raster import render
from takeoff.spaces import PageSpace, px_to_sheet, sheet_rect_to_px, sheet_to_px

# Part of the install check: `pytest -m smoke`, which is what install.ps1 runs. These
# prove numpy and the coordinate maths that everything else is expressed in.
pytestmark = pytest.mark.smoke

PDF = "Skanksa.pdf"
PROBE_DPI = 100  # enough to see a text stroke; a full-res render is not needed here


@pytest.fixture(scope="module")
def page_spaces() -> list[PageSpace]:
    with pymupdf.open(PDF) as doc:
        return [PageSpace.from_page(doc[i]) for i in range(doc.page_count)]


def test_every_page_is_landscape_sheet_portrait_page(page_spaces: list[PageSpace]) -> None:
    """The premise the whole module rests on: these sheets are stored rotated."""
    assert len(page_spaces) == 28
    for ps in page_spaces:
        assert ps.rotation in (90, 270), f"page {ps.page_index} rotation {ps.rotation}"
        assert ps.page_size_pt == (1728.0, 2592.0)
        assert ps.sheet_size_pt == (2592.0, 1728.0)


@pytest.mark.parametrize("dpi", [72, 300, 400])
def test_page_sheet_px_round_trip(page_spaces: list[PageSpace], dpi: int) -> None:
    """page_pt -> sheet_pt -> px -> sheet_pt -> page_pt returns the original, all 28 pages."""
    rng = np.random.default_rng(0)
    origin = (137.0, 211.5)  # a non-zero clip origin, so an origin bug cannot cancel out
    for ps in page_spaces:
        w, h = ps.page_size_pt
        xs = rng.uniform(0, w, 64)
        ys = rng.uniform(0, h, 64)
        for x, y in zip(xs, ys):
            sx, sy = ps.page_to_sheet(x, y)
            px, py = sheet_to_px(sx, sy, dpi, origin)
            bx, by = px_to_sheet(px, py, dpi, origin)
            rx, ry = ps.sheet_to_page(bx, by)
            assert rx == pytest.approx(x, abs=1e-6)
            assert ry == pytest.approx(y, abs=1e-6)


def test_sheet_rect_stays_a_rect_through_rotation(page_spaces: list[PageSpace]) -> None:
    """A 90-degree rotation swaps x and y; a naive two-corner transform inverts the rect."""
    for ps in page_spaces:
        x0, y0, x1, y1 = ps.page_rect_to_sheet((100.0, 200.0, 300.0, 500.0))
        assert x1 > x0 and y1 > y0
        assert (x1 - x0) == pytest.approx(300.0)  # the page rect's height, now its width
        assert (y1 - y0) == pytest.approx(200.0)


def _ink_coverage(gray: np.ndarray, boxes: list[tuple[float, float, float, float]]) -> tuple[float, int]:
    """Mean fraction of dark pixels inside boxes that land inside the raster."""
    values = []
    for x0, y0, x1, y1 in boxes:
        a, b, c, d = (int(round(v)) for v in (x0, y0, x1, y1))
        if c <= a or d <= b:
            continue
        if a < 0 or b < 0 or c > gray.shape[1] or d > gray.shape[0]:
            continue
        values.append(float((gray[b:d, a:c] < 200).mean()))
    return (float(np.mean(values)) if values else 0.0, len(values))


def test_text_boxes_land_on_ink_and_the_wrong_space_does_not() -> None:
    """The load-bearing test: transformed text boxes must sit on the glyphs that drew them.

    `get_text` reports page_pt. Transforming it to sheet_pt and then to px should put every
    word box squarely on ink. Feeding the same page_pt numbers in as if they were already
    sheet_pt -- the mistake in scratch/viewport2.py -- must land on blank paper instead,
    which is what makes this test capable of failing.
    """
    raster = render(PDF, 4, dpi=PROBE_DPI)
    with pymupdf.open(PDF) as doc:
        page = doc[4]
        space = PageSpace.from_page(page)
        boxes_page_pt = [tuple(w[:4]) for w in page.get_text("words")]

    assert len(boxes_page_pt) > 100, "expected a text layer on T5"

    correct = [
        sheet_rect_to_px(space.page_rect_to_sheet(b), raster.dpi, raster.origin_sheet_pt)
        for b in boxes_page_pt
    ]
    mixed_up = [
        sheet_rect_to_px(b, raster.dpi, raster.origin_sheet_pt) for b in boxes_page_pt
    ]

    right_cover, right_n = _ink_coverage(raster.gray, correct)
    wrong_cover, wrong_n = _ink_coverage(raster.gray, mixed_up)

    assert right_n == len(boxes_page_pt), "correctly transformed boxes must all be on-sheet"
    assert right_cover > 0.25, f"text boxes are not on ink: coverage {right_cover:.3f}"
    assert wrong_cover < 0.10, f"the mixed-up space also lands on ink: {wrong_cover:.3f}"
    assert right_cover > 5 * wrong_cover


def test_raster_origin_survives_a_clip() -> None:
    """A clipped render must still convert px back to the same sheet_pt as an unclipped one."""
    clip = (108.0, 108.0, 1656.0, 1008.0)
    full = render(PDF, 25, dpi=PROBE_DPI)
    part = render(PDF, 25, dpi=PROBE_DPI, clip_sheet_pt=clip)

    assert part.origin_sheet_pt[0] == pytest.approx(clip[0], abs=0.5)
    assert part.origin_sheet_pt[1] == pytest.approx(clip[1], abs=0.5)

    # The same sheet point, addressed through both rasters, must be the same pixel of ink.
    probe = (600.0, 500.0)
    fx, fy = full.to_px(*probe)
    px, py = part.to_px(*probe)
    assert full.gray[int(fy), int(fx)] == part.gray[int(py), int(px)]
