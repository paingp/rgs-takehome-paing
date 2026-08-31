"""Template bank, coverage scoring, banding, and the elevation marker end to end on T5.

The synthetic shapes exist because the symmetric score has to be shown to earn its keep. On
real ink a one-sided score agrees with the symmetric one most of the time, which would let
the backward half rot into dead code without a test noticing -- exactly the trap the drag
filter has a scene for in test_candidates.py.
"""

from __future__ import annotations

import numpy as np
import pytest

from takeoff import banding, candidates as cand, classes, detect, scoring, templates
from takeoff.raster import render
from takeoff.schema import Raster

PDF = "Skanksa.pdf"
T5 = 4

# The marker beside the existing elevator, and the four-and-three others on the sheet.
T5_MARKER_BBOX = (6479, 2879, 44, 129)


# ------------------------------------------------------------------------- template bank


def _triangle(width: int, height: int, hatched: bool) -> np.ndarray:
    """A right-pointing triangle, optionally filled with horizontal hatching."""
    mask = np.zeros((height, width), bool)
    for row in range(height):
        reach = int(width * (1 - abs(2 * row / (height - 1) - 1)))
        if reach <= 0:
            continue
        mask[row, :reach] = True
    if not hatched:
        edge = np.zeros_like(mask)
        edge[1:-1, 1:-1] = mask[1:-1, 1:-1]
        mask = mask & ~np.roll(edge, -2, 1) if width > 4 else mask
    else:
        mask[::3, :] = False
    return mask


def _template(mask: np.ndarray, class_id: str = "t") -> templates.Template:
    return templates.Template(
        class_id=class_id, mask=mask, dpi=300, source_page_index=0, source_bbox_px=(0, 0, 1, 1)
    )


def test_quarter_turns_are_exact_and_arbitrary_angles_are_not() -> None:
    bank = templates.variants(_template(_triangle(30, 60, True)))
    assert all(v.exact for v in bank)
    spun = templates.variants(_template(_triangle(30, 60, True)), rotations=(37.0,), mirrors=(False,))
    assert not spun[0].exact


def test_the_bank_deduplicates_symmetric_orientations() -> None:
    """A shape symmetric about its vertical axis has no distinct mirror."""
    symmetric = np.zeros((21, 21), bool)
    symmetric[8:13, :] = True          # a horizontal bar: mirroring changes nothing
    bank = templates.variants(_template(symmetric))
    assert len(bank) == 2, [v.label for v in bank]


def test_rotation_trims_the_blank_margin_it_creates() -> None:
    mask = _triangle(24, 60, True)
    for variant in templates.variants(_template(mask)):
        assert variant.mask.any(0).all() or variant.mask.any(1).all()
        assert variant.mask[0].any() and variant.mask[-1].any()


def test_distance_to_ink_is_zero_on_ink_and_grows_off_it() -> None:
    mask = np.zeros((9, 9), bool)
    mask[4, 4] = True
    dist = templates.distance_to_ink(mask)
    assert dist[4, 4] == 0
    assert dist[4, 6] == pytest.approx(2.0, abs=0.1)
    assert dist[0, 0] > dist[3, 3]


# ------------------------------------------------------------------------------- scoring


def test_symmetric_score_rejects_an_outline_that_forward_coverage_accepts() -> None:
    """The reason the score takes min(forward, backward) rather than forward alone.

    A bare outline sits entirely on top of a hatched triangle, so every pixel it has is
    explained and forward coverage is ~1. It explains almost none of the hatching, so backward
    coverage is low. One-sided scoring would call this a match.
    """
    hatched = _triangle(40, 80, True)
    outline = hatched.copy()
    outline[:, 6:] = False                      # keep only the leading edge

    bank = templates.variants(_template(hatched), rotations=(0.0,), mirrors=(False,))
    score = scoring.StrokeCoverageScorer().score(outline, bank[0], dpi=300)

    assert score.forward > 0.9, score
    assert score.backward < 0.5, score
    assert score.match == score.backward
    assert score.asymmetry > 0.4


def test_a_glyph_matches_itself_perfectly_in_every_orientation() -> None:
    mask = _triangle(30, 64, True)
    scorer = scoring.StrokeCoverageScorer()
    for variant in templates.variants(_template(mask)):
        assert scorer.score(variant.mask, variant, dpi=300).match == pytest.approx(1.0)


def test_empty_ink_scores_zero_rather_than_dividing_by_it() -> None:
    bank = templates.variants(_template(_triangle(30, 60, True)), rotations=(0.0,), mirrors=(False,))
    score = scoring.StrokeCoverageScorer().score(np.zeros((10, 10), bool), bank[0], dpi=300)
    assert score.match == 0.0


# ------------------------------------------------------------------------------- banding


def _symbol(**kwargs) -> classes.SymbolClass:
    base = dict(
        id="x",
        name="X",
        anchor=classes.TemplateAnchor(page_index=0, drag_bbox_px=(0, 0, 1, 1)),
        counted_at=0.90,
        review_floor=0.80,
        margin_at=0.10,
    )
    return classes.SymbolClass(**{**base, **kwargs})


