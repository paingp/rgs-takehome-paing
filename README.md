# Symbol Spotter

Select a symbol on a construction drawing; count every other instance of it on that sheet.

Open a page of `Skanksa.pdf`, drag a box around a symbol — a door, a detail or elevation
marker, an electrical receptacle — and the tool returns an annotated drawing plus a table of
every match with its confidence, scope, and lifecycle state.

Detection runs on a **rasterized** render of the page. The PDF's vector geometry is used only
to bootstrap ground truth for grading, and never reaches the detector.

## Status

Scaffold. Research and feasibility spikes are complete (`PROGRESS.md`); the build is gated and
starts at Gate 1. Every module under `takeoff/`, `eval/`, and `server/` is a documented stub
except `tests/test_raster_only.py`, which is live and enforcing.

## Setup

```
.venv/Scripts/python.exe -m pip install -r requirements.txt
.venv/Scripts/python.exe -m pytest
```

Python 3.14. Two gotchas on this machine: `import pymupdf`, not the deprecated `fitz`; and
console output is cp1252, so any script printing symbol glyphs needs
`sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')`.

## Layout

```
takeoff/          detector core — pure Python, no HTTP, no UI
  spaces.py       ALL coordinate conversion
  raster.py       PDF -> Raster; DZI tile pyramid
  layout.py       sheet -> named regions
  candidates.py   line suppression, connected components, per-class size bands
  classes.py      symbol class registry — how a new symbol gets added
  templates.py    template extraction, rotation/mirror bank, legend harvesting
  scoring.py      Scorer protocol + StrokeCoverageScorer
  detect.py       candidates -> score -> competitive assignment -> band -> scope
  banding.py      thresholds -> status; pure, re-runnable without re-detecting
  doors.py        parametric arc detector
  lifecycle.py    grey-level band classification
  schema.py       Detection / GroundTruth dataclasses + JSON IO
  vector_gt.py    vector-layer ground-truth bootstrap (grading only)
server/           FastAPI adapter + OpenSeadragon viewer
eval/             matching, metrics, calibration, HTML report
gt/               reviewed ground truth, one JSON per page
golden/           committed regression counts
scratch/          throwaway spikes, kept for reference — do not build on them
```

## The rule the tests enforce

`raster.py`, `spaces.py`, `layout.py`, and `vector_gt.py` may import `pymupdf`. No other module
in `takeoff/` may, directly or through a chain of first-party imports.
`tests/test_raster_only.py` walks the AST to prove it and names the import chain when it fails,
so the brief's constraint is a CI failure rather than a review habit.

## Design decisions

Recorded in `PROGRESS.md`; the executable plan lives at
`~/.claude/plans/crystalline-gliding-bear.md`. The two that shape everything else:

- **Competitive assignment, not per-template thresholds.** A duplex receptacle is geometrically a
  subset of a quad; measured margin against the duplex template is 0.816 vs 0.681, too thin to
  threshold. Matches are assigned by argmax across the template library plus a margin test.
- **Two confidence numbers, `match` and `margin`.** They fail differently, so they stay separable,
  and drive three bands — counted / review / rejected — through two independently settable gates.
