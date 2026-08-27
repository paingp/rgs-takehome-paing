"""The rasterization boundary: the detection raster, ink layers, and the viewer pyramid.

The two artifacts that come out of raster.py are meant for different consumers and must
never be confused -- but they must still describe the same sheet at the same geometry. The
reassembly test below is what proves that: a pyramid level, rebuilt from its own tiles,
against a detection render at the matching DPI.
"""

from __future__ import annotations

import math

import cv2
import numpy as np
import pytest

from takeoff import raster

PDF = "Skanksa.pdf"
E4 = 25             # page index of sheet E4, the spike's reference sheet
T5 = 4              # page index of sheet T5, where the gated build starts
E4_PLAN_CLIP = (108.0, 108.0, 1656.0, 1008.0)


def test_plan_region_matches_the_recorded_geometry() -> None:
    """PROGRESS.md records this clip at 300 DPI as 6450 x 3750 px = 24.2 MP."""
    r = raster.render(PDF, E4, dpi=300, clip_sheet_pt=E4_PLAN_CLIP)
    assert r.size_px == (6450, 3750)
    assert r.dpi == 300
    assert r.gray.dtype == np.uint8
    assert r.origin_sheet_pt[0] == pytest.approx(108.0, abs=0.5)


def test_level_pyramid_halves_to_a_single_pixel() -> None:
    levels = raster.levels_for(10800, 7200)
    assert levels[0][1:] == (1, 1)
    assert levels[-1][1:] == (10800, 7200)
    assert [lvl for lvl, _, _ in levels] == list(range(len(levels)))
    for (_, w0, h0), (_, w1, h1) in zip(levels, levels[1:]):
        assert w1 in (2 * w0, 2 * w0 - 1) and h1 in (2 * h0, 2 * h0 - 1)


def _reassemble(page_index: int, level: int, width: int, height: int) -> np.ndarray:
    """Rebuild one pyramid level from its tiles, discarding the overlap skirt."""
    tiles_dir = raster.dzi_dir(PDF, page_index) / "sheet_files" / str(level)
    canvas = np.zeros((height, width, 3), np.uint8)
    tile, overlap = raster.DZI_TILE, raster.DZI_OVERLAP
    for row in range(math.ceil(height / tile)):
        for col in range(math.ceil(width / tile)):
            img = cv2.imread(str(tiles_dir / f"{col}_{row}.png"))
            assert img is not None, f"missing tile {level}/{col}_{row}"
            x0, x1 = col * tile, min((col + 1) * tile, width)
            y0, y1 = row * tile, min((row + 1) * tile, height)
            ox, oy = x0 - max(x0 - overlap, 0), y0 - max(y0 - overlap, 0)
            canvas[y0:y1, x0:x1] = img[oy : oy + (y1 - y0), ox : ox + (x1 - x0)]
    return canvas


def test_pyramid_tiles_reassemble_into_the_detection_raster() -> None:
    """Level 12 is 2700 px wide, which is exactly 75 DPI on a 2592 pt sheet.

    Rebuilding it from tiles and comparing against a plain 75 DPI render checks two things
    at once: that the overlap skirt is being stripped at the right offset (a seam would show
    as a bright line of difference), and that the viewer pyramid and the detection raster
    are the same geometry rather than two independently drifting renders.
    """
    raster.build_dzi(PDF, T5)  # no-op once cached
    rebuilt = cv2.cvtColor(_reassemble(T5, 12, 2700, 1800), cv2.COLOR_BGR2GRAY)
    direct = raster.render(PDF, T5, dpi=75).gray

    assert rebuilt.shape == direct.shape
    diff = np.abs(rebuilt.astype(np.int16) - direct.astype(np.int16))
    assert diff.mean() < 0.5, f"pyramid drifts from the detection raster: {diff.mean():.3f}"
    assert (diff > 32).mean() == 0.0, "hard edges differ -- likely a tile seam"


def test_build_is_marked_complete_only_when_finished() -> None:
    """An interrupted build must be rebuilt, never served half-finished."""
    out = raster.dzi_dir(PDF, T5)
    assert raster.dzi_is_built(PDF, T5)
    assert (out / "COMPLETE").exists()
    assert (out / "sheet.dzi").exists()
    top = raster.levels_for(10800, 7200)[-1][0]
    assert (out / "sheet_files" / str(top) / "0_0.png").exists()
