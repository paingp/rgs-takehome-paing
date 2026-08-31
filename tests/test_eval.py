"""Ground-truth IO and the grading harness.

These are deliberately synthetic. The harness is the thing that will be used to judge every
future change, so it has to be checked against cases whose right answer is known by
construction -- grading it on real detector output would be circular.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from eval import harness
from server.app import app
from takeoff import banding, schema
from takeoff.detect import Detection


def _truth(*instances, document: str = "doc", page: int = 5) -> schema.GroundTruth:
    return schema.GroundTruth(document=document, page=page, dpi=300, instances=tuple(instances))


def _instance(class_id="m", box=(100, 100, 40, 40), occluded=False, source="reviewed"):
    return schema.TruthInstance(
        class_id=class_id, bbox_px=box, occluded=occluded, source=source
    )


def _detection(class_id="m", box=(100, 100, 40, 40), match=1.0) -> Detection:
    x, y, w, h = box
    return Detection(
        id=f"{class_id}{box}",
        class_id=class_id,
        bbox_px=box,
        centroid_px=(x + w / 2, y + h / 2),
        match=match,
        margin=None,
        status=banding.Status.COUNTED,
        reason=None,
        variant_label="v",
        runner_up=None,
    )


# --------------------------------------------------------------------------- storage


def test_truth_round_trips_through_json() -> None:
    original = _truth(_instance(occluded=True), _instance(box=(500, 500, 30, 30)))
    assert schema.GroundTruth.from_json(original.to_json()) == original


def test_truth_is_keyed_by_document_so_two_drawings_cannot_collide(tmp_path) -> None:
    """Both the bundled PDF and an uploaded scan have a page 1."""
    schema.save_truth(_truth(_instance(), document="aaaaaaaaaaaa", page=1), root=tmp_path)
    schema.save_truth(
        _truth(_instance(), _instance(box=(9, 9, 9, 9)), document="bbbbbbbbbbbb", page=1),
        root=tmp_path,
    )
    assert len(schema.load_truth("aaaaaaaaaaaa", 1, tmp_path).instances) == 1
    assert len(schema.load_truth("bbbbbbbbbbbb", 1, tmp_path).instances) == 2
    assert schema.truth_path("aaaaaaaaaaaa", 1, tmp_path).parent.name == "aaaaaaaaaaaa"


def test_an_unannotated_page_is_none_not_empty(tmp_path) -> None:
    """A page nobody has looked at cannot be scored. A page confirmed to hold nothing scores
    a detector that reports anything. Collapsing the two would silently grade the first."""
    assert schema.load_truth("doc", 5, tmp_path) is None

    schema.save_truth(_truth(document="doc", page=5), root=tmp_path)
    annotated = schema.load_truth("doc", 5, tmp_path)
    assert annotated is not None and annotated.instances == ()


def test_only_reviewed_instances_are_graded(tmp_path) -> None:
    """Decision 6: proposals are never trusted until a human has looked at them."""
    mixed = _truth(_instance(), _instance(box=(700, 700, 40, 40), source="proposed"))
    assert len(mixed.instances) == 2
    assert len(mixed.reviewed.instances) == 1


# --------------------------------------------------------------------------- matching


def test_a_detection_on_the_instance_is_a_true_positive() -> None:
    score = harness.score_class([_detection(box=(102, 101, 40, 40))], _truth(_instance()), "m")
    assert (score.true_positives, score.false_positives, score.false_negatives) == (1, 0, 0)
    assert score.precision == score.recall == score.f1 == 1.0


def test_a_detection_too_far_away_is_not_that_instance() -> None:
    """Tolerance is half the instance's larger dimension -- 20 px for a 40 px symbol."""
    near = harness.score_class([_detection(box=(115, 100, 40, 40))], _truth(_instance()), "m")
    far = harness.score_class([_detection(box=(160, 100, 40, 40))], _truth(_instance()), "m")
    assert near.true_positives == 1
    assert far.true_positives == 0 and far.false_positives == 1 and far.false_negatives == 1


