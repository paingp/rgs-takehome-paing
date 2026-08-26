"""Thresholds -> status. Pure and cheap; re-runnable without re-detecting.

Two gates, three bands: counted / review / rejected. counted requires BOTH gates;
failing either above the floor gives review with a review_reason.

Raster-only module: must never import pymupdf, directly or transitively.
Enforced by tests/test_raster_only.py.
"""
