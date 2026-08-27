"""The parametric arc detector, and a door counted end to end on T5.

The synthetic shapes exist because the gates have to be shown to do something. A door arc
and a grid bubble are both circles; only the span and occupancy tests separate them, and on
real ink they mostly agree with a naive detector, which would let those gates rot.
"""

from __future__ import annotations

import numpy as np
import pytest

from takeoff import banding, candidates as cand, classes, detect, doors
from takeoff.raster import render

PDF = "Skanksa.pdf"
T5 = 4
DPI = 300


# ------------------------------------------------------------------------ synthetic ink


def _draw(fn, size=300) -> tuple[np.ndarray, tuple[int, int, int, int]]:
    """Rasterise a shape, then crop to its ink the way find_candidates would."""
    canvas = np.zeros((size, size), bool)
    fn(canvas)
    ys, xs = np.nonzero(canvas)
    y0, y1, x0, x1 = ys.min(), ys.max(), xs.min(), xs.max()
    return canvas[y0 : y1 + 1, x0 : x1 + 1], (int(x0), int(y0), int(x1 - x0 + 1), int(y1 - y0 + 1))


def _arc_shape(cx, cy, radius, start, end, thickness=2):
    def draw(canvas):
        for deg in np.arange(start, end, 0.25):
            for t in np.arange(-thickness / 2, thickness / 2, 0.5):
                r = radius + t
                x = int(round(cx + r * np.cos(np.radians(deg))))
                y = int(round(cy + r * np.sin(np.radians(deg))))
                if 0 <= x < canvas.shape[1] and 0 <= y < canvas.shape[0]:
                    canvas[y, x] = True
    return draw


def test_a_quarter_circle_is_found_with_its_radius_and_centre() -> None:
    mask, bbox = _draw(_arc_shape(150, 150, 110, 180, 270))
    arc = doors.find_arc(mask, bbox, DPI)
    assert arc is not None
    assert arc.radius_px == pytest.approx(110, abs=4)
    assert arc.centre_px[0] == pytest.approx(150, abs=4)
    assert arc.centre_px[1] == pytest.approx(150, abs=4)
    assert 80 <= arc.span_deg <= 100
    assert arc.occupancy > 0.95


def test_a_full_circle_is_rejected_because_a_swing_is_not_one() -> None:
    """The grid bubbles on T5 are circles of about a door's radius. This is the gate that
    keeps them out, and it is the only one that does."""
    mask, bbox = _draw(_arc_shape(150, 150, 110, 0, 360))
    assert doors.find_arc(mask, bbox, DPI) is None


def test_a_filled_disc_is_not_a_ring_but_is_still_no_swing() -> None:
    """Both halves of the ring test earn their keep: a disc spans 360 degrees like a bubble
    but its radial scatter is wide, so it is refused by the sweep rather than by the ring
    check -- and it must still not be counted."""
    def draw(canvas):
        yy, xx = np.mgrid[0:canvas.shape[0], 0:canvas.shape[1]]
        canvas[((yy - 150) ** 2 + (xx - 150) ** 2) <= 110**2] = True

    mask, bbox = _draw(draw)
    assert not doors.is_closed_ring(mask)
    assert not doors.is_swing(doors.find_arc(mask, bbox, DPI), bbox)


def test_a_ring_is_recognised_directly() -> None:
    mask, _ = _draw(_arc_shape(150, 150, 110, 0, 360))
    assert doors.is_closed_ring(mask)


def test_a_straight_line_is_rejected() -> None:
    def draw(canvas):
        canvas[150:153, 40:260] = True

    mask, bbox = _draw(draw)
    assert doors.find_arc(mask, bbox, DPI) is None


def test_two_opposite_stubs_do_not_pass_as_a_wide_arc() -> None:
    """Span alone would accept this: the widest gap is small in one direction. Occupancy is
    what refuses it."""
    def draw(canvas):
        _arc_shape(150, 150, 110, 0, 25)(canvas)
        _arc_shape(150, 150, 110, 115, 140)(canvas)

    mask, bbox = _draw(draw)
    arc = doors.find_arc(mask, bbox, DPI)
    assert arc is None or arc.occupancy < doors.MIN_OCCUPANCY


