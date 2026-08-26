"""ALL coordinate conversion lives here.

The PDF text and vector layers store coordinates in *unrotated* page space while
rasters are rotated 90/270 degrees. Mixing them fails silently and produces
confidently wrong output. Every conversion between px, sheet_pt and page_pt goes
through this module and nowhere else.

May import pymupdf.
"""