@pytest.mark.parametrize(
    "match, margin, expected",
    [
        (0.95, 0.30, banding.Status.COUNTED),
        (0.95, None, banding.Status.COUNTED),   # single class: gate not evaluable, not failed
        (0.95, 0.02, banding.Status.REVIEW),    # looks like two things at once
        (0.85, 0.30, banding.Status.REVIEW),
        (0.50, 0.30, banding.Status.REJECTED),
    ],
)
def test_two_gates_three_bands(match, margin, expected) -> None:
    assert banding.band(match, margin, _symbol()).status is expected


def test_an_unevaluated_margin_says_so_rather_than_claiming_a_clean_pass() -> None:
    result = banding.band(0.99, None, _symbol())
    assert result.status is banding.Status.COUNTED
    assert result.reason and "not evaluated" in result.reason


def test_tally_reports_every_band_even_at_zero() -> None:
    counts = banding.tally([banding.Band(banding.Status.COUNTED)])
    assert counts == {"counted": 1, "review": 0, "rejected": 0}


# ------------------------------------------------------------- the registered class on T5


@pytest.fixture(scope="module")
def t5() -> tuple[Raster, list[cand.Candidate]]:
    r = render(PDF, T5, dpi=300)
    return r, cand.find_candidates(r, cand.ink_layers(r))


@pytest.fixture(scope="module")
def t5_unrepaired() -> tuple[Raster, list[cand.Candidate]]:
    """T5 with the repair turned off.

    Ink repair now rejoins the A/T10 marker before segmentation, which is a better fix than
    reassembling it afterwards -- but group matching still has to work, because some symbols
    are drawn in separate pieces and no repair will join those. These tests keep the old
    segmentation so the machinery stays exercised.
    """
    r = render(PDF, T5, dpi=300)
    return r, cand.find_candidates(r, cand.ink_layers(r, repair_gap_px=0))


@pytest.fixture(scope="module")
def marker_entry(t5) -> detect.ClassEntry:
    r, found = t5
    return detect.build_entry(classes.ELEVATION_MARKER, r, found)


@pytest.fixture(scope="module")
def marker_entry_unrepaired(t5_unrepaired) -> detect.ClassEntry:
    r, found = t5_unrepaired
    return detect.build_entry(classes.ELEVATION_MARKER, r, found)


def test_the_anchor_rebuilds_the_marker_the_selection_would_give(marker_entry) -> None:
    """The registry anchor is a drag box, so it must snap to the same glyph a person gets."""
    assert marker_entry.template.source_bbox_px == T5_MARKER_BBOX
    assert marker_entry.template.size_px == (44, 129)
    assert marker_entry.bank, "the bank must not be empty"


def test_elevation_marker_end_to_end_on_t5(t5, marker_entry) -> None:
    """Nine instances clear the counted gate, and all nine are real.

    These numbers are no longer the detector's own: T5 is annotated, and `-m eval.suites
    --page 5` scores this class 9 TP / 0 FP / 3 FN against 12 reviewed instances, with both
    remaining instances found and sent to review. The test pins the *separation*, which is
    the property that would silently rot -- the counted set must stay well clear of the best
    thing that is not a marker.

    It was ten for a while. The tenth is the marker at (6552, 2509) that a leader line is
    drawn through: its blob is 116x146 px against a 44x129 marker, so the size gate refuses
    it and nothing scores it whole. `fused_windows` searches inside and finds it at 0.961 --
    and a fused instance is now capped at REVIEW however well it scores, because the window
    that scored was chosen by the search and is the best of many tries rather than one honest
    reading. Found, not counted, and a person confirms it. See `banding.band`'s `ceiling`.
    """
    r, found = t5
    results = detect.detect(r, found, [marker_entry], keep_rejected=True)

    counted = [d for d in results if d.status is banding.Status.COUNTED]
    assert len(counted) == 9, [(d.match, d.bbox_px) for d in counted]
    assert all(d.match >= 0.90 for d in counted)
    assert not any(d.fused for d in counted), "a fused instance may never be counted"

    # The occluded one is still found, which is the property the fused search exists for.
    recovered = [d for d in results if d.fused and d.match > 0.95]
    assert recovered, "the fused search must still reach the occluded marker"
    assert all(d.status is banding.Status.REVIEW for d in recovered)

    # One of them is the A/T9 marker with a line drawn through it. Ink repair is what makes
    # it findable at all; before, it was not even a candidate.
    assert any(d.bbox_px[0] == 8636 and d.bbox_px[1] == 2867 for d in counted),         "the occluded A/T9 marker must be counted"

    # What sits just below the gate is no longer junk: it is B/T12, a real marker that a
    # line was drawn through, held at 0.868 for review rather than counted or lost. That is
    # the intended handling of an instance the tool can only partly see.
    #
    # Judged on whole components only. A fused window scores higher than this -- the search
    # picks its best position -- and mixing the two would compare a chosen score against an
    # unchosen one.
    others = sorted(
        (d for d in results if d.status is not banding.Status.COUNTED and not d.fused),
        key=lambda d: -d.match,
    )
    assert others, "the size gate should still admit some non-markers to score"
    assert 0.84 <= others[0].match <= 0.90, others[0].match
    assert others[0].status is banding.Status.REVIEW, "an occluded marker must surface"

    # The marker the template came from must count as an instance of itself.
    assert any(d.bbox_px == T5_MARKER_BBOX for d in counted)


