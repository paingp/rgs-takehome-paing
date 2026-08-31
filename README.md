# Symbol Spotter

Counting symbols on construction drawings is a job estimators still do by hand: open a sheet,
find every door, every receptacle, every elevation marker, and tally them. It is slow, and a
miscount is expensive.

Symbol Spotter turns that into one gesture. Open a page of a drawing set, drag a box around a
symbol, and the tool finds every other instance of it on that sheet — drawn on the drawing and
listed in a table, each with a confidence score, its orientation, and a reason you can argue
with. You then review what it found: accept the ones it got right, reject the ones it did not,
and record anything it missed. Those decisions become the ground truth the tool is measured
against, so its accuracy is a number rather than an impression.

**Detection runs on a rasterized render.** The drawing is converted to a plain greyscale image
first, and nothing downstream ever sees the PDF's vector geometry. That is a deliberate
constraint: a large share of real drawings arrive as scans, and a tool that reads vector paths
would work beautifully on clean exports and fail completely on a photocopy. Working from pixels
means a scan and a born-digital PDF go down exactly the same path. The vector layer is used for
one thing only — bootstrapping ground truth to grade against — and it never reaches the
detector.

Four symbol classes ship with the tool: single swing doors, interior elevation markers, detail
markers, and duplex receptacles. But you are not limited to them. Drag a box around anything —
a diffuser, a damper, a symbol nobody has ever registered — and it will be counted, because the
tool decides how to count a symbol by *measuring what you selected* rather than by looking it up
in a list. Name it, and it becomes a class like any other: recognised on other sheets, gradeable,
and counted on its own thresholds.

For how it works internally and why it was built this way, see
[ARCHITECTURE.md](ARCHITECTURE.md).

---

## Setup

Requires **Python 3.14**.

```powershell
.\bootstrap.ps1          # Windows
./bootstrap.sh           # macOS / Linux
```

The script creates `.venv` if it is missing, installs the pinned dependency set from
`requirements.txt`, and runs the test suite. A green run means the install is good — 235 tests,
about 20 minutes, because many of them run real detection over real sheets.

To do it by hand instead:

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m pytest -q
```

Two things that will bite on this machine specifically:

- Import the PDF library as `pymupdf`, not the deprecated `fitz` alias.
- The console is cp1252, so any script that prints a symbol glyph needs
  `sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')`.

## Run

```powershell
.venv\Scripts\python.exe -m uvicorn server.app:app
```

Then open **http://127.0.0.1:8000/?page=5** — sheet T5, a floor plan with doors and elevation
markers on it, and the sheet most of the measurements in this project were taken from.

The first view of any sheet builds a tile pyramid so the browser can pan and zoom a 36″×24″
drawing smoothly. That takes about 15 seconds and 9 MB, shows a progress bar, and is cached
afterwards, so a sheet is only slow the first time. To build one ahead of time:

```powershell
.venv\Scripts\python.exe -m takeoff.raster --page 5 --dzi
```

## Using it

The whole workflow is one pass, top to bottom in the right-hand panel.

1. **Select.** Click **Select symbol**, then drag a box around one instance of the symbol you
   want. Draw the box tightly — it is treated as a hard boundary, so anything outside it is not
   part of the symbol. The panel shows what it snapped to, magnified, with its size and ink
   area. If the symbol has several disjoint pieces they are outlined; click one to exclude it.
2. **Count.** Press **Count these**. The tool sweeps the sheet and returns every match, drawn
   on the drawing and listed by confidence. If the symbol is one of the registered classes it is
   recognised by name and counted on that class's calibrated thresholds; if not, it is counted
   anyway, unnamed, on generic ones.
3. **Review.** Step through the matches and judge each one. This is the part that turns a count
   into something trustworthy — and accepting a match records it as a real instance in the
   page's ground truth.
4. **Record what it missed.** Press **+ Missed**, drag a box around a symbol the tool never
   proposed, and confirm. This is the half no accept/reject gesture can produce, and it is most
   of what measuring recall depends on.
5. **Save**, then **Evaluate**. Evaluation scores your finished review against the page's
   annotations: how many recorded instances were found, how many were missed, how the occluded
   ones did, false positives, average precision and recall.

### Keyboard Shortcuts

| Key | Does |
|---|---|
| `A` / `R` | Accept / reject the current match |
| `N` / `P` (or `→` / `←`) | Step to the next / previous match |
| `M` | Arm missed-instance mode, then drag a box |
| `O` | Mark the selected instance as occluded — something crosses it |
| `S` | Save this page's annotations to disk |
| `G` | Show or hide the ground-truth overlay |
| `V` | Evaluate the count, or show a stored graded run |
| `E` | Edit mode — drag an existing annotation box to correct it |
| `F` | Fit the sheet to the window |
| `D` | Hide or show the detection overlay |
| `C` | Show the candidate blobs the detector is choosing between |
| `Delete` | Delete the selected annotation |
| `Esc` | Back out of the gesture in progress |

### Adding your own symbol class

Select a symbol, press **+ New class**, and give it a name. The box you drew is stored as that
class's reference, which is what lets it behave like a built-in — recognised on other sheets,
annotated against, and graded. User classes are saved to `classes.json`, which is local to your
machine and not committed. The **Classes** button lists everything the tool knows and lets you
remove anything you added.

## Grading from the command line

```powershell
.venv\Scripts\python.exe -m eval.suites --page 5
```

This runs a full detection pass for every registered class and scores it against the page's
saved annotations, printing precision, recall and F1 per class — plus the same figures for
occluded instances alone, which is the number that matters and which a whole-sheet average
hides. A class nobody has annotated on that page is named and skipped rather than scored
against nothing. Each run is written to `eval/reports/<document>/pageNNN.json`.

## Repository layout

```
takeoff/            the detector — pure Python, no HTTP, no UI
server/             FastAPI backend + the browser viewer
eval/               matching, metrics, and graded run reports
ground_truth/       reviewed annotations, one JSON file per page
tests/              235 tests
docs/
  ENGINEERING-LOG.md  the working log: what was measured, tried, and abandoned
ARCHITECTURE.md     how it works, and why
cache/              rasters and tile pyramids — rebuildable, not committed
scratch/            throwaway spikes kept as evidence — do not build on them
```
