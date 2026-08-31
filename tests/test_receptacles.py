"""Gate 6: the duplex receptacle, and the two things it broke on the way in.

Neither was a threshold. Both were assumptions the pipeline had absorbed from two
architectural sheets, and this is the first electrical one:

  * ink is nearly black -- the receptacle's MEDIAN pixel is 232 against a cut at 230
  * a plan viewport does not look like set type -- E4's plan is drawn screened, and the
    height-uniformity classifier reads all 50 MP of it as a notes column

E4 is annotated now, so accuracy lives in `-m eval.suites --page 26` and not here. What these
counts pin is SEPARATION -- that the shoulder at 0.95 is still where it was measured. The
graded truth is 95 TP / 0 FP / 37 FN, precision 1.000, with 17 more instances found and held
for confirmation; recall is the open problem and it is a recall of things drawn touching other
geometry, not a threshold.
"""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from takeoff import banding
from takeoff import candidates as cand
from takeoff import classes, detect, regions, scoring
from takeoff.raster import render

PDF = "Skanksa.pdf"
E4 = 25
DPI = 300

# One duplex, the one scratch/spike.py measured its 92 against.
GLYPH = (2469, 1069, 47, 31)


@pytest.fixture(scope="module")
def e4():
    r = render(PDF, E4, dpi=DPI)
    symbol = classes.get("receptacle_duplex")
    found = cand.find_candidates(r, cand.ink_layers(r, ink_threshold=symbol.ink_threshold))
    return r, found, symbol


def test_the_glyph_is_nine_fragments_at_the_default_ink_threshold() -> None:
    """Why this class carries `ink_threshold` at all.

    Electrical devices are drawn on a thin CAD layer. At the global cut the duplex is not a
    faint symbol, it is nine separate specks of 11-18 px, and `from_selection` keeps the
    largest -- a template that size matched 2,312 things on the sheet.
    """
    r = render(PDF, E4, dpi=DPI)
    x, y, w, h = GLYPH
    pieces = {}
    for cut in (cand.INK_THRESHOLD, 15):
        ink = ((255 - r.gray[y:y + h, x:x + w]) > cut).astype(np.uint8)
        count, _, stats, _ = cv2.connectedComponentsWithStats(ink, 8)
        pieces[cut] = (count - 1, max(int(s[4]) for s in stats[1:]))

    assert pieces[cand.INK_THRESHOLD][0] >= 8, "the default used to shatter it"
    assert pieces[cand.INK_THRESHOLD][1] < 25, "and left nothing big enough to be a template"
    assert pieces[15] == (1, pytest.approx(176, abs=15)), "its own threshold makes it whole"


def test_the_default_threshold_is_not_simply_wrong() -> None:
    """It is right where it was measured, which is why this is a per-class knob.

    Lowering it globally to 15 costs two doors and a marker on T5: the extra faint ink merges
    neighbouring components. Ink coverage barely moves and the candidate count even falls, so
    neither is a proxy for accuracy -- only the harness is.
    """
    assert cand.INK_THRESHOLD == 25
    assert classes.get("door_swing").ink_threshold is None
    assert classes.get("elev_marker").ink_threshold is None
    assert classes.get("receptacle_duplex").ink_threshold == 15


def test_the_region_gate_refuses_to_delete_a_sheet(e4) -> None:
    """The gate is an optimisation, and it fails safe.

    On T4 and T5 it removes 47% and 14% of the candidates and changes no count. On E4 it
    would remove 91% -- the whole plan, every receptacle on it -- because the classifier has
    misread a screened electrical background as set type. A segmentation that has misread a
    drawing does not announce itself; it just returns fewer candidates and a class comes back
    as an honest-looking zero.
    """
    r, found, _ = e4
    segs = regions.segment(r, found)
    plan = max(segs, key=lambda g: g.area_px)
    assert plan.kind == regions.TEXT, "the misclassification this guard exists for"

    kept = regions.countable(segs, found)
    assert len(kept) == len(found), "past the limit, the gate stands down entirely"
    assert any(c.bbox_px == GLYPH for c in kept), "the anchor glyph survives the gate"


