"""Scorer protocol and StrokeCoverageScorer.

Takes (Candidate.mask, TemplateVariant.mask, TemplateVariant.dist), returns floats.
A learned embedding plugs in behind the same protocol.

Raster-only module: must never import pymupdf, directly or transitively.
Enforced by tests/test_raster_only.py.
"""
