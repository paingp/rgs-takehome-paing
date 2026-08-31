"""Thresholds -> status. Pure and cheap; re-runnable without re-detecting.

Two gates, three bands: counted / review / rejected. counted requires BOTH gates;
failing either above the floor gives review with a review_reason.

The two gates fail differently, which is the whole reason they stay separate. A low `match`
means the ink does not look like the template. A low `margin` means it looks like this
template and also like another one -- the nested-symbol problem, where a duplex receptacle
scores 0.816 and the quad that contains it scores 0.681 and neither number is wrong. Collapsing
them into one confidence would hide which failure happened.

Raster-only module: must never import pymupdf, directly or transitively.
Enforced by tests/test_raster_only.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from takeoff.classes import SymbolClass


class Status(str, Enum):
    COUNTED = "counted"
    REVIEW = "review"
    REJECTED = "rejected"


# Okabe-Ito, per decision 11. Carried here so the server and the viewer cannot drift apart.
BAND_COLOURS = {
    Status.COUNTED: "#0072B2",
    Status.REVIEW: "#E69F00",
    Status.REJECTED: "#999999",
}


@dataclass(frozen=True)
class Band:
    status: Status
    reason: str | None = None

    @property
    def colour(self) -> str:
        return BAND_COLOURS[self.status]


def band(
    match: float,
    margin: float | None,
    symbol: SymbolClass,
    ceiling: Status | None = None,
) -> Band:
    """Place one score in a band.

    `margin` is None when the class has no competitor registered yet. That is not the same as
    a wide margin and must not be scored as one: the gate is recorded as unevaluated and the
    detection still counts, because with a single class there is nothing it could be confused
    with. The moment a second class registers, the gate goes live on its own.

    `ceiling` is the best band a detection is allowed to reach, whatever it scored. It exists
    for instances found by searching INSIDE a blob too big to be the symbol: the window that
    scored well was chosen by the search, so its score is the best of many tries rather than
    one honest reading, and a gate tuned on whole components does not mean the same thing
    there. Those are worth surfacing and not worth asserting, so they are capped at REVIEW --
    the count stays as trustworthy as it was and a person confirms the recoveries.

    It can only ever lower a band. A detection that already failed the floor stays rejected.
    """
    placed = _band(match, margin, symbol)
    if ceiling is None or _RANK[placed.status] <= _RANK[ceiling]:
        return placed
    return Band(ceiling, f"{placed.reason or f'match {match:.3f}'}; held at {ceiling.value}")


# Worst to best. Used only to compare a band against a ceiling.
_RANK = {Status.REJECTED: 0, Status.REVIEW: 1, Status.COUNTED: 2}


def _band(match: float, margin: float | None, symbol: SymbolClass) -> Band:
    if match < symbol.review_floor:
        return Band(Status.REJECTED, f"match {match:.3f} below floor {symbol.review_floor:.2f}")

    if match < symbol.counted_at:
        return Band(Status.REVIEW, f"match {match:.3f} below {symbol.counted_at:.2f}")

    if margin is not None and margin < symbol.margin_at:
        return Band(Status.REVIEW, f"margin {margin:.3f} below {symbol.margin_at:.2f}")

    return Band(Status.COUNTED, None if margin is not None else "margin gate not evaluated")


def tally(bands: list[Band]) -> dict[str, int]:
    """Counts per status, always with all three keys so a zero reads as a zero."""
    counts = {status.value: 0 for status in Status}
    for b in bands:
        counts[b.status.value] += 1
    return counts
