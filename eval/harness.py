"""Matching and metrics.

Matching is by centre distance within half a symbol width, not IoU.
The only place detector output and vector ground truth meet.

WHY NOT IoU. The symbols this tool most needs measuring are the occluded ones, and a partly
occluded instance has a poor IoU with its own truth box while still being unmistakably the
same instance -- a line drawn across a marker changes where its ink ends. Centre distance
asks the question that matters, "is the tool pointing at this thing", and a tolerance of half
a symbol width is tight enough that two neighbouring instances can never be confused.

WHICH CENTRE. Both sides use the middle of the box. A detection also carries `centroid_px`,
the ink-weighted centre of its components, and grading against that measures where the ink
happens to sit rather than where the instance is -- on a swing arc the two are up to 59 px
apart against a 66 px tolerance, and one T5 door was reported as a false positive AND a miss
at the same coordinates. An occluded instance is exactly the case where ink moves and the
symbol does not, so this is not a detail: it is the same argument as WHY NOT IoU.

WHAT COUNTS AS A CLAIM. Only the counting band. A review-band hit is the tool saying it does
not know, which is the honest answer and must not be scored as if it were an assertion --
grading the two together made the T5 markers read 0.769 precision when the counted band was
0.000 wrong. Review volume is reported beside the metrics instead, because a detector that
sends everything to review has not solved anything either.

WHAT IS REPORTED. Precision, recall and F1 per class, and the same again for **occluded
instances only**. The occluded split is the point: on T5 they are a handful of instances among
forty, so a whole-sheet average moves by about two points whether they are all found or all
missed, and that is exactly the change occlusion work is trying to make visible.

Raster-only module: must never import pymupdf, directly or transitively.
Enforced by tests/test_raster_only.py.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Sequence

from takeoff.banding import Status
from takeoff.detect import Detection
from takeoff.schema import GroundTruth, TruthInstance

# How far a detection's centre may sit from a truth instance's centre and still be it, as a
# fraction of that instance's larger dimension.
MATCH_DISTANCE_FACTOR = 0.5


@dataclass(frozen=True)
class Match:
    """One truth instance and the detection claimed for it, if any."""

    truth: TruthInstance
    detection: Detection | None
    distance_px: float | None = None

    @property
    def found(self) -> bool:
        return self.detection is not None


@dataclass
class Score:
    """How a detector did on one class, on one page."""

    class_id: str
    matched: list[Match] = field(default_factory=list)
    missed: list[TruthInstance] = field(default_factory=list)
    spurious: list[Detection] = field(default_factory=list)

    # Hits the tool sent to a person instead of claiming. Not graded either way: counting
    # them as claims punishes the detector for admitting doubt, and ignoring them entirely
    # would hide a detector that routes the whole sheet to review. Reported as volume.
    in_review: list[Detection] = field(default_factory=list)

    @property
    def true_positives(self) -> int:
        return len(self.matched)

    @property
    def false_negatives(self) -> int:
        return len(self.missed)

    @property
    def false_positives(self) -> int:
        return len(self.spurious)

    @property
    def precision(self) -> float:
        claimed = self.true_positives + self.false_positives
        return 1.0 if claimed == 0 else self.true_positives / claimed

    @property
    def recall(self) -> float:
        present = self.true_positives + self.false_negatives
        return 1.0 if present == 0 else self.true_positives / present

    @property
    def review_volume(self) -> int:
        return len(self.in_review)

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 0.0 if p + r == 0 else 2 * p * r / (p + r)

    def restricted_to_occluded(self) -> "Score":
        """The same score counting only instances a line or a shape crosses.

        False positives are not attributable to an occluded instance, so they are dropped
        rather than guessed at: this reports recall on hard instances honestly and does not
        pretend to a precision it cannot measure.
        """
        return Score(
            class_id=self.class_id,
            matched=[m for m in self.matched if m.truth.occluded],
            missed=[t for t in self.missed if t.occluded],
            spurious=[],
            in_review=[],
        )


def _distance(detection: Detection, truth: TruthInstance) -> float:
    dx = detection.centre_px[0] - truth.centre_px[0]
    dy = detection.centre_px[1] - truth.centre_px[1]
    return math.hypot(dx, dy)


def score_class(
    detections: Sequence[Detection],
    truth: GroundTruth,
    class_id: str,
    distance_factor: float = MATCH_DISTANCE_FACTOR,
) -> Score:
    """Match one class's counted detections to its truth instances, nearest first.

    Greedy on distance rather than on detector confidence: which instance a detection belongs
    to is a question about geometry, and letting a high score claim a far-away instance would
    let the detector influence its own grading.

    Only the counting band is graded. A review-band hit is a question, not a claim; it is
    carried through as volume so a detector cannot buy precision by sending its hard cases
    to a person.
    """
    of_class = [d for d in detections if d.class_id == class_id]
    claimed = [d for d in of_class if d.status is Status.COUNTED]
    present = list(truth.for_class(class_id))

    pairs = sorted(
        (
            (_distance(d, t), di, ti)
            for ti, t in enumerate(present)
            for di, d in enumerate(claimed)
            if _distance(d, t) <= distance_factor * t.reach_px
        ),
        key=lambda p: p[0],
    )

    used_detections: set[int] = set()
    used_truth: set[int] = set()
    result = Score(class_id=class_id)
    for distance, di, ti in pairs:
        if di in used_detections or ti in used_truth:
            continue
        used_detections.add(di)
        used_truth.add(ti)
        result.matched.append(Match(present[ti], claimed[di], distance))

    result.missed = [t for i, t in enumerate(present) if i not in used_truth]
    result.spurious = [d for i, d in enumerate(claimed) if i not in used_detections]
    result.in_review = [d for d in of_class if d.status is Status.REVIEW]
    return result


def score_page(
    detections: Sequence[Detection], truth: GroundTruth
) -> dict[str, Score]:
    """Every class that appears in the truth or in the detections."""
    ids = {t.class_id for t in truth.instances} | {d.class_id for d in detections}
    return {cid: score_class(detections, truth, cid) for cid in sorted(ids)}


def format_table(scores: dict[str, Score]) -> str:
    """A per-class table, with the occluded split underneath each class that has one."""
    lines = [
        f"{'class':<16}{'TP':>5}{'FP':>5}{'FN':>5}"
        f"{'precision':>11}{'recall':>9}{'F1':>7}{'review':>8}"
    ]
    for class_id, score in sorted(scores.items()):
        # A class with nothing present and nothing claimed is a pass, but 1.000 is not the
        # honest way to say so: the ratios have no denominator. The row still appears --
        # somebody looked, and the review column says what the detector did with the sheet.
        if score.true_positives + score.false_positives + score.false_negatives == 0:
            lines.append(
                f"{class_id:<16}{0:>5}{0:>5}{0:>5}{'none present':>27}"
                f"{score.review_volume:>8}"
            )
            continue
        lines.append(
            f"{class_id:<16}{score.true_positives:>5}{score.false_positives:>5}"
            f"{score.false_negatives:>5}{score.precision:>11.3f}"
            f"{score.recall:>9.3f}{score.f1:>7.3f}{score.review_volume:>8}"
        )
        occluded = score.restricted_to_occluded()
        if occluded.true_positives + occluded.false_negatives:
            lines.append(
                f"{'  occluded only':<16}{occluded.true_positives:>5}{'-':>5}"
                f"{occluded.false_negatives:>5}{'-':>11}"
                f"{occluded.recall:>9.3f}{'-':>7}{'-':>8}"
            )
    return "\n".join(lines)
