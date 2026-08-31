"""The types that cross the rasterization boundary, plus Detection / GroundTruth IO.

These dataclasses live here rather than in `raster.py` for a structural reason: `raster.py`
imports pymupdf, and a detection module that imported it for the sake of a type annotation
would drag a PDF handle into the half of the codebase that must never have one. The boundary
types are the contract between the two halves, so they belong to neither side's importer.

Detection IDs hash position + class, so review state and golden counts survive a re-run.
Two confidence numbers, match and margin, stay separable -- they fail differently.

Raster-only module: must never import pymupdf, directly or transitively.
Enforced by tests/test_raster_only.py.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from takeoff import spaces
from takeoff.spaces import Point


@dataclass(frozen=True)
class Raster:
    """A rendered page region. Detection sees this and nothing else."""

    gray: np.ndarray            # uint8, (H, W), 0 = ink, 255 = paper
    dpi: int
    origin_sheet_pt: Point      # px -> sheet_pt is then pure arithmetic
    page_index: int

    @property
    def size_px(self) -> tuple[int, int]:
        return (self.gray.shape[1], self.gray.shape[0])

    def to_sheet(self, x: float, y: float) -> Point:
        return spaces.px_to_sheet(x, y, self.dpi, self.origin_sheet_pt)

    def to_px(self, x: float, y: float) -> Point:
        return spaces.sheet_to_px(x, y, self.dpi, self.origin_sheet_pt)


@dataclass(frozen=True)
class InkLayers:
    """Everything derived from Raster.gray, all (H, W), all aligned to it."""

    ink: np.ndarray        # uint8   255 - gray, ink-positive
    binary: np.ndarray     # bool    ink > threshold
    structure: np.ndarray  # bool    long linear runs: walls, grid lines, sheet border
    symbols: np.ndarray    # bool    binary & ~structure   <- detectors work here

    @property
    def removed_fraction(self) -> float:
        """Share of ink that line suppression took out. ~0.81 on E4, per docs/ENGINEERING-LOG.md."""
        total = int(self.binary.sum())
        return 0.0 if total == 0 else 1.0 - int(self.symbols.sum()) / total


# ------------------------------------------------------------------------- ground truth


# Where reviewed annotations live. Keyed by document hash, the same way `cache/` is, so the
# bundled PDF and an uploaded scan can both have a page 5 without colliding, and re-opening a
# drawing finds the annotations already made against it.
GT_ROOT = Path("ground_truth")

# What produced an instance. Only `reviewed` is graded against -- decision 6: proposals are
# never trusted until a human has looked at them.
TRUTH_SOURCES = ("reviewed", "proposed")


@dataclass(frozen=True)
class TruthInstance:
    """One symbol a person has confirmed is really there."""

    class_id: str
    bbox_px: tuple[int, int, int, int]
    label: str | None = None

    # Whether a line, text or another shape crosses this instance. Carried so the harness can
    # report accuracy on occluded symbols SEPARATELY -- that number is the one the occlusion
    # work is judged by, and it is invisible inside a whole-sheet average.
    occluded: bool = False

    source: str = "reviewed"

    @property
    def centre_px(self) -> tuple[float, float]:
        x, y, w, h = self.bbox_px
        return (x + w / 2.0, y + h / 2.0)

    @property
    def reach_px(self) -> int:
        return max(self.bbox_px[2], self.bbox_px[3])

    def to_json(self) -> dict:
        return {
            "class_id": self.class_id,
            "bbox_px": list(self.bbox_px),
            "label": self.label,
            "occluded": self.occluded,
            "source": self.source,
        }

    @classmethod
    def from_json(cls, raw: dict) -> "TruthInstance":
        return cls(
            class_id=raw["class_id"],
            bbox_px=tuple(int(v) for v in raw["bbox_px"]),  # type: ignore[arg-type]
            label=raw.get("label"),
            occluded=bool(raw.get("occluded", False)),
            source=raw.get("source", "reviewed"),
        )


@dataclass(frozen=True)
class GroundTruth:
    """Every confirmed instance on one page of one document.

    Deliberately not a list of detections. Ground truth records what is ON THE DRAWING, so it
    carries no score, no band and no detector -- nothing that would let a run of the tool
    quietly become its own answer key.
    """

    document: str
    page: int
    dpi: int
    instances: tuple[TruthInstance, ...] = ()

    # Which classes a person has actually passed over on this page. Without it, "no markers
    # on this sheet" and "nobody has looked for markers on this sheet" are the same JSON --
    # the per-page distinction `load_truth` already protects (an absent file is not an empty
    # one), applied per class. T4 forced it: it carries doors and genuinely no elevation
    # markers, so the marker detector's three hits there are false positives, and grading
    # could neither say so nor stay quiet about them.
    #
    # Absent from an older file, which is why a class holding instances counts as reviewed
    # whether or not it is listed: annotations already ARE the evidence someone looked.
    reviewed_classes: tuple[str, ...] = ()

    def for_class(self, class_id: str) -> tuple[TruthInstance, ...]:
        return tuple(i for i in self.instances if i.class_id == class_id)

    def is_reviewed(self, class_id: str) -> bool:
        """Can this class be graded on this page at all?"""
        return class_id in self.reviewed_classes or any(
            i.class_id == class_id for i in self.instances
        )

    @property
    def graded_classes(self) -> tuple[str, ...]:
        return tuple(sorted(set(self.reviewed_classes) | {i.class_id for i in self.instances}))

    @property
    def reviewed(self) -> "GroundTruth":
        """Only what a human has confirmed. This is what the harness grades against."""
        return GroundTruth(
            document=self.document,
            page=self.page,
            dpi=self.dpi,
            instances=tuple(i for i in self.instances if i.source == "reviewed"),
            reviewed_classes=self.reviewed_classes,
        )

    def to_json(self) -> dict:
        return {
            "document": self.document,
            "page": self.page,
            "dpi": self.dpi,
            "reviewed_classes": list(self.reviewed_classes),
            "instances": [i.to_json() for i in self.instances],
        }

    @classmethod
    def from_json(cls, raw: dict) -> "GroundTruth":
        return cls(
            document=raw["document"],
            page=int(raw["page"]),
            dpi=int(raw["dpi"]),
            instances=tuple(TruthInstance.from_json(i) for i in raw.get("instances", ())),
            reviewed_classes=tuple(raw.get("reviewed_classes", ())),
        )


def truth_path(document: str, page: int, root: Path | None = None) -> Path:
    # Resolved at call time, not bound as a default: a default argument captures GT_ROOT when
    # this module is imported, so anything that redirects the root afterwards -- a test, or a
    # future setting -- would be silently ignored.
    return Path(GT_ROOT if root is None else root) / document / f"page{page:03d}.json"


def load_truth(document: str, page: int, root: Path | None = None) -> GroundTruth | None:
    """Reviewed annotations for one page, or None if nobody has annotated it yet.

    None and "annotated as empty" are different answers and must stay so: a page nobody has
    looked at cannot be scored, while a page confirmed to hold no instances scores a detector
    that reports any.
    """
    path = truth_path(document, page, root)
    if not path.exists():
        return None
    return GroundTruth.from_json(json.loads(path.read_text(encoding="utf-8")))


def save_truth(truth: GroundTruth, root: Path | None = None) -> Path:
    path = truth_path(truth.document, truth.page, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(truth.to_json(), indent=2) + "\n", encoding="utf-8")
    return path
