"""Scorer protocol and StrokeCoverageScorer.

Takes (Candidate.mask, TemplateVariant.mask, TemplateVariant.dist), returns floats.
A learned embedding plugs in behind the same protocol.

Why coverage rather than NCC: the spikes scored normalised cross-correlation over the whole
raster and paid 27.5 s for 48 variants over 24 MP. Line suppression has already reduced the
sheet to a few thousand connected components, so the work here is one small array comparison
per candidate per variant, and the score is computed on ink rather than on grey levels --
which means a hatched glyph is not rewarded for the paper around it.

The score is symmetric on purpose. Forward coverage alone ("how much of the candidate sits on
the template") rates a bare triangle outline a perfect match for a hatched triangle, because
every one of its pixels lands on template ink. Backward coverage alone ("how much of the
template the candidate explains") rates a solid blob perfect for the same reason in reverse.
Taking the worse of the two requires the candidate to be both no more and no less than the
template, and that is what separates the elevation marker from the stair hatching beside it.

Raster-only module: must never import pymupdf, directly or transitively.
Enforced by tests/test_raster_only.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import cv2
import numpy as np

from takeoff.templates import TemplateVariant, distance_to_ink

# How far a candidate pixel may sit from template ink and still count as explained. Line work
# on these sheets is 2-3 px wide at 300 DPI, so this is roughly one stroke: enough to absorb
# rasterisation and a pixel of misregistration, not enough to bridge a hatching gap.
TOLERANCE_IN = 0.01


@dataclass(frozen=True)
class Score:
    """One candidate against one variant. The parts are kept so a near miss is explainable."""

    match: float              # min(forward, backward) -- the number that bands
    forward: float            # share of candidate ink explained by the template
    backward: float           # share of template ink explained by the candidate
    variant_label: str

    @property
    def asymmetry(self) -> float:
        """How lopsided the fit is. Large means one shape contains the other."""
        return abs(self.forward - self.backward)


class Scorer(Protocol):
    """Anything that can rate a candidate mask against a template variant.

    A learned embedding satisfies this by ignoring `dist` and comparing feature vectors; the
    orchestration in detect.py never learns which kind it holds.
    """

    def score(self, mask: np.ndarray, variant: TemplateVariant, dpi: int) -> Score: ...


@dataclass(frozen=True)
class StrokeCoverageScorer:
    """Symmetric chamfer coverage between a candidate's ink and a variant's ink."""

    tolerance_in: float = TOLERANCE_IN

    def score(self, mask: np.ndarray, variant: TemplateVariant, dpi: int) -> Score:
        tolerance = max(1.0, self.tolerance_in * dpi)

        # Compare in the variant's frame. A candidate that passed the size gate is within a
        # fraction of the template's size, so this is a small correction, not a rescue of a
        # wrongly sized blob -- INTER_NEAREST keeps hatching from smearing into a solid.
        if mask.shape != variant.mask.shape:
            resized = cv2.resize(
                mask.astype(np.uint8),
                (variant.mask.shape[1], variant.mask.shape[0]),
                interpolation=cv2.INTER_NEAREST,
            ).astype(bool)
        else:
            resized = mask

        candidate_ink = int(resized.sum())
        template_ink = int(variant.mask.sum())
        if candidate_ink == 0 or template_ink == 0:
            return Score(0.0, 0.0, 0.0, variant.label)

        forward = float((variant.dist[resized] <= tolerance).mean())
        backward = float((distance_to_ink(resized)[variant.mask] <= tolerance).mean())
        return Score(
            match=min(forward, backward),
            forward=forward,
            backward=backward,
            variant_label=variant.label,
        )


def best_variant(
    mask: np.ndarray, bank: list[TemplateVariant], dpi: int, scorer: Scorer
) -> Score:
    """The best score a candidate achieves against any orientation of one class."""
    best = Score(0.0, 0.0, 0.0, "")
    for variant in bank:
        current = scorer.score(mask, variant, dpi)
        if current.match > best.match:
            best = current
    return best
