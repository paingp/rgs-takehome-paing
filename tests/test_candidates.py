"""Line suppression, candidate components, and resolving a drag box to a symbol.

The synthetic scene below exists because the text filter has to be shown to actually change
the answer. On real drawing ink the filtered and unfiltered rules often agree, which would
let the filter rot into dead code without a test noticing.
"""

from __future__ import annotations

import numpy as np
import pytest

from takeoff import candidates as cand
from takeoff.raster import render
from takeoff.schema import Raster

PDF = "Skanksa.pdf"
T5 = 4
E4 = 25
E4_PLAN_CLIP = (108.0, 108.0, 1656.0, 1008.0)


# --------------------------------------------------------------------- line suppression


def test_line_suppression_reproduces_the_spike() -> None:
    """scratch/spike6.py measured 81% of ink removed as structure on the E4 plan."""
    layers = cand.ink_layers(render(PDF, E4, dpi=300, clip_sheet_pt=E4_PLAN_CLIP))
    assert 0.78 <= layers.removed_fraction <= 0.84, layers.removed_fraction


def test_ink_layers_are_a_partition_when_repair_is_off() -> None:
    r = render(PDF, T5, dpi=100)
    layers = cand.ink_layers(r, repair_gap_px=0)
    assert layers.ink.shape == layers.binary.shape == r.gray.shape
    assert np.array_equal(layers.ink, 255 - r.gray)
    # symbols is what detectors see: strictly ink, and strictly not structure.
    assert not (layers.symbols & ~layers.binary).any()
    assert not (layers.symbols & layers.structure).any()
    assert layers.symbols.sum() > 0


def test_repair_puts_back_only_ink_and_only_in_small_gaps() -> None:
    """Repair deliberately breaks the strict partition, and this is the whole of what it does.

    A wall drawn through a glyph takes a slice of the glyph with it. What comes back is
    structure ink -- so `symbols` and `structure` now overlap -- but only pixels that were
    ink to begin with, and only where they sit in a gap narrow enough to be a line's width.
    """
    r = render(PDF, T5, dpi=100)
    plain = cand.ink_layers(r, repair_gap_px=0)
    fixed = cand.ink_layers(r, repair_gap_px=cand.REPAIR_GAP_PX)

    restored = fixed.symbols & ~plain.symbols
    assert restored.any(), "the repair must actually restore something"
    assert not (fixed.symbols & ~fixed.binary).any(), "it may never invent ink"
    assert not (restored & ~plain.structure).any(), "everything restored was structure"
    assert restored.sum() < 0.1 * plain.symbols.sum(), "and it is a repair, not a flood"


# ------------------------------------------------------------------ candidates on a sheet


@pytest.fixture(scope="module")
def t5() -> tuple[Raster, list[cand.Candidate]]:
    r = render(PDF, T5, dpi=300)
    return r, cand.find_candidates(r, cand.ink_layers(r))


@pytest.fixture(scope="module")
def t5_plain() -> tuple[Raster, list[cand.Candidate]]:
    """Repair off. The drag rules below are about what snapping does with a box, and pinning
    them to the plain segmentation keeps their geometry constants meaningful."""
    r = render(PDF, T5, dpi=300)
    return r, cand.find_candidates(r, cand.ink_layers(r, repair_gap_px=0))


def test_t5_candidate_population(t5) -> None:
    """Measured at 4,514 in the 0.027-0.67 in band, with ink repair on. A large drift means
    the band, the suppression kernel or the repair moved -- each changes what the overlay
    shows and what snap can see."""
    r, found = t5
    assert 4_350 <= len(found) <= 4_700, len(found)
    lo, hi = cand.SYMBOL_BAND_IN
    for c in found:
        assert lo * r.dpi - 1 <= c.max_dim_px <= hi * r.dpi + 1