def test_a_radius_outside_the_band_is_not_reported() -> None:
    """`find_arc` on its own is permissive: told to look for a small circle it will find one
    somewhere in a big arc's ink, seen from far enough off-centre. `is_swing` is the gate
    that ties the radius back to the blob's own size, so the pair is what gets asserted."""
    mask, bbox = _draw(_arc_shape(150, 150, 110, 180, 270))
    arc = doors.find_arc(mask, bbox, DPI, radius_band_in=(0.05, 0.15))
    assert not doors.is_swing(arc, bbox)


def test_the_sweep_is_deterministic() -> None:
    """Why this is a grid sweep and not RANSAC: decision 10 needs ids stable across re-runs,
    and the spike's random sampling agreed with itself on only 82% of the set."""
    mask, bbox = _draw(_arc_shape(150, 150, 110, 180, 270))
    first = doors.find_arc(mask, bbox, DPI)
    for _ in range(3):
        again = doors.find_arc(mask, bbox, DPI)
        assert (again.centre_px, again.radius_px, again.inliers) == (
            first.centre_px, first.radius_px, first.inliers,
        )


def test_width_in_feet_reads_off_the_plan_scale() -> None:
    mask, bbox = _draw(_arc_shape(150, 150, 112, 180, 270))
    arc = doors.find_arc(mask, bbox, DPI)
    assert arc.width_ft(DPI, plan_scale=8.0) == pytest.approx(3.0, abs=0.2)


# ------------------------------------------------------------------- the class on real ink


# Doors are counted on UNREPAIRED ink: the class turns repair off, because restoring the
# jamb beside a swing thickens the curve the sweep measures. The fixtures mirror what the
# server does, so the tests describe what a person actually gets.
DOOR_REPAIR = classes.SWING_DOOR.repair_gap_px


@pytest.fixture(scope="module")
def t5():
    r = render(PDF, T5, dpi=DPI)
    return r, cand.find_candidates(r, cand.ink_layers(r, repair_gap_px=DOOR_REPAIR))


@pytest.fixture(scope="module")
def door_detections(t5):
    r, found = t5
    entry = detect.build_entry(classes.SWING_DOOR, r, found)
    return r, found, entry, detect.detect(r, found, [entry], keep_rejected=True)


def test_the_detector_is_measured_from_the_selection_not_declared(t5) -> None:
    """The door class names no detector and pins no radius. Both come from the selection.

    That is what lets a symbol nobody anticipated work: a person drags a box, the tool
    measures what is in it, and the measurement picks the method.
    """
    assert classes.SWING_DOOR.detector == "auto"
    assert classes.SWING_DOOR.radius_band_in is None

    r, found = t5
    entry = detect.build_entry(classes.SWING_DOOR, r, found)
    assert entry.detector == "arc" and entry.is_arc
    assert entry.template is None and entry.bank == []
    assert entry.profile is not None and "arc" in entry.profile.reason

    lo, hi = entry.radius_band_in
    assert 0.2 < lo < hi < 0.6, (lo, hi)


def test_doors_counted_on_t5(door_detections) -> None:
    """27 swings clear the gate. Not ground truth -- T5's annotations are with Paing -- so
    what this pins is the SEPARATION, which is the property that would silently rot."""
    _, _, _, dets = door_detections
    counted = [d for d in dets if d.status is banding.Status.COUNTED]
    assert len(counted) == 29, [(d.match, d.bbox_px) for d in counted]
    assert all(d.match >= classes.SWING_DOOR.counted_at for d in counted)

    others = [d for d in dets if d.status is not banding.Status.COUNTED]
    assert others, "the sweep should still surface some near misses"
    assert max(d.match for d in others) < 0.55, "the counted band must stay clear of the rest"


