"""Grey-level band classification: existing / new / demolition.

Read from the stroke core, not the whole stroke: anti-aliased edges of a black line
are also mid-grey. A first-class output field with its own accuracy metric.

Raster-only module: must never import pymupdf, directly or transitively.
Enforced by tests/test_raster_only.py.
"""
