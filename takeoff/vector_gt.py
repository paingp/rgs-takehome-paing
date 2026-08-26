"""Vector-layer ground-truth bootstrap.

Proposes ground truth by clustering vector motifs (per scratch/vecgt.py), for human
review. Grading only -- never an input to detection. The detector and this module meet
only inside eval/harness.py, which is what makes the cross-check meaningful.

May import pymupdf.
"""
