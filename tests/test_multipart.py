"""Symbols that are not one connected thing.

A supply diffuser is a square drawn as four separate corner brackets around an X. A door
marked for demolition is a DASHED arc — nine pieces, none of them touching. Neither is
unusual and neither could be counted: the template kept the largest piece and threw the rest
away as "context", so both matched one instance, themselves.

What replaced that rule is not a better rule. Three were measured and all three failed to tell
a symbol's own parts from a label lying beside it — joined ink misses both cases here, relative
size breaks on the demo door's 37x spread (a 594 px leaf against 16 px dashes), and text-run
grouping splits the diffuser into two "runs" and keeps none of it. So the drag decides, and a
person clicks off anything that does not belong. These tests pin both halves.
"""

from __future__ import annotations

import pytest

from takeoff import banding
from takeoff import candidates as cand
from takeoff import classes, detect, regions, scoring, templates
from takeoff.raster import DETECTION_DPI, render

PDF = "Skanksa.pdf"
M2, T3 = 13, 2

# A supply diffuser on M2: square, X, centre circle, drawn as four pieces.
DIFFUSER_DRAG = (1650, 2232, 105, 125)
# The demolition door in T3's legend: a dashed arc with its leaf and jamb marks.
DEMO_DOOR_DRAG = (4180, 4740, 340, 190)


@pytest.fixture(scope="module")
def m2():
    r = render(PDF, M2, dpi=DETECTION_DPI)
    return r, cand.find_candidates(r, cand.ink_layers(r))


@pytest.fixture(scope="module")
def t3():
    r = render(PDF, T3, dpi=DETECTION_DPI)
    return r, cand.find_candidates(r, cand.ink_layers(r))


def test_a_diffuser_keeps_all_four_of_its_corners(m2) -> None:
    """Four similar boxes in a row do chain as a line of "characters". They stay anyway.

    The caption filter drops a line only when every piece of it is small beside the symbol
    (`candidates.RUN_SIZE_RATIO`, 3x). These corners run 157-183 ink px against a 275 px
    primary -- 1.5x, nothing like the 5-20x a real caption runs beside its glyph.
    """
    r, found = m2
    selection = cand.snap(found, DIFFUSER_DRAG, dpi=r.dpi)
    template = templates.Template.from_selection("diffuser", selection, page_index=M2)

    assert len(template.parts) == 4
    assert template.size_px == (76, 100), "the whole square, not one quadrant"
    assert template.ink_px == 774, "all of it: the old rule kept 275 and dropped 499"


def test_a_dashed_demolition_door_keeps_its_dashes(t3) -> None:
    """The case that makes this general rather than a diffuser fix.

    Nine pieces spanning 37x in size. Any rule based on the parts resembling each other drops
    the dashes, which are the whole symbol -- and on size alone they look exactly like a
    caption, at 37x smaller than the leaf. The caption filter never gets to ask: it only
    judges pieces that chain into a line of two or more, and these chain with nothing.
    """
    r, found = t3
    selection = cand.snap(found, DEMO_DOOR_DRAG, dpi=r.dpi)
    template = templates.Template.from_selection("demo_door", selection, page_index=T3)

    assert len(template.parts) == 9
    assert template.ink_px == 1591, "the old rule kept 594 and dropped 997"
    inks = sorted((p[2] * p[3]) for p in template.parts)
    assert inks[-1] > 20 * inks[0], "the pieces really do span a huge range"


def test_the_gap_a_symbol_needs_comes_from_the_symbol(m2, t3) -> None:
    """Grouping reach is measured off the template, not guessed from a global constant.

    `GROUP_GAP_FACTOR` gives 8 px on the diffuser's footprint and 20 px on the demo door's,
    and a symbol whose pieces sit further apart than that can never be assembled.
    """
    r, found = m2
    diffuser = templates.Template.from_selection(
        "diffuser", cand.snap(found, DIFFUSER_DRAG, dpi=r.dpi), page_index=M2)
    r3, found3 = t3
    demo = templates.Template.from_selection(
        "demo_door", cand.snap(found3, DEMO_DOOR_DRAG, dpi=r3.dpi), page_index=T3)

    assert diffuser.part_gap_px == pytest.approx(2, abs=2)
    assert demo.part_gap_px == pytest.approx(20, abs=4)
    assert templates.Template.from_selection(
        "one_piece", cand.snap(found, (1695, 2238, 56, 52), dpi=r.dpi), page_index=M2
    ).part_gap_px == 0.0, "a single-piece symbol asks for nothing"


def test_a_corner_on_its_own_is_not_a_diffuser(m2) -> None:
    """Paing's requirement, and it needs no new rule to hold.

    One quadrant explains about a quarter of the whole template, so `backward` — and with it
    `match` — collapses. Before this, the template WAS one quadrant, so every quadrant on the
    sheet matched it perfectly and a return/exhaust grille (a square with a single diagonal)
    counted as a supply diffuser.
    """
    r, found = m2
    selection = cand.snap(found, DIFFUSER_DRAG, dpi=r.dpi)
    entry = detect.entry_from_selection(
        "diffuser", selection, page_index=M2,
        symbol=classes.SymbolClass(
            id="diffuser", name="Supply diffuser",
            anchor=classes.TemplateAnchor(page_index=M2, drag_bbox_px=DIFFUSER_DRAG)),
    )
    quadrant = next(c for c in selection.members if c.bbox_px == (1695, 2244, 44, 39))
    lone = scoring.best_variant(quadrant.mask, entry.bank, r.dpi, scoring.StrokeCoverageScorer())

    assert lone.match < 0.45, f"a corner scored {lone.match:.2f} against the whole symbol"
    assert lone.backward < 0.45, "it explains only its own quarter of the template"


def test_the_diffuser_is_counted_as_whole_squares(m2) -> None:
    """End to end: the boxes cover the symbol, and each is assembled from several pieces.

    The count is the detector's own — M2 has no annotations yet — so what is pinned is the
    SHAPE of the answer: whole-symbol boxes rather than quadrant-sized ones.
    """
    r, found = m2
    selection = cand.snap(found, DIFFUSER_DRAG, dpi=r.dpi)
    entry = detect.entry_from_selection(
        "diffuser", selection, page_index=M2,
        symbol=classes.SymbolClass(
            id="diffuser", name="Supply diffuser",
            anchor=classes.TemplateAnchor(page_index=M2, drag_bbox_px=DIFFUSER_DRAG)),
    )
    assert entry.detector == "template", "a square is a shape, not a curve"

    counted = [d for d in detect.detect(r, found, [entry], regions=regions.segment(r, found))
               if d.status is banding.Status.COUNTED]
    assert counted, "the symbol that was dragged must at least find itself"
    for d in counted:
        assert d.bbox_px[2] >= 70 and d.bbox_px[3] >= 90, f"{d.bbox_px} is a fragment"
        assert d.parts >= 3, "assembled from its pieces, not matched as one blob"
