"""Orchestration: candidates -> score -> competitive assignment -> band -> scope.

Competitive assignment (argmax across the template library plus a margin test), not
per-template thresholds -- this is the answer to the nested-symbol problem.

Raster-only module: must never import pymupdf, directly or transitively.
Enforced by tests/test_raster_only.py.
"""