def test_one_detection_cannot_claim_two_instances() -> None:
    truth = _truth(_instance(box=(100, 100, 40, 40)), _instance(box=(130, 100, 40, 40)))
    score = harness.score_class([_detection(box=(115, 100, 40, 40))], truth, "m")
    assert score.true_positives == 1 and score.false_negatives == 1


def test_matching_is_by_distance_not_by_detector_confidence() -> None:
    """Which instance a detection belongs to is a question about geometry. Letting a high
    score claim a far instance would let the detector influence its own grading."""
    truth = _truth(_instance(box=(100, 100, 40, 40)))
    confident_but_far = _detection(box=(118, 100, 40, 40), match=1.0)
    unsure_but_close = _detection(box=(100, 100, 40, 40), match=0.5)
    score = harness.score_class([confident_but_far, unsure_but_close], truth, "m")
    assert score.matched[0].detection is unsure_but_close


def test_classes_are_scored_separately() -> None:
    truth = _truth(_instance(class_id="m"), _instance(class_id="d", box=(500, 500, 40, 40)))
    scores = harness.score_page([_detection(class_id="m")], truth)
    assert set(scores) == {"d", "m"}
    assert scores["m"].true_positives == 1
    assert scores["d"].false_negatives == 1


def test_a_detection_of_the_wrong_class_is_not_a_match() -> None:
    truth = _truth(_instance(class_id="m"))
    scores = harness.score_page([_detection(class_id="d")], truth)
    assert scores["m"].false_negatives == 1
    assert scores["d"].false_positives == 1


# ------------------------------------------------------------- the occluded split


def test_occluded_instances_are_reported_separately() -> None:
    """The number the occlusion work is judged by, and the reason it is split out: on a real
    sheet the occluded ones are a handful among forty, so a whole-page average barely moves
    whether they are all found or all missed."""
    truth = _truth(
        *[_instance(box=(100 * i, 100, 40, 40)) for i in range(1, 9)],           # 8 easy
        _instance(box=(2000, 100, 40, 40), occluded=True),                       # found
        _instance(box=(3000, 100, 40, 40), occluded=True),                       # missed
    )
    detections = [_detection(box=(100 * i, 100, 40, 40)) for i in range(1, 9)]
    detections.append(_detection(box=(2000, 100, 40, 40)))

    score = harness.score_class(detections, truth, "m")
    assert score.recall == pytest.approx(9 / 10)

    occluded = score.restricted_to_occluded()
    assert occluded.true_positives == 1 and occluded.false_negatives == 1
    assert occluded.recall == pytest.approx(0.5), "the whole-page 0.9 hides this"


def test_false_positives_are_not_attributed_to_occluded_instances() -> None:
    score = harness.score_class(
        [_detection(box=(9000, 9000, 40, 40))], _truth(_instance(occluded=True)), "m"
    )
    assert score.false_positives == 1
    assert score.restricted_to_occluded().false_positives == 0


def test_the_table_shows_the_occluded_line_only_when_there_is_one() -> None:
    plain = harness.score_class([], _truth(_instance()), "m")
    assert "occluded only" not in harness.format_table({"m": plain})

    hard = harness.score_class([], _truth(_instance(occluded=True)), "m")
    assert "occluded only" in harness.format_table({"m": hard})


# ------------------------------------------------------------------ the HTTP surface


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


