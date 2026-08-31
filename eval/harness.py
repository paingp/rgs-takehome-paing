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

BUT REVIEW IS NOT ALL THE SAME. A review hit sitting on an instance the counted band missed is
a different event from one sitting on nothing: the first found the symbol and declined to
assert it, the second is noise a person has to clear. Reporting them as one number made
occlusion work unmeasurable -- an instance recovered into review stayed a false negative AND
raised review volume, so every metric moved the wrong way and real progress read as a
regression. They are matched separately and reported as `recovered` and `review_spurious`,
with `recall_with_review` as the ceiling recall would reach if a person confirmed every one.
`recall` itself is untouched and still counts only what the tool asserted.

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

    # Hits the tool sent to a person instead of claiming. Not graded as claims: counting them
    # as such punishes the detector for admitting doubt. Split by whether they landed on
    # something real, because that is the difference between finding a hard instance and
    # generating work.
    recovered: list[Match] = field(default_factory=list)
    review_spurious: list[Detection] = field(default_factory=list)

    @property
    def in_review(self) -> list[Detection]:
        """Every review-band hit, recovered or not, in no particular order."""
        return [m.detection for m in self.recovered if m.detection] + self.review_spurious

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
    def not_found(self) -> list[TruthInstance]:
        """Instances the tool did not locate AT ALL -- not counted, not sent to review.

        `missed` is every instance the COUNTED band failed to claim, which is the right
        denominator for `recall` and the wrong thing to show a person: an instance found and
        held for confirmation is in there too, so listing `missed` alongside `recovered` named
        the same instance twice, once as lost and once as found. Recall is unchanged; this is
        the subset with nothing pointing at it.
        """
        seen = {m.truth for m in self.recovered}
        return [t for t in self.missed if t not in seen]

    @property
    def review_volume(self) -> int:
        return len(self.recovered) + len(self.review_spurious)

    @property
    def recall_with_review(self) -> float:
        """Recall if a person confirmed every review hit that landed on a real instance.

        The ceiling on what the current detector can reach without finding anything new, and
        the number occlusion work moves when recoveries are deliberately held out of the
        counted band. It is not a substitute for `recall`: the gap between them is exactly
        the human effort the tool is asking for.
        """
        present = self.true_positives + self.false_negatives
        return 1.0 if present == 0 else (self.true_positives + len(self.recovered)) / present

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
            recovered=[m for m in self.recovered if m.truth.occluded],
            review_spurious=[],
        )


def _distance(detection: Detection, truth: TruthInstance) -> float:
    dx = detection.centre_px[0] - truth.centre_px[0]
    dy = detection.centre_px[1] - truth.centre_px[1]
    return math.hypot(dx, dy)


def _greedy(
    detections: Sequence[Detection],
    truths: Sequence[TruthInstance],
    distance_factor: float,
) -> tuple[list[Match], list[int], list[int]]:
    """Pair detections to truth instances, nearest pair first. Returns (matches, spare
    detection indices, unmatched truth indices).

    Greedy on distance rather than on detector confidence: which instance a detection belongs
    to is a question about geometry, and letting a high score claim a far-away instance would
    let the detector influence its own grading.
    """
    pairs = sorted(
        (
            (_distance(d, t), di, ti)
            for ti, t in enumerate(truths)
            for di, d in enumerate(detections)
            if _distance(d, t) <= distance_factor * t.reach_px
        ),
        key=lambda p: p[0],
    )

    used_d: set[int] = set()
    used_t: set[int] = set()
    matches: list[Match] = []
    for distance, di, ti in pairs:
        if di in used_d or ti in used_t:
            continue
        used_d.add(di)
        used_t.add(ti)
        matches.append(Match(truths[ti], detections[di], distance))

    return (
        matches,
        [i for i in range(len(detections)) if i not in used_d],
        [i for i in range(len(truths)) if i not in used_t],
    )


