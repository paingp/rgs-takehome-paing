"""Parametric arc detector.

Doors get a swept radius range rather than a fixed size band -- a door's arc radius
*is* its width.

Raster-only module: must never import pymupdf, directly or transitively.
Enforced by tests/test_raster_only.py.
"""
