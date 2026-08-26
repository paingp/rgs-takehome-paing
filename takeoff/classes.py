"""SYMBOL CLASS REGISTRY.

Adding a symbol must be an entry in this file, not a pipeline change. If a new symbol
needs code elsewhere, the core is under-general -- report that at the gate.

Raster-only module: must never import pymupdf, directly or transitively.
Enforced by tests/test_raster_only.py.
"""
