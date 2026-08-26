"""Detection / GroundTruth dataclasses and JSON IO.

Detection IDs hash position + class, so review state and golden counts survive a re-run.
Two confidence numbers, match and margin, stay separable -- they fail differently.

Raster-only module: must never import pymupdf, directly or transitively.
Enforced by tests/test_raster_only.py.
"""