def test_a_fused_instance_can_never_be_counted(t5, marker_entry) -> None:
    """The precision guarantee, stated where it cannot quietly lapse.

    A window found inside a larger blob was chosen by the search: its score is the best of
    hundreds of positions, not one reading of one component, so it does not mean what a
    class's gate was tuned to mean. Counting them outright would spend precision -- the one
    property this detector has that is worth 1.000 on two of three graded sheets -- to buy
    recall that a person can confirm instead.
    """
    r, found = t5
    results = detect.detect(r, found, [marker_entry], keep_rejected=True)
    fused = [d for d in results if d.fused]

    assert fused, "the fused search must find something on T5"
    assert max(d.match for d in fused) > marker_entry.symbol.counted_at,         "and at least one must score well enough that only the ceiling holds it back"
    assert all(d.status is not banding.Status.COUNTED for d in fused)
    assert all("held at review" in (d.reason or "") for d in fused
               if d.status is banding.Status.REVIEW and d.match >= marker_entry.symbol.counted_at)


def test_the_fused_search_looks_in_blobs_the_size_gate_threw_away(t5, marker_entry) -> None:
    """Where the recoveries actually come from.

    `find_candidates` stops at 0.67 in, and a symbol drawn touching a wall belongs to the
    wall's component -- which is bigger than that and so was never in the pool the fused
    search filtered. Measured on E4: 25 of 36 missed receptacles sit in hosts running 108-421
    px against a 201 px band top, and raising the fused search's own cap without this reached
    4 of them.
    """
    r, found = t5
    hosts = cand.host_blobs(r, cand.ink_layers(r))
    assert hosts, "T5 has components above the symbol band"
    assert all(max(c.bbox_px[2], c.bbox_px[3]) > cand.SYMBOL_BAND_IN[1] * r.dpi for c in hosts)
    assert not ({c.id for c in hosts} & {c.id for c in found}), "hosts are not candidates"

    # They reach the fused search and nothing else: a host must never be scored whole, and
    # must never be grouped with a real candidate.
    searched = {c.id for c in detect.fused_blobs([*found, *hosts], marker_entry)}
    assert searched & {c.id for c in hosts}, "hosts must reach the search"
    for members in detect.candidate_groups(hosts, marker_entry):
        raise AssertionError("a host blob must never be grouped as an instance")