def test_counted_doors_are_all_a_plausible_width(door_detections) -> None:
    _, _, entry, dets = door_detections
    lo, hi = entry.radius_band_in
    assert lo < hi
    widths = [float(d.variant_label.split("@r")[1].rstrip("ft")) for d in dets
              if d.status is banding.Status.COUNTED]
    assert min(widths) >= 2.0 and max(widths) <= 4.0, (min(widths), max(widths))


def test_a_door_reports_which_way_it_failed(door_detections) -> None:
    """occupancy and share are carried as the two halves, same as a template's coverage:
    a broken arc and a small fragment of a big blob fail differently."""
    _, _, _, dets = door_detections
    assert all(0.0 <= d.forward <= 1.0 and 0.0 <= d.backward <= 1.0 for d in dets)
    assert any(d.backward < 0.5 for d in dets), "expected some arcs merged into bigger blobs"


def test_detection_ids_are_stable_across_runs(door_detections) -> None:
    r, found, entry, dets = door_detections
    again = detect.detect(r, found, [entry], keep_rejected=True)
    assert [d.id for d in dets] == [d.id for d in again]


def test_diagnose_speaks_arc_not_template(t5) -> None:
    r, found = t5
    entry = detect.build_entry(classes.SWING_DOOR, r, found)
    note = detect.diagnose(r, found, entry, [])["note"]
    assert note and "swept for a circle" in note


# ------------------------------------------------------------ two classes, side by side


def test_registering_a_second_class_changes_neither_count(t5) -> None:
    """The property the gated build is meant to test: adding a symbol does not move the
    symbol that was already there."""
    r, found = t5
    entries = [detect.build_entry(c, r, found) for c in classes.all_classes()]
    assert {e.symbol.id for e in entries} == {"door_swing", "elev_marker"}

    together = detect.detect(r, found, entries)
    counts = {
        cid: sum(1 for d in together if d.class_id == cid and d.status is banding.Status.COUNTED)
        for cid in ("door_swing", "elev_marker")
    }
    assert counts == {"door_swing": 29, "elev_marker": 8}


def test_the_margin_gate_is_still_unexercised_and_says_so(t5) -> None:
    """Two classes are registered now, but they are not confusable: a hatched triangle and a
    thin arc never claim the same ink, so no detection has a runner-up. The nested-symbol
    problem stays untested until two classes actually compete -- record that, do not imply
    the gate has been proven."""
    r, found = t5
    entries = [detect.build_entry(c, r, found) for c in classes.all_classes()]
    dets = detect.detect(r, found, entries)
    assert all(d.margin is None for d in dets)
    assert all(d.runner_up is None for d in dets)


# ---------------------------------------------------- type code as an attribute, not a class


def test_door_type_codes_are_read_where_the_drawing_gives_one(door_detections) -> None:
    """The scoping decision, made concrete: one door class, type as a per-detection field.

    Seven legend entries collapse to three shapes; the rest of the difference is lifecycle
    and a keynote code. Most doors carry no code at all -- 11 bubbles against 27 doors on
    T5 -- so a class per legend entry would have nothing to read on most instances.
    """
    from takeoff import layout

    r, _, _, dets = door_detections
    words = layout.words_px(PDF, T5, r.dpi, r.origin_sheet_pt)
    pattern = classes.SWING_DOOR.label_pattern
    counted = [d for d in dets if d.status is banding.Status.COUNTED]

    labels = [layout.label_for(words, d.bbox_px, pattern=pattern) for d in counted]
    assert sum(label == "EX" for label in labels) >= 20, "most doors here are existing"
    assert "WS/PA" in labels, "the new standard door must be distinguishable"
    assert any(label is None for label in labels), "a door with no code must report None"


