"""PDF -> Raster, and PDF -> DZI tile pyramid.

The rasterization boundary. Renders once per (pdf_hash, page, dpi, clip) and caches.
Two artifacts come out and must not be confused:
  Raster.gray  greyscale, single DPI (default 300) -- the ONLY input to detection
  DZI tiles    RGB, multi-resolution -- viewer only, never scored against

May import pymupdf.
"""