def test_candidate_ids_are_stable_across_runs(t5) -> None:
    """Review state and golden counts survive a re-run only if these do."""
    r, found = t5
    again = cand.find_candidates(r, cand.ink_layers(r))
    assert [c.id for c in found] == [c.id for c in again]
    assert len(set(c.id for c in found)) == len(found), "ids must be unique"


def test_candidate_mask_matches_its_patch(t5) -> None:
    """The mask must select ink, not paper -- an off-by-one crop would invert this."""
    _, found = t5
    sample = sorted(found, key=lambda c: -c.area_px)[:50]
    for c in sample:
        assert c.mask.shape == c.patch.shape
        assert c.mask.sum() == c.area_px
        assert c.patch[c.mask].mean() < c.patch[~c.mask].mean() if (~c.mask).any() else True


# ------------------------------------------------------------------------ synthetic scene


@pytest.fixture(scope="module")
def scene() -> tuple[Raster, list[cand.Candidate], cand.BBox]:
    """A marker with a label above it, and a line of note text clipped by the drag box.

    Written as explicit pixel ranges rather than cv2 primitives, because the distances are
    the point and stroke thickness would make them approximate.

        ring    cols  60..140, rows 110..190   the symbol
        label   cols  78..122, rows  85..100   four blobs, a complete `X/TY` above the ring
        note    cols 190..319, rows 150..165   eleven blobs, a sentence running off to the
                                               right; the drag box cuts it at col 230
        drag    cols  40..230, rows  70..210

    Neither run is the symbol. The note is obviously not -- it is clipped by the box edge --
    and neither is the label, which finishes inside: a caption is different on every instance,
    so a template holding one matches only the instance it was cut from. What identifies an
    instance is read separately, from the text layer, per detection.
    """
    gray = np.full((300, 400), 255, np.uint8)
    gray[110:113, 60:141] = 0      # ring, top edge
    gray[188:191, 60:141] = 0      # ring, bottom edge
    gray[110:191, 60:63] = 0       # ring, left edge
    gray[110:191, 138:141] = 0     # ring, right edge
    for col in (78, 90, 102, 114):
        gray[85:100, col : col + 9] = 0        # label characters
    for col in range(190, 320, 12):
        gray[150:166, col : col + 9] = 0       # note characters, running past the box edge

    r = Raster(gray=gray, dpi=300, origin_sheet_pt=(0.0, 0.0), page_index=0)
    found = cand.find_candidates(r, cand.ink_layers(r))
    return r, found, (40, 70, 190, 140)


def _is_note(c: cand.Candidate) -> bool:
    x, y, _, _ = c.bbox_px
    return y >= 150 and x >= 190


def _is_label(c: cand.Candidate) -> bool:
    x, y, _, _ = c.bbox_px
    return 80 <= y <= 105 and 70 <= x <= 130


def test_scene_has_the_pieces_it_claims(scene) -> None:
    _, found, _ = scene
    assert len(found) == 1 + 4 + 11
    assert sum(_is_label(c) for c in found) == 4
    assert sum(_is_note(c) for c in found) == 11


def test_the_box_is_the_boundary(scene) -> None:
    """union_inside takes everything enclosed, including the clipped note fragments."""
    r, found, drag = scene
    members = cand.snap(found, drag, dpi=r.dpi, rule=cand.union_inside).members
    assert len(members) == 8                      # ring + 4 label + 3 clipped note letters
    assert sum(_is_note(c) for c in members) == 3


def test_the_selection_never_grows_past_the_drag(scene) -> None:
    """The box is a ceiling on the symbol's size.

    A component is kept when most of its ink is inside, and it used to be kept WHOLE -- so a
    supply diffuser with a duct line curling off one corner came back bigger than the box
    drawn round it, and no amount of care with the mouse could exclude the curl. Measured on
    M2: a tight 82x106 drag returned a 120x123 selection.

    Refusing components that stick out was the alternative, and it loses the symbol instead of
    the curl. Cutting at the boundary keeps what was asked for and nothing else.
    """
    r, found, _ = scene
    # A box that cuts the ring in half: the half inside is the symbol, the half outside is not.
    drag = (60, 110, 40, 81)
    selection = cand.snap(found, drag, dpi=r.dpi)

    x, y, w, h = selection.bbox_px
    assert x >= drag[0] and y >= drag[1]
    assert x + w <= drag[0] + drag[2] and y + h <= drag[1] + drag[3]
    assert w < 81, "the ring really was wider than the box"


