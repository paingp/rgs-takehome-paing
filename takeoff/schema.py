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

from dataclasses import dataclass

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
        """Share of ink that line suppression took out. ~0.81 on E4, per PROGRESS.md."""
        total = int(self.binary.sum())
        return 0.0 if total == 0 else 1.0 - int(self.symbols.sum()) / total