def test_two_instances_in_one_host_blob_are_both_found(t5) -> None:
    """One window per blob was not enough.

    A host blob is a run of wall with symbols drawn on it, and it holds as many as it holds --
    6 of the 19 host blobs on E4 hide two receptacles each. `fused_windows` returned only its
    best window, and the assignment loop then claimed the whole blob by id, so the second
    instance was unreachable twice over.
    """
    r, found = t5
    marker = classes.ELEVATION_MARKER
    entry = detect.build_entry(marker, r, found)

    # Two copies of the glyph, joined by a bar, in one component.
    glyph = next(c for c in found if c.bbox_px == T5_MARKER_BBOX)
    gw, gh = glyph.bbox_px[2], glyph.bbox_px[3]
    canvas = np.zeros((gh + 20, gw * 2 + 60), bool)
    canvas[10:10 + gh, 10:10 + gw] = glyph.mask
    canvas[10:10 + gh, gw + 50:gw + 50 + gw] = glyph.mask
    canvas[10 + gh // 2, :] = True                      # the bar that welds them together

    host = cand.Candidate(
        id="host", bbox_px=(0, 0, canvas.shape[1], canvas.shape[0]),
        centroid_px=(canvas.shape[1] / 2, canvas.shape[0] / 2), mask=canvas,
        patch=np.zeros(canvas.shape, np.uint8), area_px=int(canvas.sum()), raw_id=1,
    )

    windows = detect.fused_windows(host, entry, r.dpi, scoring.StrokeCoverageScorer())
    assert len(windows) >= 2, [round(s.match, 3) for s, _ in windows]
    assert all(s.match >= marker.review_floor for s, _ in windows)
    boxes = [b for _, b in windows]
    for i, a in enumerate(boxes):
        for b in boxes[i + 1:]:
            assert not detect._boxes_overlap(a, b), "windows must not claim the same ink"


def test_the_marker_is_found_at_several_orientations(t5, marker_entry) -> None:
    """If every hit used one variant, the rotation bank would be doing nothing."""
    r, found = t5
    counted = [
        d for d in detect.detect(r, found, [marker_entry]) if d.status is banding.Status.COUNTED
    ]
    assert len({d.variant_label for d in counted}) >= 2


def test_detection_ids_are_stable_and_position_bound(t5, marker_entry) -> None:
    r, found = t5
    first = detect.detect(r, found, [marker_entry])
    again = detect.detect(r, found, [marker_entry])
    assert [d.id for d in first] == [d.id for d in again]
    assert len({d.id for d in first}) == len(first)


def test_margin_is_none_while_only_one_class_competes(t5, marker_entry) -> None:
    """Documents the deliberate gap: the margin gate goes live when a second class lands."""
    r, found = t5
    assert all(d.margin is None for d in detect.detect(r, found, [marker_entry]))


def test_the_size_gate_keeps_the_scored_pool_small(t5, marker_entry) -> None:
    """Scoring every candidate against every variant is what the gate exists to avoid."""
    _, found = t5
    admitted = [c for c in found if detect._passes_size_gate(c, marker_entry)]
    assert len(admitted) < len(found) // 50, f"{len(admitted)} of {len(found)}"


def test_a_live_selection_produces_the_same_count_as_the_anchor(t5) -> None:
    """The viewer's path and the test's path must not be able to disagree."""
    r, found = t5
    selection = cand.snap(found, classes.ELEVATION_MARKER.anchor.drag_bbox_px, dpi=r.dpi)
    live = detect.entry_from_selection("elev_marker", selection, page_index=r.page_index)
    anchored = detect.build_entry(classes.ELEVATION_MARKER, r, found)
    assert [d.id for d in detect.detect(r, found, [live])] == [
        d.id for d in detect.detect(r, found, [anchored])
    ]


def test_registry_rejects_a_duplicate_id() -> None:
    with pytest.raises(ValueError, match="already registered"):
        classes.register(classes.ELEVATION_MARKER)


def test_summarise_splits_by_band_and_class(t5, marker_entry) -> None:
    r, found = t5
    summary = detect.summarise(detect.detect(r, found, [marker_entry], keep_rejected=True))
    assert summary["total"] == sum(summary["by_band"].values())
    assert set(summary["by_class"]) == {"elev_marker"}


# ------------------------------------------------- a template must be one connected glyph

# The same marker, dragged three ways: tight to the triangle, and twice with the `C\T9`
# sheet reference included. The label is a separate component 14 px off the triangle's ink
# and differs on every instance, so it can identify a marker but never help match one.
T5_MARKER_DRAGS = {
    "glyph only": (6470, 2870, 62, 148),
    "with label": (6470, 2870, 105, 148),
    "generous": (6460, 2860, 120, 170),
}


def test_the_template_is_every_piece_of_geometry_the_box_held(t5) -> None:
    """The drag is the boundary for GEOMETRY; a caption is not geometry.

    It used to keep the largest piece plus whatever the drawing had joined to it, which is
    wrong for any symbol drawn as separate parts -- a supply diffuser (four corner brackets
    around an X) lost 64% of its ink that way and a demolition door's dashed arc lost 63%.

    The A/T10 marker is the case on this sheet: a centre line runs through its apex, gets
    removed as structure, and leaves two halves that are both the symbol.
    """
    r, found = t5
    selection = cand.snap(found, (7300, 2292, 65, 155), dpi=r.dpi)
    assert len(selection.members) == 2, "suppression really did split it"

    template = templates.Template.from_selection("elev_marker", selection, page_index=4)
    assert template.size_px == (44, 135), "both halves, not the larger one"
    assert len(template.parts) == 2
    assert not template.trimmed and template.context_blobs == 0


def test_a_label_in_the_box_does_not_change_the_count(t5) -> None:
    """Dragging sloppily over a marker's sheet reference must not cost the count.

    It did, for one day: the drag became the boundary for everything inside it, so the label
    joined the template and 10 counted became 1. The rule is now that a line of characters
    which does not include the symbol is not the symbol -- see
    `candidates.inside_minus_foreign_text`.
    """
    r, found = t5

    def count(selection):
        entry = detect.entry_from_selection("elev_marker", selection, page_index=r.page_index)
        return sum(1 for d in detect.detect(r, found, [entry])
                   if d.status is banding.Status.COUNTED)

    tight = cand.snap(found, T5_MARKER_DRAGS["glyph only"], dpi=r.dpi)
    sloppy = cand.snap(found, T5_MARKER_DRAGS["with label"], dpi=r.dpi)
    assert len(sloppy.members) == 1, "the label was dropped, not kept and matched"
    assert count(sloppy) == count(tight) == 9


# The other marker on T5, and a harder case than `C/T9`. It points left, its glyph is 44 px
# tall against 24 px letters, and the word `FILM` is printed in the empty wedge beneath it.
B_T9_DRAGS = {
    "glyph only": (8630, 1783, 146, 56),
    "with label": (8606, 1749, 194, 134),
}


def test_a_caption_beside_a_squat_glyph_is_still_dropped(t5) -> None:
    """A glyph can chain into the line of text it labels, and then protect it.

    `C/T9` is dropped because the run of characters does not include the symbol. That test
    passes on a marker three times a letter's height. This one is 44 px against 24 -- a ratio
    of 1.83, which any height band loose enough for real text will accept -- and the letters
    sit close enough to share a baseline with it. So the glyph joined the run, the run
    therefore "included the symbol", and `FILM` plus four dashes went into the template: 9
    counted became 0.

    The fix is to judge the line with the symbol set aside, and drop it only if every
    character is small beside the symbol. See `candidates.RUN_SIZE_RATIO`.
    """
    r, found = t5

    def count(selection):
        entry = detect.entry_from_selection("elev_marker", selection, page_index=r.page_index)
        return sum(1 for d in detect.detect(r, found, [entry])
                   if d.status is banding.Status.COUNTED)

    tight = cand.snap(found, B_T9_DRAGS["glyph only"], dpi=r.dpi)
    sloppy = cand.snap(found, B_T9_DRAGS["with label"], dpi=r.dpi)
    assert len(sloppy.members) == 1, "the caption was dropped, not kept and matched"
    assert count(sloppy) == count(tight) == 8


def test_a_multi_blob_template_is_matchable_now(t5) -> None:
    """It was not, and that is why the template used to be trimmed to one blob.

    `detect` scored one connected component at a time, so a template spanning disconnected
    blobs had nothing that could represent it -- not a low score, an impossible one, measured
    at 0.780 against 4,770 candidates. Group matching is what changed: the pieces are
    assembled and scored together, which is what lets a diffuser or a dashed arc be counted
    at all. See tests/test_multipart.py.
    """
    r, found = t5
    selection = cand.snap(found, (7300, 2292, 65, 155), dpi=r.dpi)
    assert len(selection.members) == 2

    entry = detect.entry_from_selection(
        "elev_marker", selection, page_index=4, symbol=classes.ELEVATION_MARKER)
    counted = [d for d in detect.detect(r, found, [entry])
               if d.status is banding.Status.COUNTED]
    assert len(counted) == 8
    assert any(d.parts > 1 for d in counted), "at least one was assembled from its pieces"


def test_a_template_matches_the_glyph_it_was_cut_from(t5) -> None:
    """The self-consistency the six-blob template failed: 0.645 against its own source."""
    r, found = t5
    for drag in T5_MARKER_DRAGS.values():
        entry = detect.entry_from_selection(
            "elev_marker", cand.snap(found, drag, dpi=r.dpi), page_index=r.page_index
        )
        selection = cand.snap(found, drag, dpi=r.dpi)
        best = scoring.best_variant(
            selection.mask, entry.bank, r.dpi, scoring.StrokeCoverageScorer()
        )
        assert best.match == pytest.approx(1.0), (drag, best)


def test_a_label_in_the_box_costs_the_class_its_name_until_excluded(t5) -> None:
    """The price of letting the drag decide, stated plainly.

    Identification compares what was selected against each registered class's reference. A
    drag that also caught the `C/T9` sheet reference is glyph-plus-label, which matches no
    reference, so the symbol comes back unnamed and loses that class's caption pattern and
    its calibrated thresholds. Dropping the label pieces is what gives them back -- which is
    why the server applies exclusions BEFORE identifying, not after.
    """
    r, found = t5
    library = {"elev_marker": detect.build_entry(classes.ELEVATION_MARKER, r, found)}

    sloppy = cand.snap(found, T5_MARKER_DRAGS["with label"], dpi=r.dpi)
    guess, _ = detect.identify(sloppy, r, found, references=library)
    assert guess.id == "elev_marker", "a caption in the box must not cost the class its name"

    # And the mechanism for dropping a piece by hand still works, for the pieces no rule can
    # judge -- a leader arrow, a dimension tick, anything that is not a line of characters.
    split = cand.snap(found, (7300, 2292, 65, 155), dpi=r.dpi)
    assert len(split.without([1]).members) == 1


# --------------------------------------------------------------- a zero explains itself


def test_diagnose_names_the_gate_that_stopped_a_run(t5, marker_entry) -> None:
    r, found = t5
    empty = detect.diagnose(r, found, marker_entry, [])
    assert empty["note"] and "right size" in empty["note"]
    assert empty["size_gate_admitted"] > 0
    assert empty["best_match"] > 0.9


def test_diagnose_is_quiet_when_there_is_something_to_report(t5, marker_entry) -> None:
    r, found = t5
    real = detect.diagnose(r, found, marker_entry, detect.detect(r, found, [marker_entry]))
    assert real["note"] is None


def test_nothing_inside_the_box_is_quietly_ignored(t5) -> None:
    """There is no "context" any more: what the box held is what gets matched."""
    r, found = t5
    selection = cand.snap(found, T5_MARKER_DRAGS["with label"], dpi=r.dpi)
    entry = detect.entry_from_selection("elev_marker", selection, page_index=r.page_index)
    assert entry.template.context_blobs == 0
    assert "separate piece" not in detect.diagnose(r, found, entry, [])["note"]


# ------------------------------------------------ a glyph that line suppression split apart

# The A/T10 marker on T5 sits inside a dotted circle with the drawing's centre line running
# through its apex. Line suppression removes that line -- and the 3x3 dilation takes the apex
# junction with it -- so one component in the raw ink becomes two, and neither half is the
# symbol. Both are needed, and together they must count once.
T5_SPLIT_MARKER_HALVES = ((7313, 2302, 41, 62), (7311, 2370, 43, 66))
T5_SPLIT_MARKER_WHOLE = (7311, 2302, 43, 134)


def test_line_suppression_really_does_split_that_glyph(t5_unrepaired) -> None:
    """The premise. If suppression stopped splitting it, grouping would be untested here."""
    r, _ = t5_unrepaired
    layers = cand.ink_layers(r)
    x, y, w, h = T5_SPLIT_MARKER_WHOLE
    window = (slice(y - 4, y + h + 4), slice(x - 4, x + w + 4))

    import cv2

    raw = cv2.connectedComponentsWithStats(layers.binary[window].astype(np.uint8), 8)[0] - 1
    kept = cv2.connectedComponentsWithStats(layers.symbols[window].astype(np.uint8), 8)[0] - 1
    assert raw == 1, "the glyph is one component before suppression"
    assert kept == 2, "and two after it"


def test_both_halves_are_candidates_and_neither_is_the_symbol(t5_unrepaired, marker_entry_unrepaired) -> None:
    _, found = t5_unrepaired
    halves = [c for c in found if c.bbox_px in T5_SPLIT_MARKER_HALVES]
    assert len(halves) == 2

    scorer = scoring.StrokeCoverageScorer()
    for half in halves:
        assert scoring.best_variant(half.mask, marker_entry_unrepaired.bank, 300, scorer).match < 0.80


def test_grouping_reassembles_the_split_marker(t5_unrepaired, marker_entry_unrepaired) -> None:
    r, found = t5_unrepaired
    hit = next(
        (d for d in detect.detect(r, found, [marker_entry_unrepaired]) if d.bbox_px == T5_SPLIT_MARKER_WHOLE),
        None,
    )
    assert hit is not None, "the split marker must be found once grouping is on"
    assert hit.parts == 2
    assert hit.status is banding.Status.COUNTED
    assert hit.match == pytest.approx(1.0, abs=0.01)


def test_a_split_marker_is_counted_once_not_once_per_piece(t5_unrepaired, marker_entry_unrepaired) -> None:
    """Competitive assignment over groups. Without it the halves count too."""
    r, found = t5_unrepaired
    results = detect.detect(r, found, [marker_entry_unrepaired])
    overlapping = [
        d
        for d in results
        if d.bbox_px in T5_SPLIT_MARKER_HALVES or d.bbox_px == T5_SPLIT_MARKER_WHOLE
    ]
    assert len(overlapping) == 1, [d.bbox_px for d in overlapping]


def test_group_growth_is_bounded_by_the_template_footprint(t5_unrepaired, marker_entry_unrepaired) -> None:
    """The bound that stops proximity chaining into the note beside a symbol."""
    _, found = t5_unrepaired
    for group in detect.candidate_groups(found, marker_entry_unrepaired):
        assert len(group) <= detect.MAX_GROUP_PARTS
        _, bbox = detect._group_mask(group)
        assert detect._fits_footprint(bbox, marker_entry_unrepaired)


def test_groups_include_every_step_of_their_growth(t5_unrepaired, marker_entry_unrepaired) -> None:
    """A maximal group is not the only reading; the smaller ones must compete too.

    Emitting only the fully grown group cost 6 of 8 counted markers when a second scale was
    registered, because the bound let each group swallow its neighbours and no tighter
    reading was ever offered.
    """
    _, found = t5_unrepaired
    groups = detect.candidate_groups(found, marker_entry_unrepaired)
    sizes = {len(g) for g in groups}
    assert 1 in sizes and max(sizes) > 1, sizes

    halves = [c for c in found if c.bbox_px in T5_SPLIT_MARKER_HALVES]
    keys = {tuple(sorted(c.id for c in g)) for g in groups}
    assert tuple(sorted(c.id for c in halves)) in keys
    for half in halves:
        assert (half.id,) in keys, "each piece must also stand alone"


def test_a_split_glyph_selects_whole_not_halved(t5_unrepaired) -> None:
    """Dragging round A/T10 must give a whole triangle, not the larger half of one.

    Before raw connectivity was consulted, the two halves looked like a glyph plus a label,
    so the template came out 0.143 x 0.220 in instead of 0.143 x 0.427 -- half a triangle,
    which then counted each half of every marker as its own instance.
    """
    r, found = t5_unrepaired
    selection = cand.snap(found, (7300, 2292, 65, 155), dpi=r.dpi)
    assert len(selection.members) == 2

    template = templates.Template.from_selection("elev_marker", selection, page_index=4)
    assert template.size_px == (43, 134)
    assert template.context_blobs == 0, "neither half is context; both are the symbol"


def test_the_pieces_a_selection_holds_are_reported_and_removable(t5) -> None:
    """What the viewer draws, and what a click acts on.

    The label is gone before this point -- a run of characters without the symbol in it is
    dropped -- so what remains to be shown are pieces of geometry, and the click exists for
    the ones no rule can judge.
    """
    r, found = t5
    selection = cand.snap(found, (7300, 2292, 65, 155), dpi=r.dpi)
    assert len(selection.parts_px) == 2
    # Largest INK first, not largest box: a thin curve can outrun a dense glyph's bounds.
    assert selection.members[0].area_px == max(c.area_px for c in selection.members)
    assert selection.parts_px[0] == selection.members[0].bbox_px

    kept = selection.without([1])
    assert len(kept.members) == 1
    assert kept.bbox_px == selection.members[0].bbox_px


def test_raw_id_separates_a_split_glyph_from_a_neighbouring_label(t5_unrepaired) -> None:
    """The signal itself, with no thresholds in sight."""
    _, found = t5_unrepaired
    halves = [c for c in found if c.bbox_px in T5_SPLIT_MARKER_HALVES]
    assert len({c.raw_id for c in halves}) == 1, "the halves were one blob as drawn"

    # Unrepaired geometry: the repair restores a sliver of the glyph's own suppressed edge.
    triangle = next(c for c in found if c.bbox_px == (6479, 2879, 43, 128))
    label = [c for c in found if c.bbox_px in ((6535, 2914, 25, 13), (6535, 2928, 24, 16))]
    assert all(c.raw_id != triangle.raw_id for c in label), "the label never was"


def test_every_route_to_the_same_marker_counts_the_same(t5) -> None:
    """Anchor, tight drag, sloppy drag, and a drag on the once-split instance must agree."""
    r, found = t5
    drags = [
        (6470, 2870, 62, 148),    # C\T9, tight
        (7300, 2292, 65, 155),    # A/T10, the split one
        (7280, 2270, 95, 190),    # A/T10, sloppy
    ]
    counts = set()
    for drag in drags:
        entry = detect.entry_from_selection(
            "elev_marker", cand.snap(found, drag, dpi=r.dpi), page_index=r.page_index
        )
        results = detect.detect(r, found, [entry])
        counts.add(sum(1 for d in results if d.status is banding.Status.COUNTED))
    # Not identical, and the reason is worth keeping in view. Eight of the nine are single
    # components and every route finds them. The ninth is A/T9, the marker with a line drawn
    # through it, which is assembled from three pieces -- and group growth is bounded by the
    # template's own footprint, so a template 6 px taller (selecting A/T10 rather than C\T9)
    # is enough for that group not to form. Occluded instances sit at the margin by nature.
    assert max(counts) - min(counts) <= 1, counts
    assert 9 in counts, counts


# ------------------------------------------- the selection says what is being counted


def test_identify_recognises_a_marker_and_a_door_from_the_drag_alone(t5, t5_unrepaired) -> None:
    """No second input. What is counted comes from what was selected, and nothing else.

    Each class is recognised on the segmentation IT uses -- doors on unrepaired ink, markers
    on repaired -- which is what the server does once the class is known.
    """
    for fixture, cases in (
        (t5, (((6470, 2870, 62, 148), "elev_marker"),
              ((7300, 2292, 65, 155), "elev_marker"))),
        (t5_unrepaired, (((6360, 2890, 155, 165), "door_swing"),
                         ((4790, 1540, 150, 150), "door_swing"))),
    ):
        r, found = fixture
        for drag, expected in cases:
            symbol, reason = detect.identify(cand.snap(found, drag, dpi=r.dpi), r, found)
            assert symbol.id == expected, (drag, symbol.id, reason)
            assert reason


def test_a_marker_selection_is_never_counted_as_a_door(t5) -> None:
    """The bug this fixes. A dropdown could disagree with the drag: selecting a marker while
    it still said `door_swing` applied the door's 0.80/0.60 thresholds to a triangle, giving
    11 counted instead of 8 and 32 in review instead of 3, every result labelled a door."""
    r, found = t5
    selection = cand.snap(found, (6470, 2870, 62, 148), dpi=r.dpi)

    wrong = classes.get("door_swing")
    mislabelled = detect.detect(
        r, found,
        [detect.entry_from_selection("door_swing", selection, page_index=4, symbol=wrong)],
    )
    assert sum(1 for d in mislabelled if d.status is banding.Status.COUNTED) != 8

    symbol, _ = detect.identify(selection, r, found)
    right = detect.detect(
        r, found, [detect.entry_from_selection(symbol.id, selection, page_index=4, symbol=symbol)]
    )
    counted = [d for d in right if d.status is banding.Status.COUNTED]
    assert symbol.id == "elev_marker"
    assert len(counted) == 9
    assert {d.class_id for d in right} == {"elev_marker"}


def test_an_unregistered_symbol_still_counts_but_says_it_is_unnamed(t5) -> None:
    """A symbol nobody entered must still work -- it just arrives without a name, without a
    caption pattern, and on thresholds no one has calibrated. Saying so is the point."""
    r, found = t5
    selection = cand.snap(found, (6180, 3330, 260, 90), dpi=r.dpi)
    symbol, reason = detect.identify(selection, r, found)
    assert symbol.id not in classes.REGISTRY
    assert symbol.name == "Selected symbol"
    assert "not a symbol registered yet" in reason


def test_identifying_does_not_depend_on_which_instance_was_dragged(t5_unrepaired) -> None:
    r, found = t5_unrepaired
    entry = detect.build_entry(classes.SWING_DOOR, r, found)
    counted = [d for d in detect.detect(r, found, [entry])
               if d.status is banding.Status.COUNTED]
    for d in counted[:6]:
        x, y, w, h = d.bbox_px
        m = int(0.2 * max(w, h))
        sel = cand.snap(found, (x - m, y - m, w + 2 * m, h + 2 * m), dpi=r.dpi)
        assert detect.identify(sel, r, found)[0].id == "door_swing", d.bbox_px


# ---------------------------------------------------------------- the detail marker, on T9

T9 = 8  # the sheet the detail marker is anchored on


@pytest.fixture(scope="module")
def t9() -> tuple[Raster, list[cand.Candidate]]:
    r = render(PDF, T9, dpi=300)
    return r, cand.find_candidates(r, cand.ink_layers(r))


@pytest.fixture(scope="module")
def detail_entry(t9) -> detect.ClassEntry:
    r, found = t9
    return detect.build_entry(classes.DETAIL_MARKER, r, found)


def test_detail_marker_separates_from_everything_else_on_its_sheet(t9, detail_entry) -> None:
    """Five instances on T9, and the best non-marker is 0.34 of score away.

    This is the widest separation of any class here, and the reason is worth writing down:
    the glyph is three features at once -- a circle, a bar across it, and a hatched wedge
    fused to its side -- and nothing else on an architectural sheet carries all three. The
    thresholds were set from this gap rather than from the 0.90/0.80 defaults a class gets
    when it is named in the viewer.

    The test pins the SEPARATION rather than the count, because that is the property that
    would rot quietly. A change that costs 0.05 of score on real markers is invisible in a
    count of five until the day it takes one.
    """
    r, found = t9
    hits = detect.detect(r, found, [detail_entry], keep_rejected=True)
    counted = [d for d in hits if d.status is banding.Status.COUNTED]
    rest = [d for d in hits if d.status is not banding.Status.COUNTED]

    assert len(counted) == 5
    assert min(d.match for d in counted) > 0.97
    assert max(d.match for d in rest) < 0.60
    assert min(d.match for d in counted) - max(d.match for d in rest) > 0.30


def test_the_detail_number_inside_the_circle_does_not_have_to_match(t9, detail_entry) -> None:
    """The template carries a `4`, and the markers it finds carry 4, 7 and 1.

    The reference is drawn INSIDE the glyph -- detail number over sheet number, split by the
    bar -- so unlike every other class here the template cannot be separated from one
    instance's caption. The caption filter takes the two-line sheet number out as a run of
    characters and leaves the single digit in, because a lone character beside a symbol is
    not a caption by any measure the filter has.

    That is tolerable and this test says why: the digit is 210 px of ink against 3,626 for
    the glyph, so an instance with a different number still scores 0.977 or better. If that
    ever stops being true, this fails before a count does.
    """
    r, found = t9
    template_ink = int(detail_entry.template.mask.sum())
    hits = [d for d in detect.detect(r, found, [detail_entry])
            if d.status is banding.Status.COUNTED]

    # The one at (8678, 2047) reads `7 / T11` where the template reads `4 / T12`.
    odd = [d for d in hits if abs(d.bbox_px[0] - 8678) < 20]
    assert odd, "the 7/T11 marker should be found"
    assert odd[0].match > 0.97
    assert template_ink > 3000, "a digit is a small share of this much ink"


def test_the_detail_marker_does_not_take_the_elevation_marker(t5, marker_entry, detail_entry) -> None:
    """Both carry the same hatched wedge, and T5 has elevation markers and no detail ones.

    This is the case the margin gate exists for, so it is worth running the two together
    rather than trusting that a 0.34 gap on one sheet holds on another. Nothing on T5 may be
    counted as a detail marker, and the elevation markers must keep their count.
    """
    r, found = t5
    hits = detect.detect(r, found, [marker_entry, detail_entry])
    assert not [d for d in hits if d.class_id == "detail_marker"]

    markers = [d for d in hits
               if d.class_id == "elev_marker" and d.status is banding.Status.COUNTED]
    assert len(markers) == 9