def test_a_duplex_receptacle_counts_on_e4(e4) -> None:
    """End to end, the way the server runs it: build from the anchor, count the sheet.

    95 counted, against 92 from scratch/spike.py's NCC over the same plan and 90 from vector
    motif clustering. Three methods that share no code agree within five.

    It was 96 until fused instances were capped at review: one of them was a receptacle found
    inside a larger blob and counted outright. E4 is now graded -- 95 TP / 0 FP / 37 FN with
    17 more found and held for confirmation -- so this test no longer carries the accuracy
    claim. What it pins is that the whole path still runs from an anchor drag to a count.
    """
    r, found, symbol = e4
    entry = detect.build_entry(symbol, r, found)
    assert entry.detector == "template"
    assert entry.template.trimmed is False, "the drag takes the whole glyph, not a fragment"
    assert entry.template.context_blobs == 0

    dets = detect.detect(r, found, [entry], regions=regions.segment(r, found))
    counted = [d for d in dets if d.status is banding.Status.COUNTED]
    assert len(counted) == 95
    assert all(d.match >= symbol.counted_at for d in counted)
    assert not any(d.fused for d in counted), "a fused instance is never counted outright"


def test_a_drag_sees_ink_too_small_to_be_a_candidate_anywhere_else() -> None:
    """The sheet-wide size floor is about the sheet, not about a box someone drew.

    `find_candidates` drops anything under 0.027 in -- 8 px at 300 DPI -- because a sheet
    holds millions of specks. At the default ink cut this receptacle is nine fragments and
    FIVE of them are under that floor, so they were not merely dropped from the selection,
    they could not be reached: not set aside, not clickable, gone. Inside an explicit drag
    there is no pool to bound, and the person has already said where the symbol is.
    """
    r = render(PDF, E4, dpi=DPI)
    layers = cand.ink_layers(r)                      # the DEFAULT cut, which shatters it
    x, y, w, h = GLYPH
    drag = (x - 8, y - 8, w + 16, h + 16)

    coarse = [c for c in cand.find_candidates(r, layers)
              if cand._inside_fraction(c, drag) >= cand.INSIDE_FRACTION]
    fine = cand.fine_candidates(r, drag, layers=layers)

    assert fine, "there is sub-band ink in this box"
    floor = cand.SYMBOL_BAND_IN[0] * r.dpi
    assert all(max(c.bbox_px[2], c.bbox_px[3]) < floor for c in fine), "only the small ones"
    assert not ({c.id for c in fine} & {c.id for c in coarse}), "and never a duplicate"

    # Offered, never assumed. Sub-band ink is mostly the frayed edge of a stroke, and folding
    # it into the template silently changes every registered class -- on T5 it cost the
    # elevation marker a count. So it arrives switched off, and a person decides.
    whole = cand.snap(cand.find_candidates(r, layers), drag, dpi=r.dpi, fine=fine)
    assert len(whole.members) == len(coarse), "the template is unchanged"
    assert len(whole.set_aside) >= len(fine), "and every speck is on the screen to click"

    restored = whole.plus(range(len(whole.set_aside)))
    assert len(restored.members) > len(whole.members), "one click brings them in"


def test_the_reference_instance_scores_perfectly_against_its_own_template(e4) -> None:
    """The sanity check that caught the region gate.

    The glyph the template was built from must score 1.000 -- and it did, while being absent
    from the sheet's detections entirely, because the gate had removed it from the pool.
    """
    r, found, symbol = e4
    entry = detect.build_entry(symbol, r, found)
    glyph = next(c for c in found if c.bbox_px == GLYPH)

    score = scoring.best_variant(glyph.mask, entry.bank, r.dpi, scoring.StrokeCoverageScorer())
    assert score.match == pytest.approx(1.0, abs=1e-6)

    dets = detect.detect(r, found, [entry], regions=regions.segment(r, found))
    assert any(d.bbox_px == GLYPH for d in dets), "and it must reach the count"
