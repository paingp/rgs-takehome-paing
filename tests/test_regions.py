"""Sheet segmentation, and the gate that keeps general notes out of a count.

What these pin is the SEPARATION between a plan viewport and a column of set type. The
constant that does it (0.57 height-uniformity) sits in a 0.18 gap measured across two
sheets, so it is exactly the kind of number that rots silently if nothing asserts it.
"""

from __future__ import annotations

import numpy as np
import pytest

from takeoff import candidates as cand
from takeoff import classes, detect, regions
from takeoff.raster import render

PDF = "Skanksa.pdf"
T4, T5 = 3, 4
DPI = 300


@pytest.fixture(scope="module")
def t4():
    r = render(PDF, T4, dpi=DPI)
    found = cand.find_candidates(r, cand.ink_layers(r))
    return r, found, regions.segment(r, found)


@pytest.fixture(scope="module")
def t5():
    r = render(PDF, T5, dpi=DPI)
    found = cand.find_candidates(r, cand.ink_layers(r))
    return r, found, regions.segment(r, found)


def test_t4_separates_its_plan_viewports_from_its_notes(t4) -> None:
    """T4 is the adversarial sheet: two viewports at different scales, two columns of
    general notes, a legend and a title-block strip."""
    _, _, found_regions = t4
    drawing = [g for g in found_regions if g.kind == regions.DRAWING]
    text = [g for g in found_regions if g.kind == regions.TEXT]

    assert len(drawing) >= 2, "both plan viewports must survive as drawing"
    assert len(text) >= 3, "notes, legend and title block must all read as set type"

    # The two viewports are the largest drawing blocks and hold most of the drawn ink.
    biggest = max(drawing, key=lambda g: g.area_px)
    assert biggest.components > 1500


def test_the_gate_sits_in_the_measured_gap(t4, t5) -> None:
    """Drawing 0.31-0.48, text 0.66-0.97, gate 0.57. If a sheet ever lands between those,
    this fails rather than quietly reclassifying a viewport as a notes column."""
    for _, _, found_regions in (t4, t5):
        for g in found_regions:
            if g.kind == regions.UNKNOWN:
                continue
            assert not 0.50 < g.uniformity < 0.57 or g.kind == regions.DRAWING
            if g.kind == regions.DRAWING:
                assert g.uniformity <= regions.TEXT_UNIFORMITY
            else:
                assert g.uniformity > regions.TEXT_UNIFORMITY


def test_a_notes_column_is_a_third_of_t4s_candidates(t4) -> None:
    """The size of the prize, and the reason to bother at all."""
    _, found, found_regions = t4
    kept = regions.countable(found_regions, found)
    removed = len(found) - len(kept)
    assert removed > 0.30 * len(found), f"only {removed} of {len(found)} removed"


def test_t5_keeps_almost_everything_because_the_plan_fills_the_sheet(t5) -> None:
    """The same gate must not be greedy on a sheet that is nearly all drawing."""
    _, found, found_regions = t5
    kept = regions.countable(found_regions, found)
    assert len(kept) > 0.80 * len(found)


def test_an_unjudgeable_block_counts_as_drawing(t5) -> None:
    """A block with too few components to take a statistic on stays `unknown`, and unknown
    must never exclude ink -- refusing to guess cannot be allowed to cost a symbol."""
    _, _, found_regions = t5
    unknown = [g for g in found_regions if g.kind == regions.UNKNOWN]
    assert unknown, "T5 has small dense blocks that cannot be classified"
    assert all(g.is_drawing for g in unknown)
    assert all(g.components < regions.MIN_SAMPLE for g in unknown)


def test_the_gate_removes_work_and_not_symbols(t5) -> None:
    """The property that matters: counting inside the drawing blocks gives the same answer
    as counting the whole sheet, for both registered classes."""
    r = render(PDF, T5, dpi=DPI)
    for symbol in classes.all_classes():
        found = cand.find_candidates(r, cand.ink_layers(r, repair_gap_px=symbol.repair_gap_px))
        found_regions = regions.segment(r, found)
        entry = detect.build_entry(symbol, r, found)
        whole = detect.detect(r, found, [entry])
        gated = detect.detect(r, found, [entry], regions=found_regions)
        assert [d.id for d in gated] == [d.id for d in whole], symbol.id


def test_segmentation_is_stable_across_runs(t4) -> None:
    """Detection ids hash position (decision 10), so a segmentation that wobbled would move
    which candidates are counted and invalidate review state that refers to them."""
    r, found, once = t4
    twice = regions.segment(r, found)
    assert [g.bbox_px for g in once] == [g.bbox_px for g in twice]
    assert [g.kind for g in once] == [g.kind for g in twice]


def test_classify_refuses_a_sample_too_small_to_judge() -> None:
    assert regions.classify(np.array([]))[0] == regions.UNKNOWN
    assert regions.classify(np.full(regions.MIN_SAMPLE - 1, 24.0))[0] == regions.UNKNOWN
    # Set in one size: unmistakably type.
    assert regions.classify(np.full(regions.MIN_SAMPLE, 24.0))[0] == regions.TEXT
    # A spread of sizes: a drawing.
    assert regions.classify(np.arange(8, 8 + regions.MIN_SAMPLE * 4, 4.0))[0] == regions.DRAWING
