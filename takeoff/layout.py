"""Sheet -> named regions.

Region *geometry* comes from the raster (dilate-and-label with rule removal, per
scratch/viewport2.py). Only the caption and scale *text* comes from the text layer --
sheet metadata, not symbol detection. For a scanned set, OCR over the raster substitutes.

May import pymupdf.
"""
