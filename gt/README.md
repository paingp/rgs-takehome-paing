# Reviewed ground truth

One JSON per page per document, keyed by the document's content hash -- the same scheme
`cache/` uses, so the bundled PDF and an uploaded scan can both have a page 5 without
colliding, and re-opening a drawing finds the annotations already made against it.

```
gt/0937f4764f7b/page005.json     Skanksa.pdf, sheet T5
gt/e604b67ffc6a/page001.json     an uploaded scan
```

**Made in the viewer, never by typing coordinates.** Count a symbol, review the hits with
`A`/`R`, mark anything the detector missed with `M`, then `S` to save. Accepting a detection
records it as real; `+ Missed` records what was never proposed, which is the half no
accept/reject gesture can produce and most of what occlusion work needs to measure.

An absent file and an empty one are different claims: a page nobody has annotated cannot be
scored, while a page confirmed to hold no instances scores a detector that reports any.

Instances carry `occluded`, so `eval/harness.py` can report recall on occluded symbols
separately -- on a real sheet they are a handful among forty, and a whole-page average barely
moves whether they are all found or all missed.

`source` is `reviewed` or `proposed`. Only `reviewed` is graded against: proposals -- from a
low-threshold detector run, or from `takeoff/vector_gt.py` -- are never trusted until a human
has looked at them.

Grade a page with:

```
.venv/Scripts/python.exe -m eval.suites --page 5
```
