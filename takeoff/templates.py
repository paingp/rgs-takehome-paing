"""Template extraction, rotation/mirror bank, legend harvesting.

One Template per class, expanded to 48 TemplateVariant (rotation x mirror).

Raster-only module: must never import pymupdf, directly or transitively.
Enforced by tests/test_raster_only.py.
"""
