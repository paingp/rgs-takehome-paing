"""Line suppression, connected components, per-class size bands.

Raster.gray -> InkLayers -> list[Candidate]. Ports scratch/spike6.py.

Raster-only module: must never import pymupdf, directly or transitively.
Enforced by tests/test_raster_only.py.
"""