def score_class(
    detections: Sequence[Detection],
    truth: GroundTruth,
    class_id: str,
    distance_factor: float = MATCH_DISTANCE_FACTOR,
) -> Score:
    """Match one class's counted detections to its truth instances, nearest first.

    Only the counting band is graded. A review-band hit is a question, not a claim; it is
    carried through as volume so a detector cannot buy precision by sending its hard cases
    to a person. The review band is then matched AGAIN, against the instances the counted band
    missed -- same geometry, same tolerance, no second chance at precision. That says which
    review hits found something and which are noise, and it is the only way work that
    deliberately routes hard instances to review can be told from work that lost them.
    """
    of_class = [d for d in detections if d.class_id == class_id]
    claimed = [d for d in of_class if d.status is Status.COUNTED]
    present = list(truth.for_class(class_id))

    matched, unclaimed_di, unmatched_ti = _greedy(claimed, present, distance_factor)
    result = Score(class_id=class_id)
    result.matched = matched
    result.missed = [present[i] for i in unmatched_ti]
    result.spurious = [claimed[i] for i in unclaimed_di]

    # Second pass: does a review hit sit on one of the instances the counted band missed?
    sent_to_person = [d for d in of_class if d.status is Status.REVIEW]
    recovered, spare_di, _ = _greedy(sent_to_person, result.missed, distance_factor)
    result.recovered = recovered
    result.review_spurious = [sent_to_person[i] for i in spare_di]
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
        f"{'precision':>11}{'recall':>9}{'F1':>7}{'+review':>9}{'review':>8}"
    ]
    for class_id, score in sorted(scores.items()):
        # A class with nothing present and nothing claimed is a pass, but 1.000 is not the
        # honest way to say so: the ratios have no denominator. The row still appears --
        # somebody looked, and the review column says what the detector did with the sheet.
        if score.true_positives + score.false_positives + score.false_negatives == 0:
            lines.append(
                f"{class_id:<16}{0:>5}{0:>5}{0:>5}{'none present':>36}"
                f"{score.review_volume:>8}"
            )
            continue
        # `+review` is recall counting the review hits that landed on a real instance. It sits
        # beside recall rather than replacing it: the gap between the two is the human effort
        # the tool is asking for, and hiding either number hides half of that.
        lines.append(
            f"{class_id:<16}{score.true_positives:>5}{score.false_positives:>5}"
            f"{score.false_negatives:>5}{score.precision:>11.3f}"
            f"{score.recall:>9.3f}{score.f1:>7.3f}"
            f"{score.recall_with_review:>9.3f}{score.review_volume:>8}"
        )
        occluded = score.restricted_to_occluded()
        if occluded.true_positives + occluded.false_negatives:
            lines.append(
                f"{'  occluded only':<16}{occluded.true_positives:>5}{'-':>5}"
                f"{occluded.false_negatives:>5}{'-':>11}"
                f"{occluded.recall:>9.3f}{'-':>7}"
                f"{occluded.recall_with_review:>9.3f}{'-':>8}"
            )
    return "\n".join(lines)


# ------------------------------------------------------- scoring a review, not a detector run