def test_truth_endpoints_round_trip(client: TestClient, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(schema, "GT_ROOT", tmp_path)

    assert client.get("/api/pages/6/truth").json() == {"annotated": False, "instances": []}

    body = {
        "instances": [
            {
                "class_id": "elev_marker",
                "bbox_image_px": [6479, 2879, 44, 129],
                "label": "C\\T9",
                "occluded": True,
            }
        ]
    }
    saved = client.post("/api/pages/6/truth", json=body)
    assert saved.status_code == 200, saved.text
    assert saved.json()["instances"] == 1

    read = client.get("/api/pages/6/truth").json()
    assert read["annotated"] is True
    assert read["instances"][0]["label"] == "C\\T9"
    assert read["instances"][0]["occluded"] is True
    assert read["instances"][0]["source"] == "reviewed"

    stored = json.loads((tmp_path / read["document"] / "page006.json").read_text())
    assert stored["page"] == 6 and stored["dpi"] == 300


# ------------------------------------------------- which centre, and which band


def test_a_hit_is_matched_on_box_centres_not_on_where_its_ink_sits() -> None:
    """The ink centroid of a swing arc is nowhere near the middle of its box.

    Measured on T5: 3-59 px from the box centre against a 66 px tolerance, and the door at
    (9412, 2894) was reported as a false positive AND a miss for that reason alone -- 73 px
    apart on ink centroids, 19 px on box centres. An occluded instance is exactly the case
    where the ink moves and the symbol does not, so grading has to ask where the instance is.
    """
    truth = _truth(_instance(box=(100, 100, 133, 112)))
    on_the_arc = _detection(box=(100, 100, 133, 150))
    object.__setattr__(on_the_arc, "centroid_px", (123.0, 214.0))  # ink, low and left

    score = harness.score_class([on_the_arc], truth, "m")
    assert score.true_positives == 1
    assert score.false_positives == 0 and score.false_negatives == 0


def test_a_review_band_hit_is_neither_a_claim_nor_invisible() -> None:
    """Review means the tool is asking, not asserting.

    Scored as a claim it costs precision the detector never spent; dropped entirely, a
    detector could route every hard case to a person and grade perfectly. So: not graded,
    counted as volume.
    """
    unsure = _detection(box=(500, 500, 40, 40))
    object.__setattr__(unsure, "status", banding.Status.REVIEW)

    score = harness.score_class([unsure, _detection()], _truth(_instance()), "m")
    assert score.true_positives == 1
    assert score.false_positives == 0
    assert score.precision == 1.0
    assert score.review_volume == 1
    assert score.recovered == [], "it landed on nothing, so it recovered nothing"
    assert score.recall_with_review == score.recall


def test_a_review_hit_on_a_missed_instance_is_told_from_one_on_nothing() -> None:
    """The split that makes occlusion work measurable.

    Without it a hard instance deliberately routed to review reads as a false negative AND as
    more review volume -- every number moves the wrong way and the work looks like a
    regression. `recall` still counts only what the tool asserted; `recall_with_review` says
    where it would land if a person confirmed the review hits that found something.
    """
    found_it = _detection(box=(500, 500, 40, 40))            # sits on the occluded instance
    noise = _detection(box=(900, 900, 40, 40))               # sits on nothing
    for d in (found_it, noise):
        object.__setattr__(d, "status", banding.Status.REVIEW)

    truth = _truth(_instance(), _instance(box=(500, 500, 40, 40), occluded=True))
    score = harness.score_class([_detection(), found_it, noise], truth, "m")

    assert score.true_positives == 1 and score.false_negatives == 1
    assert score.recall == 0.5, "recall is unchanged -- review is not a claim"
    assert len(score.recovered) == 1 and len(score.review_spurious) == 1
    assert score.review_volume == 2
    assert score.recall_with_review == 1.0

    # And the occluded split sees it too, which is the whole point of that split.
    occluded = score.restricted_to_occluded()
    assert occluded.recall == 0.0 and occluded.recall_with_review == 1.0


def test_an_instance_found_in_review_is_not_also_reported_as_missing() -> None:
    """The three states a recorded instance can be in must not overlap.

    `missed` is every instance the COUNTED band failed to claim, which is the right
    denominator for `recall` and the wrong thing to put in front of a person: an instance
    found and held for confirmation is in there too. Listing it beside `recovered` named the
    same instance twice, once as lost and once as found, and the list read as a contradiction
    of itself. `not_found` is the subset with nothing pointing at it; `recall` is untouched.
    """
    held = _detection(box=(500, 500, 40, 40))
    object.__setattr__(held, "status", banding.Status.REVIEW)
    truth = _truth(_instance(), _instance(box=(500, 500, 40, 40), occluded=True))

    score = harness.score_class([_detection(), held], truth, "m")
    assert score.false_negatives == 1, "recall still counts it against the counted band"
    assert score.recall == 0.5
    assert len(score.recovered) == 1
    assert score.not_found == [], "but nothing about it is lost"

    # Counted, in review, and not found partition what was recorded.
    present = score.true_positives + score.false_negatives
    assert score.true_positives + len(score.recovered) + len(score.not_found) == present


def test_the_report_lists_only_instances_nothing_points_at() -> None:
    """What `missed` means in a written report, and the count that still means the old thing."""
    from eval import report

    held = _detection(box=(500, 500, 40, 40))
    object.__setattr__(held, "status", banding.Status.REVIEW)
    truth = _truth(_instance(), _instance(box=(500, 500, 40, 40), occluded=True))

    scores = {"m": harness.score_class([_detection(), held], truth, "m")}
    entry = report.payload(scores)["classes"]["m"]
    assert entry["false_negatives"] == 1, "the metric is unchanged"
    assert entry["not_found"] == 0
    assert entry["missed"] == [], "nothing is listed as lost"
    assert len(entry["recovered"]) == 1


def test_an_absent_class_is_gradeable_only_once_someone_has_said_so() -> None:
    """An empty class list is not a zero until a person asserts it."""
    unseen = _truth(_instance(class_id="d"))
    assert unseen.is_reviewed("d") and not unseen.is_reviewed("m")

    asserted = schema.GroundTruth(
        document="doc", page=4, dpi=300,
        instances=(_instance(class_id="d"),),
        reviewed_classes=("m",),
    )
    assert asserted.is_reviewed("m") and not asserted.for_class("m")
    assert asserted.graded_classes == ("d", "m")
    assert schema.GroundTruth.from_json(asserted.to_json()).reviewed_classes == ("m",)


def test_the_report_carries_the_boxes_behind_the_numbers() -> None:
    """A table says a sheet has one false positive; the report says which one.

    Without this the only way to see the box was a throwaway script that re-ran the
    detector -- the contact-sheet loop the harness exists to end.
    """
    from eval import report

    truth = _truth(_instance(box=(100, 100, 40, 40)),
                   _instance(box=(500, 500, 40, 40), occluded=True))
    unsure = _detection(box=(800, 800, 40, 40))
    object.__setattr__(unsure, "status", banding.Status.REVIEW)
    scores = harness.score_page(
        [_detection(box=(102, 101, 40, 40)), _detection(box=(300, 300, 40, 40)), unsure],
        truth,
    )

    payload = report.payload(scores, skipped=["receptacle: not annotated on this page"])
    row = payload["classes"]["m"]
    assert row["true_positives"] == 1 and row["false_positives"] == 1
    assert row["matched"][0]["truth_px"] == [100, 100, 40, 40]
    assert row["matched"][0]["distance_px"] == 2.2
    assert row["spurious"][0]["detection_px"] == [300, 300, 40, 40]
    assert row["missed"][0] == {"truth_px": [500, 500, 40, 40], "occluded": True,
                                "label": None}
    assert row["review_spurious"][0]["detection_px"] == [800, 800, 40, 40]
    assert row["recovered"] == [], "it sits on nothing"
    assert payload["not_graded"] == ["receptacle: not annotated on this page"]


# ------------------------------------------------------- scoring a review, not a detector run


def test_an_accepted_review_hit_is_a_find_not_a_half_find() -> None:
    """The whole point of scoring verdicts instead of bands.

    `score_class` refuses to credit a review-band hit, and that is right when nobody has
    looked: the tool said it did not know, and grading a question as an assertion lets a
    detector buy precision by sending its hard cases to a person. But once somebody HAS looked
    and said yes, the band it came from is a detail of how it got there. Calling it a
    half-find would be reporting the tool's uncertainty rather than the answer.
    """
    truth = _truth(_instance(box=(100, 100, 40, 40)), _instance(box=(300, 300, 40, 40)))
    counted = _detection(box=(100, 100, 40, 40))
    from_review = _detection(box=(300, 300, 40, 40), match=0.82)
    object.__setattr__(from_review, "status", banding.Status.REVIEW)

    bands = harness.score_class([counted, from_review], truth, "m")
    assert bands.true_positives == 1 and bands.recall == 0.5

    review = harness.score_review([counted, from_review], [], truth, "m")
    assert len(review.detected) == 2
    assert review.present == 2
    assert review.recall == 1.0
    assert review.missed == []


def test_a_rejected_match_is_a_false_positive() -> None:
    """The only definition of one a review can produce: the tool proposed it, a person said no."""
    truth = _truth(_instance(box=(100, 100, 40, 40)))
    good = _detection(box=(100, 100, 40, 40))
    bad = _detection(box=(900, 900, 40, 40), match=0.9)

    score = harness.score_review([good], [bad], truth, "m")
    assert score.false_positives == 1
    assert score.recall == 1.0
    assert score.precision == 0.5


def test_an_accepted_match_on_nothing_recorded_is_also_a_false_positive() -> None:
    """Usually empty, because accepting RECORDS the instance -- but it must not be assumed.

    A page can be evaluated against annotations that predate the review, and an accept that
    lands nowhere near any of them is a claim about ink with nothing behind it.
    """
    truth = _truth(_instance(box=(100, 100, 40, 40)))
    score = harness.score_review(
        [_detection(box=(100, 100, 40, 40)), _detection(box=(900, 900, 40, 40))],
        [], truth, "m",
    )
    assert score.false_positives == 1
    assert len(score.detected) == 1


def test_occlusion_is_found_of_recorded_and_carries_no_false_positives() -> None:
    """A false positive sits on no instance, so no instance can say whether it was occluded."""
    truth = _truth(
        _instance(box=(100, 100, 40, 40), occluded=True),
        _instance(box=(300, 300, 40, 40), occluded=True),
        _instance(box=(500, 500, 40, 40)),
    )
    score = harness.score_review(
        [_detection(box=(100, 100, 40, 40)), _detection(box=(500, 500, 40, 40))],
        [_detection(box=(900, 900, 40, 40))],
        truth, "m",
    )
    assert score.occluded_detected == 1
    assert score.occluded_present == 2
    assert score.false_positives == 1


def test_average_precision_reads_the_order_not_just_the_outcome() -> None:
    """Why it is worth reporting beside precision rather than instead of it.

    Both runs below catch every recorded instance and get one thing wrong, so precision and
    recall are identical. They are not equally good: in the second the wrong one outranks the
    right ones, which is the case a score threshold would get wrong, and AP is the only number
    here that can see it.
    """
    truth = _truth(_instance(box=(100, 100, 40, 40)), _instance(box=(300, 300, 40, 40)))
    right = [_detection(box=(100, 100, 40, 40), match=0.99),
             _detection(box=(300, 300, 40, 40), match=0.95)]

    tidy = harness.score_review(right, [_detection(box=(900, 900, 40, 40), match=0.50)],
                                truth, "m")
    messy = harness.score_review(right, [_detection(box=(900, 900, 40, 40), match=1.00)],
                                 truth, "m")

    assert tidy.precision == messy.precision
    assert tidy.recall == messy.recall == 1.0
    assert tidy.average_precision == 1.0
    assert messy.average_precision < 0.85
    assert round(messy.average_precision, 3) == 0.583


def test_average_precision_is_measured_against_the_sheet_not_against_what_was_claimed() -> None:
    """Finding three of forty and being right about all three is not a perfect run."""
    truth = _truth(*[_instance(box=(100 * i, 100, 40, 40)) for i in range(1, 5)])
    score = harness.score_review([_detection(box=(100, 100, 40, 40))], [], truth, "m")
    assert score.precision == 1.0
    assert score.average_precision == 0.25


def test_nothing_recorded_and_nothing_claimed_is_not_a_failure() -> None:
    score = harness.score_review([], [], _truth(), "m")
    assert score.present == 0
    assert score.recall == 1.0 and score.average_precision == 1.0
