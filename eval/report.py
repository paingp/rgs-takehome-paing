"""A graded run, as data -- the metrics plus every box behind them.

The table says a sheet has one false positive. The only question anyone asks next is WHICH
ONE, and until this existed the answer was to write a throwaway script that re-ran the
detector and printed boxes: the contact-sheet loop the harness was built to end.

Not the HTML page of crops this module was originally sketched as. The viewer already tiles
the sheet, already draws ground truth, and is the only place a false positive means anything
-- you have to see the ink it landed on. So the run is written as JSON, `server/app.py` hands
it to the viewer in image pixels, and the drawing itself is the report.

Boxes are in detection pixels, the same space `gt/` stores, so a report and the annotations
it grades can be read against each other with no conversion nobody sees.
"""

from __future__ import annotations

from typing import Sequence

from eval.harness import Score


def payload(scores: dict[str, Score], skipped: Sequence[str] = ()) -> dict:
    """A run, as data: the metrics plus every box behind them.

    The table says a sheet has one false positive. The only question anyone asks next is
    WHICH ONE, and until this existed the answer was to write a throwaway script that re-ran
    the detector and printed boxes -- the contact-sheet loop the harness was built to end.

    Boxes are in detection pixels, the same space `gt/` stores, so this file and the
    annotations it grades can be read against each other without a conversion nobody sees.
    """
    return {
        "version": 1,
        "classes": {
            class_id: {
                "true_positives": s.true_positives,
                "false_positives": s.false_positives,
                "false_negatives": s.false_negatives,
                "precision": round(s.precision, 4),
                "recall": round(s.recall, 4),
                "f1": round(s.f1, 4),
                "review_volume": s.review_volume,
                "occluded_recall": round(s.restricted_to_occluded().recall, 4),
                # Every box behind the numbers. `matched` carries both boxes and the distance
                # between them, which is what says whether a hit is centred or barely inside
                # the tolerance -- a detector drifting off its symbols shows up here first.
                "matched": [
                    {
                        "truth_px": list(m.truth.bbox_px),
                        "detection_px": list(m.detection.bbox_px),
                        "distance_px": round(m.distance_px, 1),
                        "match": m.detection.match,
                        "occluded": m.truth.occluded,
                        "label": m.truth.label,
                    }
                    for m in s.matched
                ],
                "missed": [
                    {"truth_px": list(t.bbox_px), "occluded": t.occluded, "label": t.label}
                    for t in s.missed
                ],
                "spurious": [
                    {"detection_px": list(d.bbox_px), "match": d.match,
                     "variant": d.variant_label}
                    for d in s.spurious
                ],
                "in_review": [
                    {"detection_px": list(d.bbox_px), "match": d.match, "reason": d.reason}
                    for d in s.in_review
                ],
            }
            for class_id, s in sorted(scores.items())
        },
        # Named, never scored: a class nobody has annotated here, or one whose anchor is on a
        # sheet this drawing does not have. A reader has to be able to tell an empty result
        # from a class that was never asked.
        "not_graded": list(skipped),
    }
