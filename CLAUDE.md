# Working in this repo

Symbol Spotter: open a construction drawing, drag a box around a symbol, count every other
instance of it on that sheet. Python 3.14, classical computer vision, no ML framework.

Read [`ARCHITECTURE.md`](ARCHITECTURE.md) before changing anything in `takeoff/`.

## Commands

```powershell
.\bootstrap.ps1                                          # venv + pinned deps + tests
.venv\Scripts\python.exe -m pytest -q                    # 235 tests, ~20 min
.venv\Scripts\python.exe -m pytest -q tests/test_doors.py -k arc     # one file, one pattern
.venv\Scripts\python.exe -m uvicorn server.app:app       # viewer on :8000, no --reload
.venv\Scripts\python.exe -m eval.suites --page 5         # grade one sheet against annotations
```

The suite is slow because most of it runs real detection over real sheets. Run one file while
iterating; run the whole thing before saying you are done.

## Layout

```
takeoff/       the detector. Pure Python, no HTTP, no UI.
  raster.py      PDF/image -> greyscale array + DZI tiles. The rasterization boundary.
  spaces.py      ALL coordinate conversion. Nowhere else.
  candidates.py  line suppression, connected components, drag -> selection
  classes.py     the symbol registry — where a new symbol is added
  templates.py   template extraction, rotation/mirror bank
  scoring.py     Scorer protocol + StrokeCoverageScorer
  doors.py       parametric arc detector (doors only)
  detect.py      orchestration: score -> competitive assignment -> band
  banding.py     thresholds -> counted / review / rejected. Pure, re-runnable.
server/        FastAPI + the browser viewer (static/viewer.js is the whole UI)
eval/          harness.py (matching + metrics), suites.py (CLI), reports/
ground_truth/  reviewed annotations, one JSON per page
docs/ENGINEERING-LOG.md   what was measured, tried, and abandoned
```

## Rules that are easy to break

**Detection never sees the PDF.** `candidates`, `templates`, `scoring`, `detect`, `doors`,
`lifecycle`, `banding`, `schema`, `classes` and `regions` may not import `pymupdf` — not
directly, and not through a chain of first-party imports. Only `raster`, `spaces`, `layout` and
`vector_gt` may. `tests/test_raster_only.py` parses the AST and fails if this is violated. If
you need PDF data in a detection module, you need a different design.

**Ground truth is the user's.** Never generate, infer or fabricate annotations from detector
output, and never edit files in `ground_truth/` to make a number look better. An ungraded class
is reported as ungraded. If a metric moved, check whether the annotations changed before
claiming it was your change.

**Adding a symbol is a registry entry in `classes.py`, not a pipeline change.** If a new symbol
needs code elsewhere, the core is under-general — say so plainly rather than special-casing it.

**Measure before tuning, and record what was falsified.** Thresholds in this project are
justified by a measured gap between real instances and the best false positive, not by taste.
When a hypothesis turns out to be wrong, write it into `docs/ENGINEERING-LOG.md` with the
numbers — several entries there exist to stop an idea being retried a third time. Check that log
before proposing a change to arc ranking, the door gates, or the quality constants.

**All coordinate conversion lives in `spaces.py`.** Pages are rotated 90°/270° and the text
layer stores unrotated coordinates; anything that converts by hand will be silently wrong.

## Machine gotchas

- `import pymupdf`, not the deprecated `fitz`.
- Console output is cp1252. A script printing symbol glyphs needs
  `sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')`.
- Run uvicorn **without** `--reload`: it watches `.venv` and `cache/`, misses real edits, and can
  half-restart while the old worker keeps serving stale code.
- `cache/`, `documents/` and `classes.json` are gitignored and rebuildable. `scratch/` is
  throwaway spikes kept as evidence — read them, do not build on them.
