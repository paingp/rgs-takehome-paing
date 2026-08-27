"""The text layer, in raster pixels, and the caption it puts beside a glyph.

The join between a detection and its label lives here rather than in detect.py, because
detect.py may never import pymupdf. These tests are the proof that the split still produces
a labelled detection at the far end.
"""

from __future__ import annotations

import pytest

from takeoff import candidates as cand, classes, detect, layout
from takeoff.raster import render

PDF = "Skanksa.pdf"
T5 = 4

# Every interior elevation marker on T5 carries a sheet reference beside it. These are the
# references the text layer holds, keyed by the glyph's bounding box in detection pixels.
T5_MARKER_LABELS = {
    (8636, 1789, 134, 44): "B/T9",
    (4631, 1949, 133, 42): "A/T12",
    (9621, 2498, 134, 44): "C/T10",
    (7502, 2646, 133, 43): "B/T10",
    (7186, 2874, 44, 133): "D\\T9",
    (6479, 2879, 43, 128): "C\\T9",
    (8767, 2302, 47, 134): "E/T10",
}


@pytest.fixture(scope="module")
def t5():
    r = render(PDF, T5, dpi=300)
    return r, cand.find_candidates(r, cand.ink_layers(r))


@pytest.fixture(scope="module")
def words(t5) -> list[layout.Word]:
    r, _ = t5
    return layout.words_px(PDF, T5, r.dpi, r.origin_sheet_pt)


def test_the_text_layer_arrives_in_raster_pixels(words, t5) -> None:
    r, _ = t5
    width, height = r.size_px
    assert len(words) > 100, "expected a text layer on T5"
    for word in words:
        x0, y0, x1, y1 = word.bbox_px
        assert x1 > x0 and y1 > y0
        assert -1 <= x0 and x1 <= width + 1
        assert -1 <= y0 and y1 <= height + 1


def test_no_blank_words_survive(words) -> None:
    assert all(w.text.strip() for w in words)


PATTERN = classes.ELEVATION_MARKER.label_pattern


def test_every_marker_gets_its_sheet_reference(words) -> None:
    """The load-bearing one: each marker resolves to its own reference and no other's."""
    for bbox, expected in T5_MARKER_LABELS.items():
        assert layout.label_for(words, bbox, pattern=PATTERN) == expected, bbox


def test_proximity_alone_is_not_enough(words) -> None:
    """Why the class carries a label pattern at all.

    The marker at (7502, 2646) has the dimension string `4"` nearer its centre than its own
    reference `B/T10`. Nearest-word returns a confidently wrong caption on 1 marker in 7.
    """
    bbox = (7502, 2646, 133, 43)
    assert layout.label_for(words, bbox) == '4"'
    assert layout.label_for(words, bbox, pattern=PATTERN) == "B/T10"


def test_the_pattern_rejects_the_text_that_surrounds_a_marker(words) -> None:
    import re

    matcher = re.compile(PATTERN)
    for noise in ('4"', "GS/GC", "SKANSKA", "CONTRACTOR:", "RM", "EX.OFFICE"):
        assert not matcher.search(noise), noise


def test_labels_are_distinct_so_they_identify_an_instance(words) -> None:
    found = [layout.label_for(words, b, pattern=PATTERN) for b in T5_MARKER_LABELS]
    assert len(set(found)) == len(found), found


def test_words_near_is_ordered_and_bounded(words) -> None:
    bbox = (6479, 2879, 43, 128)
    near = layout.words_near(words, bbox, limit=3)
    assert [w.text for w in near][:1] == ["C\\T9"]
    assert len(near) <= 3

    cx, cy = bbox[0] + bbox[2] / 2, bbox[1] + bbox[3] / 2
    distances = [((w.centre_px[0] - cx) ** 2 + (w.centre_px[1] - cy) ** 2) ** 0.5 for w in near]
    assert distances == sorted(distances)


def test_a_glyph_in_empty_paper_has_no_label(words, t5) -> None:
    r, _ = t5
    width, height = r.size_px
    assert layout.label_for(words, (width - 60, height - 60, 20, 20)) is None


def test_a_pattern_that_matches_nothing_nearby_gives_none_not_a_guess(words) -> None:
    """Better no caption than the wrong one -- the fallback must not leak through."""
    assert layout.label_for(words, (6479, 2879, 43, 128), pattern=r"^ZZZ\d+$") is None


# ------------------------------------------------------- detection carries its metadata


def test_detections_carry_both_halves_of_their_score(t5) -> None:
    """match is min(forward, backward); keeping the halves says which way a miss failed."""
    r, found = t5
    entry = detect.build_entry(classes.ELEVATION_MARKER, r, found)
    results = detect.detect(r, found, [entry], keep_rejected=True)

    for d in results:
        assert d.match == pytest.approx(min(d.forward, d.backward), abs=1e-4)
        assert d.ink_px > 0
        assert d.asymmetry == pytest.approx(abs(d.forward - d.backward), abs=1e-4)

    # The rule earns its keep only if the halves actually disagree somewhere on real ink.
    assert any(d.asymmetry > 0.05 for d in results)


def test_geometry_metadata_is_self_consistent(t5) -> None:
    r, found = t5
    entry = detect.build_entry(classes.ELEVATION_MARKER, r, found)
    for d in detect.detect(r, found, [entry]):
        assert d.size_px == (d.bbox_px[2], d.bbox_px[3])
        assert d.size_in(r.dpi)[0] == pytest.approx(d.bbox_px[2] / r.dpi)
        cx, cy = d.centre_in(r.dpi)
        assert d.bbox_px[0] / r.dpi <= cx <= (d.bbox_px[0] + d.bbox_px[2]) / r.dpi
        assert d.bbox_px[1] / r.dpi <= cy <= (d.bbox_px[1] + d.bbox_px[3]) / r.dpi


def test_counted_markers_are_labelled_and_review_ones_are_not(t5, words) -> None:
    """The two letter `A`s held for review sit in the title block, not beside a reference."""
    r, found = t5
    entry = detect.build_entry(classes.ELEVATION_MARKER, r, found)
    results = detect.detect(r, found, [entry])

    counted = [d for d in results if d.status.value == "counted"]
    labels = {layout.label_for(words, d.bbox_px, pattern=PATTERN) for d in counted}
    assert set(T5_MARKER_LABELS.values()) <= labels
    assert "A/T10" in labels, "the split marker must be reassembled and labelled"

    # No physical marker may be counted twice: one label, one instance.
    found = [layout.label_for(words, d.bbox_px, pattern=PATTERN) for d in counted]
    assert len(found) == len(set(found)), found

    review = [d for d in results if d.status.value == "review"]
    assert review, "expected the near misses to still be present"

    # Review holds two different kinds of thing, and both belong there. The letter `A` in
    # the title block, twice, with no reference anywhere near it -- and B/T12, a real marker
    # that line suppression left in three pieces and that reassembles to only 0.820. Neither
    # should be counted; neither should be silently dropped.
    review_labels = {layout.label_for(words, d.bbox_px, pattern=PATTERN) for d in review}
    assert None in review_labels, "the title-block letters must have no reference"
    assert "B/T12" in review_labels, "a fragmented real marker must surface, not vanish"
    assert not (review_labels - {None}) & labels, "nothing may be both counted and in review"