def test_a_piece_the_rule_dropped_is_still_offered(scene) -> None:
    """A caption is removed from the count, not from the screen.

    The rule reads a line of characters that does not include the symbol as a label and drops
    it, which is right for counting and wrong as a final answer: a person who meant to include
    it needs to see it and say so. Deleting ink the box enclosed leaves nothing to click and
    makes the default the only reachable outcome.
    """
    r, found, drag = scene
    selection = cand.snap(found, drag, dpi=r.dpi)
    assert len(selection.members) == 1, "the ring alone is the symbol"
    assert selection.set_aside, "and the label is kept to be shown"

    restored = selection.plus(range(len(selection.set_aside)))
    assert len(restored.members) > len(selection.members)
    assert not restored.set_aside, "nothing is offered twice"
    assert restored.area_px > selection.area_px, "the label's ink joined the symbol"

    # And a piece dropped by hand joins the same pile, so the click can be taken back.
    reduced = restored.without([1])
    assert len(reduced.members) == len(restored.members) - 1
    assert len(reduced.set_aside) == 1


def test_text_that_is_not_the_symbol_is_removed(scene) -> None:
    """The default rule: a line of characters that does not include the symbol is not it.

    Both runs go -- the clipped note and the complete label. It used to keep a label that
    finished inside the box, and that is what made a generous drag around the T5 marker build
    a glyph-plus-label template: 10 instances counted became 1, and the class lost its name
    because nothing in the registry matched. Whether a run finishes inside the box turns out
    to say nothing about whether it is the symbol.
    """
    r, found, drag = scene
    selection = cand.snap(found, drag, dpi=r.dpi)
    assert len(selection.members) == 1            # the ring, and only the ring
    assert sum(_is_note(c) for c in selection.members) == 0
    assert sum(_is_label(c) for c in selection.members) == 0


def test_the_symbol_itself_is_never_removed(scene) -> None:
    r, found, drag = scene
    selection = cand.snap(found, drag, dpi=r.dpi)
    biggest = max(selection.members, key=lambda c: c.area_px)
    assert biggest.bbox_px == (60, 110, 81, 81)


def test_filtering_actually_changes_the_answer(scene) -> None:
    """If both rules agreed, the text filter would be dead code."""
    r, found, drag = scene
    plain = len(cand.snap(found, drag, dpi=r.dpi, rule=cand.union_inside).members)
    filtered = len(cand.snap(found, drag, dpi=r.dpi).members)
    assert plain > filtered


def test_bbox_is_trimmed_to_the_kept_ink_not_the_drag(scene) -> None:
    """The user declares the boundary; the reported bounds still hug what was kept, so the
    measured size means something for a per-class size band later."""
    r, found, drag = scene
    selection = cand.snap(found, drag, dpi=r.dpi)
    x, y, w, h = selection.bbox_px
    assert (x, y) == (60, 110)                    # the ring's own top-left
    assert x + w == 141 and y + h == 191
    assert w < drag[2] and h < drag[3]


def test_text_runs_group_a_line_and_split_separate_lines(scene) -> None:
    _, found, _ = scene
    runs = {len(run) for run in cand.text_runs([c for c in found if not _is_note(c)])}
    assert 4 in runs, "the four label characters must group into one run"
    assert 1 in runs, "the ring is several times a letter's height and must stay alone"