@dataclass(frozen=True)
class ReviewScore:
    """One class scored with a PERSON'S VERDICTS standing in for the detector's bands.

    `score_class` above grades a detector run: the counted band is the claim, the review band
    is a question, and nobody has looked. This grades the other thing -- a finished review,
    where every match has been accepted or rejected and those verdicts are the answer. The two
    are not interchangeable and neither replaces the other:

      - `score_class` is what `-m eval.suites` reports. No human is present, so a review-band
        hit cannot be resolved and is carried as volume.
      - `ReviewScore` is what the Evaluate button reports. A person has been through every
        match, so the bands no longer matter: what they accepted is what the tool found, and
        the band it came from is a detail of how it got there.

    ACCEPTED IS DETECTED, whichever band it came from. Holding a hard instance for
    confirmation and having it confirmed is a find, not a half-find, and once somebody has
    confirmed it there is nothing left for `recall_with_review` to say.

    REJECTED IS A FALSE POSITIVE. The tool proposed it and a person said no; that is the only
    definition of a false positive that a review can produce. An accepted match that lands on
    no recorded instance is one too, though in practice accepting RECORDS the instance, so
    that set is usually empty and the rejections are the whole of it.
    """

    class_id: str
    detected: list[Match] = field(default_factory=list)
    missed: list[TruthInstance] = field(default_factory=list)
    wrong: list[Detection] = field(default_factory=list)

    # Every verdict as (score, was it right), best score first. Kept rather than collapsed
    # because average precision is a question about the ORDER the detector put them in, which
    # no count of outcomes can answer.
    ranked: list[tuple[float, bool]] = field(default_factory=list)

    @property
    def present(self) -> int:
        """Recorded instances of this class on the page -- the denominator for recall."""
        return len(self.detected) + len(self.missed)

    @property
    def false_positives(self) -> int:
        return len(self.wrong)

    @property
    def recall(self) -> float:
        return 1.0 if self.present == 0 else len(self.detected) / self.present

    @property
    def precision(self) -> float:
        claimed = len(self.detected) + len(self.wrong)
        return 1.0 if claimed == 0 else len(self.detected) / claimed

    @property
    def occluded_detected(self) -> int:
        return len([m for m in self.detected if m.truth.occluded])

    @property
    def occluded_present(self) -> int:
        return self.occluded_detected + len([t for t in self.missed if t.occluded])

    @property
    def average_precision(self) -> float:
        return average_precision(self.ranked, self.present)


def average_precision(ranked: Sequence[tuple[float, bool]], present: int) -> float:
    """Area under the precision-recall curve, walked in the detector's own score order.

    NOT the same number as precision, and that is the point of having it. Precision is one
    operating point: of what was claimed, how much was right. AP asks whether the SCORES were
    ordered correctly -- a wrong detection scoring above right ones costs AP while leaving
    precision alone, and that is a real defect, because the score is what a reviewer reads
    first and what any future threshold would cut on.

    `ranked` is (score, was it right), best score first. `present` is how many instances are
    recorded, so recall is measured against the sheet and not against what was claimed: a
    detector that finds three of forty and is right about all three earns AP 0.075, not 1.000.

    Nothing recorded and nothing claimed is 1.000, the same convention `precision` and
    `recall` use: a class with no instances and no claims got nothing wrong.
    """
    if present == 0:
        return 1.0 if not ranked else 0.0
    hits = 0
    seen = 0
    previous_recall = 0.0
    area = 0.0
    for _, right in ranked:
        seen += 1
        if right:
            hits += 1
        recall = hits / present
        area += (recall - previous_recall) * (hits / seen)
        previous_recall = recall
    return area


def score_review(
    accepted: Sequence[Detection],
    rejected: Sequence[Detection],
    truth: GroundTruth,
    class_id: str,
    distance_factor: float = MATCH_DISTANCE_FACTOR,
) -> ReviewScore:
    """Score one class from a finished review: accepted are the finds, rejected are the errors.

    Matching is the same greedy nearest-pair rule `score_class` uses, on the same tolerance,
    so a match here means exactly what a match means there. Only the input differs.
    """
    mine_ok = [d for d in accepted if d.class_id == class_id]
    mine_no = [d for d in rejected if d.class_id == class_id]
    present = list(truth.for_class(class_id))

    matched, unclaimed_di, unmatched_ti = _greedy(mine_ok, present, distance_factor)
    on_nothing = [mine_ok[i] for i in unclaimed_di]

    # Ranked by the detector's own score, best first, so AP reads the order it proposed.
    right = {id(m.detection) for m in matched}
    ranked = sorted(
        ((d.match, id(d) in right) for d in list(mine_ok) + list(mine_no)),
        key=lambda row: -row[0],
    )

    return ReviewScore(
        class_id=class_id,
        detected=matched,
        missed=[present[i] for i in unmatched_ti],
        wrong=on_nothing + list(mine_no),
        ranked=ranked,
    )