def test_the_type_pattern_refuses_a_finish_keynote(door_detections) -> None:
    """`GS/GC` is a finish code, not a door type, and two of them sit inside a door's swing
    on T5. A bare two-by-two letter pattern reported them as the door's type."""
    import re

    matcher = re.compile(classes.SWING_DOOR.label_pattern)
    for code in ("EX", "EX.", "EX/PA", "WS/PA", "WB/BF", "WD/DL", "WD/DF", "RE/EX"):
        assert matcher.search(code), code
    for other in ("GS/GC", "WV/PP", "RM", "TV", "PA"):
        assert not matcher.search(other), other


# ------------------------------------------------- one gesture, whatever the symbol is


def test_every_door_on_the_sheet_profiles_as_an_arc(t5) -> None:
    """A person may drag round ANY door, not the one the registry happens to anchor on.

    This is what caught the selection bug behind the whole feature: a door's arc carries
    less ink than the `EX` beside it, so ranking blobs by ink alone made the letter the
    primary, the arc lost its protection, and the text filter deleted the symbol. 7 of 27
    doors were unselectable and the failure looked like a detector problem.
    """
    r, found = t5
    entry = detect.build_entry(classes.SWING_DOOR, r, found)
    counted = [d for d in detect.detect(r, found, [entry])
               if d.status is banding.Status.COUNTED]
    assert len(counted) == 29

    rng = np.random.default_rng(0)
    verdicts = []
    for d in counted:
        x, y, w, h = d.bbox_px
        m = int(0.22 * max(w, h))
        jx, jy = rng.integers(-m // 2, m // 2 + 1, 2)
        sel = cand.snap(found, (x - m + int(jx), y - m + int(jy), w + 2 * m, h + 2 * m),
                        dpi=r.dpi)
        assert not sel.is_empty
        verdicts.append(detect.profile_selection(sel, r.dpi).detector)
    assert verdicts.count("arc") == len(counted), verdicts


def test_a_shape_never_profiles_as_a_curve(t5) -> None:
    """The other direction. A circle can be fitted through the elevation marker's diagonals,
    so density is what refuses it: a hatched triangle fills 24% of its box."""
    r, found = t5
    entry = detect.build_entry(classes.ELEVATION_MARKER, r, found)
    counted = [d for d in detect.detect(r, found, [entry])
               if d.status is banding.Status.COUNTED]
    assert len(counted) == 8

    rng = np.random.default_rng(1)
    for d in counted:
        x, y, w, h = d.bbox_px
        m = int(0.22 * max(w, h))
        jx, jy = rng.integers(-m // 2, m // 2 + 1, 2)
        sel = cand.snap(found, (x - m + int(jx), y - m + int(jy), w + 2 * m, h + 2 * m),
                        dpi=r.dpi)
        assert detect.profile_selection(sel, r.dpi).detector == "template", d.bbox_px


def test_the_radius_band_follows_the_door_that_was_selected(t5) -> None:
    """Nothing about door width is hard-coded, so a set drawn at another scale needs no edit."""
    r, found = t5
    entry = detect.build_entry(classes.SWING_DOOR, r, found)
    measured = entry.profile.arc.radius_px / r.dpi
    lo, hi = entry.radius_band_in
    assert lo < measured < hi
    assert lo == pytest.approx(measured * (1 - detect.RADIUS_TOLERANCE), abs=1e-3)
    assert hi == pytest.approx(measured * (1 + detect.RADIUS_TOLERANCE), abs=1e-3)


def test_selecting_any_door_gives_the_same_count(t5) -> None:
    """Consistency the other way round: which instance was dragged must not change the answer.
    This is exactly what the template path could not do -- there, recall ran 0%-68% on it."""
    r, found = t5
    entry = detect.build_entry(classes.SWING_DOOR, r, found)
    counted = [d for d in detect.detect(r, found, [entry])
               if d.status is banding.Status.COUNTED]

    totals = set()
    for d in counted[:6]:
        x, y, w, h = d.bbox_px
        m = int(0.2 * max(w, h))
        sel = cand.snap(found, (x - m, y - m, w + 2 * m, h + 2 * m), dpi=r.dpi)
        e = detect.entry_from_selection("door_swing", sel, page_index=4)
        totals.add(sum(1 for x2 in detect.detect(r, found, [e])
                       if x2.status is banding.Status.COUNTED))
    assert len(totals) == 1, totals


# ------------------------------------------------------ the door to room 217, specifically

# Bottom-right of the plan. Its swing is flawless -- a continuous 95-degree arc at 3.1 ft --
# but a wall jamb shares its connected component, so 1,059 of its 1,357 ink pixels are not on
# the arc. Every quality measure that looked at the whole blob scored it 0.47 and rejected it.
T5_ROOM_217_DOOR = (9412, 2894, 133, 150)


def test_the_room_217_door_is_counted(door_detections) -> None:
    _, _, _, dets = door_detections
    hit = next((d for d in dets if d.bbox_px == T5_ROOM_217_DOOR), None)
    assert hit is not None, "the sweep must at least find it"
    assert hit.status is banding.Status.COUNTED, f"scored {hit.match}"


def test_that_door_is_judged_on_its_arc_not_on_its_blob(t5) -> None:
    """Why it was being lost. The arc is perfect; the blob it lives in is not its fault."""
    r, found = t5
    c = next(x for x in found if x.bbox_px == T5_ROOM_217_DOOR)
    arc = doors.find_arc(c.mask, c.bbox_px, r.dpi, (0.20, 0.60))

    assert arc.occupancy == pytest.approx(1.0), "the swing is continuous"
    assert 85 <= arc.span_deg <= 105, "and sweeps a quadrant"
    assert arc.share < 0.3, "yet the arc is under a third of the blob's ink"
    assert arc.quality >= 0.7, "quality must not follow that share down"


def test_quality_ignores_ink_that_is_not_on_the_arc(t5) -> None:
    """The property, stated directly: adding unrelated ink to a blob must not move the score
    of an arc that is already in it."""
    r, found = t5
    c = next(x for x in found if x.bbox_px == (6395, 2915, 108, 112))
    clean = doors.find_arc(c.mask, c.bbox_px, r.dpi, (0.20, 0.60))

    # A block of ink well inside the swing, touching nothing the arc uses.
    dirty = c.mask.copy()
    dirty[70:100, 10:40] = True
    fouled = doors.find_arc(dirty, c.bbox_px, r.dpi, (0.20, 0.60))

    assert fouled is not None
    assert fouled.share < clean.share, "the arc really is a smaller part of the blob now"
    assert fouled.quality == pytest.approx(clean.quality, abs=0.02), "but it scores the same"


# ------------------------------------------------------- T4: the adversarial sheet
#
# Three plan viewports at three scales, and full of furniture. An office chair's back is a
# continuous quarter-circle, one stroke wide, at very nearly a door's radius -- on geometry
# alone it is a perfect door swing, and 17 of them were counted as such.

T4 = 3
T4_DOOR_DRAG = (7970, 1530, 160, 165)
T4_CHAIR = (8584, 1894, 90, 72)
T4_DOOR = (4530, 2133, 110, 110)


@pytest.fixture(scope="module")
def t4():
    r = render(PDF, T4, dpi=DPI)
    found = cand.find_candidates(r, cand.ink_layers(r, repair_gap_px=DOOR_REPAIR))
    return r, found, doors.page_ink_from(r.gray)


def test_a_chair_back_is_geometrically_a_perfect_door(t4) -> None:
    """The premise. If a chair were an obviously bad arc, none of this would be needed."""
    r, found, ink = t4
    chair = next(c for c in found if c.bbox_px == T4_CHAIR)
    arc = doors.find_arc(chair.mask, chair.bbox_px, r.dpi, (0.26, 0.54), ink)

    assert arc is not None
    assert doors.is_swing(arc, chair.bbox_px, require_anchor=False), "geometry says door"
    assert arc.quality >= 0.7, "and it scores like one"


def test_what_separates_them_is_whether_the_curve_pivots_on_ink(t4) -> None:
    """A door swings about its hinge, which is a drawn jamb. A chair back curves about the
    middle of a seat, which is empty."""
    r, found, ink = t4
    chair = next(c for c in found if c.bbox_px == T4_CHAIR)
    door = next(c for c in found if c.bbox_px == T4_DOOR)

    chair_arc = doors.find_arc(chair.mask, chair.bbox_px, r.dpi, (0.26, 0.54), ink)
    door_arc = doors.find_arc(door.mask, door.bbox_px, r.dpi, (0.26, 0.54), ink)

    assert chair_arc.anchor_ink < doors.ANCHOR_FRACTION
    assert door_arc.anchor_ink > doors.ANCHOR_FRACTION
    assert not doors.is_swing(chair_arc, chair.bbox_px, require_anchor=True)
    assert doors.is_swing(door_arc, door.bbox_px, require_anchor=True)


def test_the_anchor_test_is_read_off_the_selection_not_hard_coded(t4) -> None:
    """Selecting a door means matches must pivot too. Nothing here knows what a door is."""
    r, found, ink = t4
    profile = detect.profile_selection(
        cand.snap(found, T4_DOOR_DRAG, dpi=r.dpi), r.dpi, ink
    )
    assert profile.detector == "arc"
    assert profile.anchored is True
    assert "pivoting on drawn ink" in profile.reason


def test_no_chair_is_counted_as_a_door_on_t4(t4) -> None:
    r, found, ink = t4
    selection = cand.snap(found, T4_DOOR_DRAG, dpi=r.dpi)
    symbol, _ = detect.identify(selection, r, found)
    entry = detect.entry_from_selection(
        symbol.id, selection, page_index=T4, symbol=symbol, page_ink=ink
    )
    counted = [
        d for d in detect.detect(r, found, [entry]) if d.status is banding.Status.COUNTED
    ]
    assert 20 <= len(counted) <= 30, len(counted)
    assert not any(d.bbox_px == T4_CHAIR for d in counted)

    # `require_anchor` defaults to None, meaning "the selection did not say" -- and then the
    # test is skipped. Easy to leave out, and leaving it out in the server was exactly the
    # bug: the chairs came straight back. Both readings are asserted here on purpose.
    with_anchor = {
        c.bbox_px
        for c, _ in doors.swings_in(list(found), r.dpi, entry.radius_band_in, ink, True)
    }
    without = {
        c.bbox_px
        for c, _ in doors.swings_in(list(found), r.dpi, entry.radius_band_in, ink, None)
    }
    assert T4_CHAIR not in with_anchor
    assert T4_CHAIR in without, "without the anchor test a chair is indistinguishable"


def test_a_registered_symbol_is_recognised_on_a_sheet_it_is_not_anchored_to(t4) -> None:
    """Identification used to be impossible off the anchor sheet.

    `identify` rebuilt each class on the CURRENT page, and every class is anchored on T5, so
    `build_entry` raised and every class was skipped. Every door on T4 came back "not a
    symbol registered yet" -- losing its name, its caption pattern, and the thresholds that
    had been calibrated for it.
    """
    r, found, _ = t4
    selection = cand.snap(found, T4_DOOR_DRAG, dpi=r.dpi)

    # Without references, nothing anchored elsewhere can be recognised.
    bare, _ = detect.identify(selection, r, found, references={})
    assert bare.id not in classes.REGISTRY

    # With one, it is.
    reference = render(PDF, classes.SWING_DOOR.anchor.page_index, dpi=DPI)
    ref_found = cand.find_candidates(
        reference, cand.ink_layers(reference, repair_gap_px=DOOR_REPAIR)
    )
    library = {"door_swing": detect.build_entry(classes.SWING_DOOR, reference, ref_found)}

    symbol, reason = detect.identify(selection, r, found, references=library)
    assert symbol.id == "door_swing", reason
    assert symbol.counted_at == classes.SWING_DOOR.counted_at