def test_selection_mask_and_size(scene) -> None:
    r, found, drag = scene
    selection = cand.snap(found, drag, dpi=r.dpi)
    assert selection.mask.shape == (selection.bbox_px[3], selection.bbox_px[2])
    assert selection.area_px == sum(c.area_px for c in selection.members)
    width_in, _ = selection.size_in
    assert width_in == pytest.approx(selection.bbox_px[2] / 300)


def test_empty_drag_returns_an_empty_selection(scene) -> None:
    r, found, _ = scene
    selection = cand.snap(found, (300, 20, 60, 40), dpi=r.dpi)
    assert selection.is_empty
    assert selection.members == ()


# --------------------------------------------------------------- snapping on real ink


def _isolated(found: list[cand.Candidate]) -> cand.Candidate:
    """A component with clear space around it, so a generous drag cannot reach a neighbour."""
    boxes = np.array([c.bbox_px for c in found], float)
    x, y, w, h = boxes.T
    for i in sorted(range(len(found)), key=lambda i: -found[i].area_px):
        m = 0.5 * found[i].max_dim_px + 5
        overlaps = (
            (x < x[i] + w[i] + m) & (x + w > x[i] - m) & (y < y[i] + h[i] + m) & (y + h > y[i] - m)
        )
        if overlaps.sum() == 1:
            return found[i]
    raise AssertionError("no isolated component on T5")


def test_a_sloppy_drag_gives_the_same_glyph_every_time(t5) -> None:
    """Around an isolated glyph the reported bounds must not wander with the drag."""
    r, found = t5
    target = _isolated(found)
    x, y, w, h = target.bbox_px
    margin = int(0.4 * target.max_dim_px)
    rng = np.random.default_rng(0)

    results = set()
    for _ in range(10):
        jx, jy = rng.integers(-margin // 2, margin // 2 + 1, 2)
        drag = (x - margin + int(jx), y - margin + int(jy), w + 2 * margin, h + 2 * margin)
        results.add(cand.snap(found, drag, dpi=r.dpi).bbox_px)

    assert results == {target.bbox_px}, results


# The door beside the existing elevator on T5, at 300 DPI. The hatched elevation marker
# `C\T9` sits to its right; the marker's ink comes within 10 px of the door's arc, closer
# than the door's own parts are to each other, so no proximity rule can separate the two.
# What separates them is that a box drawn around the door encloses only the marker's apex.
T5_DOOR_DRAG = (6360, 2880, 150, 170)
T5_ELEV_MARKER = (6479, 2879, 43, 128)


def test_a_clipped_marker_tip_does_not_join_the_door_template(t5_plain) -> None:
    """The elevation marker is 72% of its bbox inside the door drag but 58% of its ink.

    Judged on bbox area it was kept whole, putting the marker's hatching -- 40 px beyond the
    box the user drew -- into the door's template. Judged on ink it falls below the bar.
    """
    r, found = t5_plain
    marker = next((c for c in found if c.bbox_px == T5_ELEV_MARKER), None)
    assert marker is not None, "the elevation marker is no longer a single component"

    assert cand._inside_fraction(marker, T5_DOOR_DRAG) < cand.INSIDE_FRACTION

    selection = cand.snap(found, T5_DOOR_DRAG, dpi=r.dpi)
    assert marker not in selection.members
    # The template must not reach past the drag box to collect the rest of the marker.
    assert selection.bbox_px[0] + selection.bbox_px[2] <= T5_DOOR_DRAG[0] + T5_DOOR_DRAG[2]


def test_ink_and_bbox_fractions_disagree_on_a_sparse_shape(t5_plain) -> None:
    """If they always agreed, measuring ink would be pointless indirection."""
    _, found = t5_plain
    marker = next(c for c in found if c.bbox_px == T5_ELEV_MARKER)
    x, y, w, h = marker.bbox_px
    bbox_fraction = cand._intersection_area(marker.bbox_px, T5_DOOR_DRAG) / (w * h)
    assert bbox_fraction >= cand.INSIDE_FRACTION > cand._inside_fraction(marker, T5_DOOR_DRAG)
