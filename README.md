# Symbol Spotter

Select a symbol on a construction drawing; count every other instance of it on that sheet.

Open a page of `Skanksa.pdf`, drag a box around a symbol — a door, a detail or elevation
marker, an electrical receptacle — and the tool returns an annotated drawing plus a table of
every match with its confidence, scope, and lifecycle state.

Detection runs on a **rasterized** render. The goal is to count symbols on *any* rasterized
drawing, so a scan is a first-class input: open a PDF or an image (PNG, JPG, TIFF) and the
detector cannot tell which it came from. The PDF's vector geometry is used only to bootstrap
ground truth for grading, and never reaches the detector.

## Status

**Ground truth and grading are in.** Annotate a page in the viewer — count, accept with `A`,
reject with `R`, record what the detector *missed* with `M`, mark an instance something
crosses with `O`, correct a box with `E`, save with `S` — then grade it:

```powershell
.venv\Scripts\python.exe -m eval.suites --page 5
```

Precision, recall and F1 per class, **plus the same for occluded instances alone**, plus how
many hits went to review rather than being claimed. The occluded split is the point: on a real
sheet the occluded symbols are a handful among forty, so a whole-page average barely moves
whether they are all found or all missed. Nothing here was gradeable before — every count was
checked by eye against a contact sheet, and three changes in one session each moved counts
invisibly until someone looked.

A class is graded on any sheet, not only the one it was registered from: the entry is built on
its anchor page and run against the page under test. A class nobody has annotated on that page
is named and skipped rather than scored — `-m eval.suites --page 4` used to report 27 misses
and a precision of **1.000** against a detector it had never asked to look.

**Where the results are.** Each run writes `eval/reports/<document>/pageNNN.json`: the metrics,
and every box behind them — what was found, what was missed, what was claimed wrongly, what
went to review. Press `V` in the viewer to draw that run over the sheet:

```
green            found it
vermillion       claimed ink that is not the symbol
vermillion, dashed   a real instance it never claimed
amber, dotted    sent to review rather than counted
```

Click any row in the Grading panel to fly to the ink. This is the answer to *which one* — the
question a table always provokes and could never answer, and the reason the old habit was to
render a contact sheet and squint at it. The overlay reads the stored run rather than grading
live, because a run is a full detection pass (17 s on T5, 53 s on T4) and the tool should not
have two places where one happens; the panel says how old the run is.

A recorded box is correctable. `E` turns on box editing: click an instance, drag any of eight
handles, `Del` removes it. What moves is the ANNOTATION, never the detection — the detector's
box is its claim, and the evidence when it is wrong. This is not cosmetic: the harness matches
within half the truth box's larger side, so a tighter box is a stricter target.

Occlusion is the annotator's call, made with `O`, and deliberately not inferred from whether
the detector found the instance. It used to be: accepting a hit wrote `occluded: false` and
`M` wrote `occluded: true`, so the flag recorded *"the tool missed this"* rather than *"a wall
crosses this"*. That made the split circular — a crossed symbol the detector did find could
never enter it, and occluded recall started at 0 by construction.

**Gate 5: doors count, by a different detector.** Two classes are registered. 192 tests pass.

The elevation marker is matched against a template bank. A door is not: measured on T5, ink
per door varies **11.4x** once line suppression has run, because it leaves a different amount
of each one behind — so only 47% of door-to-door pairs clear 0.90 and recall runs 0%-68%
purely on which instance was dragged. The arc's *radius* does not vary at all. Doors are
counted by sweeping for that circle (`takeoff/doors.py`), and the registry says which detector
a class needs. **31 swings counted on T5**, best non-door 0.46, ~13 s.

**Gate 4: the first symbol counts end to end.** Drag a box around an interior
elevation marker on T5, press **Count these**, and every instance on the sheet comes back
banded and coloured — 7 counted, 2 held for review, in 0.02 s. 76 tests pass.

Live as of that gate: `spaces.py`, `raster.py`, `schema.py`, `candidates.py`,
`templates.py`, `scoring.py`, `classes.py`, `banding.py`, `detect.py`, the DZI pyramid, the
server and the viewer. Absent then, landed since: `doors.py`, `regions.py`, the eval harness
and reports, and the margin gate — which now fires, but only where a marker found inside a
blob it was fused to shares that ink with a door. Still absent: `lifecycle.py`, and the
nested-symbol case the margin gate was built for.

The build is gated and stops for testing at each gate; see `PROGRESS.md`.

## Setup

```powershell
.\bootstrap.ps1          # or ./bootstrap.sh
```

Creates `.venv` if missing, installs the pinned stack, runs the suite. Python 3.14.

## Run

```powershell
.venv\Scripts\python.exe -m uvicorn server.app:app
# open http://127.0.0.1:8000/?page=5
```

Deliberately **without** `--reload` on this machine. The reloader watches the whole working
directory — 6,536 files, of which `.venv` and `cache/` are 96% — and a change to
`takeoff/candidates.py` went undetected for two minutes. Worse, when it does fire the
restart can half-fail: it logs `Reloading...`, the replacement worker hangs mid-import, and
the *old* worker stays alive serving stale code with no error anywhere. Cold import is
0.7 s, so Ctrl+C and re-run is both faster and honest. If you want it anyway, scope it with
`--reload-dir takeoff --reload-dir server` and check for `Application startup complete`
after every `Reloading...`.

The first view of a sheet builds its tile pyramid — about 15 s and 9 MB for 460 tiles, with
a progress bar in the viewer — and is cached under `cache/` afterwards. To pre-build one:

```powershell
.venv\Scripts\python.exe -m takeoff.raster --page 5 --dzi
.venv\Scripts\python.exe -m takeoff.raster --page 26         # raster + ink-layer stats
```

Two gotchas on this machine: `import pymupdf`, not the deprecated `fitz`; and console output
is cp1252, so any script printing symbol glyphs needs
`sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')`.

## Stack

| Layer | Choice |
|---|---|
| Runtime | Python 3.14 — one language end to end, no Node build step |
| PDF → pixels | PyMuPDF 1.28.2, confined to the four modules allowed to import it |
| Detection | OpenCV 5.0 (headless) + NumPy 2.5 — classical CV, no learned model yet |
| Serving | FastAPI 0.141 on uvicorn, Pydantic 2.13 |
| Viewer | OpenSeadragon 6.1, vendored in `server/static/vendor/` — no CDN, works offline |
| Tests | pytest 9.1 with `httpx2` for the API client |

No ML framework by design. `scoring.py` defines a `Scorer` protocol; a learned embedding
drops in behind it once the classical baseline has been measured rather than assumed.

## Layout

```
takeoff/          detector core — pure Python, no HTTP, no UI
  spaces.py       ALL coordinate conversion                      [live]
  raster.py       PDF -> Raster; DZI tile pyramid                 [live]
  schema.py       boundary types; later Detection / GroundTruth   [live]
  candidates.py   line suppression, components, drag -> symbol    [live]
  layout.py       text layer -> captions (grading and labels only)
  regions.py      sheet -> named blocks; which of them hold drawings    [live]
  classes.py      symbol class registry — how a new symbol gets added   [live]
  templates.py    template extraction, rotation/mirror bank             [live]
  scoring.py      Scorer protocol + StrokeCoverageScorer                [live]
  detect.py       candidates -> score -> assignment -> band             [live]
  banding.py      thresholds -> status; pure, re-runnable               [live]
  doors.py        parametric arc detector
  lifecycle.py    grey-level band classification
  vector_gt.py    vector-layer ground-truth bootstrap (grading only)
server/           FastAPI adapter + OpenSeadragon viewer            [live]
eval/             matching, metrics, calibration, HTML report
gt/               reviewed ground truth, one JSON per page
golden/           committed regression counts
cache/            rasters and tile pyramids, keyed by PDF hash — gitignored, rebuildable
scratch/          throwaway spikes, kept for reference — do not build on them
```

## Selecting a symbol

**The user's box is the boundary.** A symbol is often several blobs of ink — a marker's circle
and the letter inside it never touch, a door's arc and leaf are separate — so rather than
inferring which blobs belong together, the tool takes what was enclosed.

"Enclosed" is measured on a blob's **ink**, not on its bounding box. The two agree for solid
glyphs and disagree for the sparse shapes a drawing is full of. The door beside the elevator
on T5 has a hatched elevation marker to its right, a left-pointing triangle whose bbox is
mostly the empty wedge above and below its apex: a box drawn around the door clips only that
apex, yet covers 72% of the bbox. On bbox area the marker was kept whole and its hatching —
40 px outside the box — landed in the door's template. On ink it scores 58% and drops out.
The marker's ink comes within 10 px of the door's arc, closer than the door's own parts are
to each other, so no proximity or clustering rule separates them; what does is that the user
only enclosed the tip.

The one thing removed is *foreign text*: a blob belonging to a line of text that continues
past the box edge. Three letters clipped off the end of a note are not part of the symbol; a
label like `X/TY` sitting complete above a marker is. Whether the run finishes inside the box
is the whole test, and it needs no notion of what a symbol looks like. Text lines are found by
plain adjacency — similar height, shared baseline, gap under one character width — which is
enough to chain a sentence and not enough to chain a marker circle to the text beside it.

Measured on T5: a sloppy drag around a grid bubble keeps both blobs, the circle and its `A`.
A box clipping the middle of a word keeps 1 of 18 blobs.

The largest blob in the box is never removed, so a glyph that happens to sit at text height
beside a clipped note cannot be deleted along with it. `union_inside` is the unfiltered rule,
same signature — swapping is a one-line change at the `snap()` call, and
`tests/test_candidates.py` pins a scene where the two disagree so the filter cannot rot into
dead code.

## Counting a symbol

Selection gives you a template. Counting scores it against every candidate on the sheet, picks
a winner per candidate, and bands the result.

**The first class is the interior elevation marker on T5** — a hatched triangle with a
sheet/detail reference beside it. It was chosen over the door deliberately: the door's leaf is
63% eaten by line suppression (585 of 928 px classed as structure), leaving a 2.9%-fill arc
whose radius ranges 62–193 px across the sheet. A door needs the parametric arc detector in
`doors.py`, which is a pipeline special case; the marker goes through the generic path and so
proves the generic path works.

**Scoring is symmetric coverage, not NCC.** The spikes paid 27.5 s for 48-variant NCC over
24 MP. Line suppression has already reduced the sheet to a few thousand components, so the
work is one small array comparison per candidate per variant — 0.02 s for the whole sheet.
The score is `min(forward, backward)`: forward is the share of the candidate's ink that lands
on template ink, backward is the share of the template the candidate explains. Forward alone
rates a bare outline a perfect match for a hatched triangle, because every pixel it has is
explained. Backward alone rates a solid blob perfect for the mirror-image reason. Taking the
worse of the two demands the candidate be neither more nor less than the template, and that is
what separates the marker from the stair hatching. `tests/test_detect.py` pins a synthetic
outline where the two halves disagree, so the backward half cannot rot into dead code.

Measured on T5 with the registered class:

| | score |
|---|---|
| 7 true markers | 0.988 – 1.000 |
| best non-marker (a letter `A`) | 0.808 |

The gap is 0.18 wide, so `counted_at` sits at 0.90 and the review floor just under the `A` at
0.80 — the two `A`s surface for review rather than vanishing. **These are the detector's
numbers, not ground truth**; T5's annotations live with Paing and the counts want checking
against them.

**Two gates, three bands** (`banding.py`). `match` says the ink does not look like the
template; `margin` says it looks like this template *and* another one — the nested-symbol
problem, where a duplex scores 0.816 and the quad containing it scores 0.681 and neither
number is wrong. They fail differently so they stay separate. With one class registered there
is no runner-up, so margin is reported as `None` and the gate is explicitly recorded as *not
evaluated* rather than silently passed. It goes live on its own the moment a second class
lands.

**A template is one connected glyph, and that is a limit, not a preference.** `detect()` scores
one connected component at a time, so a template spanning disconnected blobs has nothing that
could match it — measured on T5, the best score available to any of 4,770 candidates against a
six-blob template was **0.780**, under even the review floor. Such a template reports zero and
cannot say why. `Template.from_selection` therefore keeps the selection's largest connected
glyph and records the rest as context, which the panel states plainly rather than dropping in
silence.

That is right for a *label* — the `C\T9` sheet reference beside the T5 marker sits 14 px off
the triangle and differs on every instance, so it identifies a marker but can never help match
one. It is **wrong for a genuinely multi-part symbol**: a door's arc and leaf, a grid bubble's
circle and letter, a duplex's circle and two lines. Those need matching over candidate
*groups* rather than single components, and until that exists only single-component symbols
are countable. Reported as a gate finding — see `PROGRESS.md`.

**Adding the next symbol should be an entry in `takeoff/classes.py` and nothing else.** The
anchor in a registry entry is a *drag box*, not a tight bbox: it is fed through
`candidates.snap` exactly as a browser selection is, so the registry and the viewer cannot
build different templates from the same glyph. A test asserts the two paths produce identical
detection ids.

## Orientation, and how it is chosen

Nothing measures an angle. `templates.variants()` pre-builds the glyph at every orientation
the class registers — four quarter turns times mirrored, deduplicated — and
`scoring.best_variant()` takes the argmax over that bank. The `elev_marker@180m` on a
detection is **which pre-built variant won**, not an angle estimated from the ink.

Quarter turns go through `np.rot90` and are exact. Any other angle resamples, and a resampled
binary mask of a 43 x 128 glyph loses hatching lines to interpolation, so variants record
whether they are `exact` and an axis-aligned class asks only for the four that are. A class
whose instances sit at arbitrary angles would register a finer `rotations` sweep and pay for
the resampling — nothing else changes.

Symmetric glyphs collapse: identical masks under different transforms are deduplicated, so
the orientation reported for a symmetric symbol is the first transform that produced that
mask, not a claim about how it was drawn.

## Why symbols get missed, and what Candidates is for

**`Candidates` shows every blob of ink the detector can see at all** — the connected pieces
left after walls, grid lines and borders are stripped. Nothing there is matched or judged. It
exists to separate two failures that look identical from the outside and need opposite fixes:
*the detector never saw it* (wrong size band, or line suppression ate it) versus *the detector
saw it and scored it low* (wrong template, or a threshold). A symbol with no amber box around
it was lost before scoring began.

That distinction found the A/T10 marker on T5. It sits inside a dotted circle with the
drawing's centre line running through its apex. Line suppression classes that line as
structure, removes it, and the 3x3 dilation takes the apex junction with it — so a glyph that
is **one component in the raw ink becomes two**, and neither half is the symbol. The same
thing removes the triangle's own vertical back edge, which at 150 px is longer than the
0.30 in structure threshold.

Two things follow, and both are now handled:

- **Detection matches candidate *groups*, not single components** (`detect.candidate_groups`).
  Growth is greedy and bounded twice: a piece joins only if it is within a stroke of the group
  and only if the group still fits a variant's footprint. Every step of the growth is emitted
  as its own group and they compete, so a group that grew too far does not get to be the only
  reading. Competitive assignment then gives each piece of ink to one instance, which is what
  stops a split marker being counted once per half.
- **Selection keeps ink the drawing joined.** `Candidate.raw_id` records which component of
  the *raw* ink a piece came from, so a glyph in pieces is distinguishable from a glyph beside
  a label with no distance threshold: A/T10's halves share a raw id, the `C\T9` label does not.
  Proximity is still required as well, because the long structure lines are themselves ink and
  can join a glyph to something 26 px away through a line that ran across both.

Measured on T5: **8 counted**, one of them reassembled from two pieces, no duplicate labels,
0.5 s. `B/T12` is a real marker that suppression left in three pieces; it reassembles to only
0.820 and lands in review rather than being counted or lost. Still missed: `A/T9`, which is a
larger marker than the template and falls outside the 30% size gate.

`SymbolClass.scales` exists for that case and is deliberately left at `(1.0,)`. Scale must go
in the bank rather than in `size_tolerance`, because the tolerance also bounds group growth —
taking it from 30% to 60% dropped the count from 8 to 4 as groups swallowed their neighbours.
But a second scale did not recover `A/T9` either: it is fragmented into far more than four
pieces. That one is unfinished, and it wants ground truth before it is tuned further.

## What a detection carries

Selecting a match shows its full record: the label, both halves of the score, geometry, and a
magnified crop of the ink that was actually matched.

**The score's two halves are kept, not collapsed.** `match` is `min(forward, backward)`, so on
its own it never says *which way* a near miss failed. The two letter `A`s held for review on
T5 score forward 0.974, backward 0.808 — almost every pixel they have lands on template ink,
but they explain only four fifths of the template. That is a reviewer's distinction, and a
single number destroys it.

**The label comes from the PDF text layer, joined outside the detector.** Every marker on T5
carries a distinct sheet reference — `C\T9`, `B/T9`, `A/T12`, `E/T10` — which is what a person
recognises an instance by, so the hit list shows it instead of an orientation code. The join
lives in `layout.py`, which may import pymupdf; `detect.py` stays raster-only and never learns
that a text layer exists. Detection runs on pixels and has finished before any word is read.

**Proximity alone picks the wrong caption.** The marker at (7502, 2646) has the dimension
string `4"` nearer its centre than its own reference `B/T10` — nearest-word is confidently
wrong on 1 marker in 7. What a caption looks like is per-symbol knowledge, so it arrives as
`SymbolClass.label_pattern` in the registry and `layout.py` stays generic. With no pattern
match nearby the label is `None` rather than a guess, and `nearby_text` still lists what was
actually there.

Crops are fetched from `/api/pages/{n}/crop` on demand and memoised per detection. Embedding
nine previews in a count response is harmless; embedding three hundred for a sheet of
receptacles would send a lot of pixels nobody has looked at.

## Reviewing the result

Counting hands the sheet back rather than taking it over. Pressing **Count these** drops out of
select mode automatically — select mode calls `setMouseNavEnabled(false)`, so leaving it on
froze the drawing at exactly the moment there was something to look at: results drawn, and no
way to pan or zoom to them.

| | |
|---|---|
| **N** / **P**, or ← / → | step through matches, flying to each |
| **A** / **R** | keep or reject the current match |
| **F** | fit the whole sheet |
| **D** | hide the overlay to see the drawing under it |
| **Esc** | leave select mode |

Shortcuts are ignored while a form control has focus, so typing a page number does not step
through matches.

A reviewer's verdict is drawn *on top of* the detector's band colour, never in place of it —
a kept match gets a tick in its corner, a rejected one goes dashed and struck through at 45%
opacity. The two are different facts: the detector's confidence does not change because a
person disagreed with it, and a review that overwrote the band would destroy the evidence for
recalibrating thresholds later. Verdicts key off the detection id, which hashes position and
class, so they survive a re-count of the same page. They are **session-only** — persisting
them is ground-truth work and belongs with `gt/`.

## Opening a drawing

**Open…** takes a PDF or an image. Documents are keyed by content hash, so the same drawing
uploaded twice is one document and every cache under `cache/` is already warm for it.

A PDF and an image differ in exactly three ways, all handled in `raster.py`, and nothing past
that module can tell them apart:

| | PDF | image |
|---|---|---|
| pages | many | one |
| scale | stated by the file, so px-per-inch is derived | **declared** — a scan does not say how big the sheet was |
| resampling | rendered at whatever DPI is asked for | **never resampled** — its pixels already are the drawing |

That last row is a real trap: PyMuPDF will happily open a PNG, but it maps images at 96 DPI,
so asking for 300 upscales a 4600 px scan to 14375 and invents nothing. The first cut of the
tile pyramid did exactly that; `tests/test_sources.py` now pins native resolution.

The declared DPI matters less than it looks. Templates and radius bands are measured from
*your selection*, so only the candidate size band and the readouts in feet depend on it.

Two things a scan genuinely loses, and the tool says so rather than failing quietly:

- **No captions.** `layout.words_px` returns nothing without a text layer, so detections
  arrive unlabelled and the header says *"no text layer, so no labels"*. Reading captions off
  a scan needs OCR, which the tool does not do.
- **No vector bootstrap** for ground truth — annotation on a scan is fully manual.

A registered symbol is still *recognised* on an uploaded drawing: `TemplateAnchor.source`
names the document its reference instance lives in, so the class library is built from there
rather than from whatever you are looking at. Verified on a scan — a door selected on a PNG
comes back as "Single swing door", with the class's calibrated thresholds, not as an unnamed
symbol.

## What happens when you select a symbol

One gesture, five steps, and nothing in between asks you anything.

1. **Snap.** The drag box resolves to the ink you enclosed (`candidates.snap`). Blobs whose
   *ink* is 60% inside are kept; a line of text running out of the box is dropped.
2. **Profile.** `detect.profile_selection` measures what that ink is. A thin blob holding a
   clean circular arc means the symbol is a curve; anything else is a shape.
3. **Identify.** `detect.identify` asks which registered symbol this is — comparing a measured
   radius, or matching the glyph against each registered template bank. A hit brings the
   class's name, thresholds and caption pattern. A miss still counts, unnamed.
4. **Count.** Either sweep the sheet for that radius (`doors.swings_in`) or match the glyph in
   every orientation (`scoring.best_variant` over `candidate_groups`). Every hit competes for
   the ink it claims, so one physical symbol is counted once.
5. **Band and annotate.** `banding.band` puts each result in counted / review / rejected, and
   `layout.label_for` reads its caption off the PDF text layer.

**There is no dropdown, deliberately.** There used to be, and it was a second input that could
disagree with the drag: selecting a marker while it still said "door" applied the door's
0.80/0.60 thresholds to a triangle — 11 counted instead of 8, 32 in review instead of 3, every
result labelled a door. One thing is being pointed at, so one place decides what it is.

## Rasterization: two renders, and they never mix

The page in the browser is **not one image**. It is a Deep Zoom pyramid: OpenSeadragon asks for
512 px PNG tiles over HTTP and assembles the ones the current view needs. T5 is **460 tiles
across 10 zoom levels, 9.0 MB**, built once on first view (~15 s, with a progress bar) and
cached in `cache/dzi/<pdf-hash>/p004/`. At the top level there is a single 1-tile thumbnail;
at the bottom, the full 10800 × 7200.

Detection never sees any of that. It runs on a **separate, full-page greyscale render** at
`DETECTION_DPI` — one 10800 × 7200 array, 74 MB, cached as a PNG in `cache/raster/`. Both come
from the same `page.get_pixmap()` call in `takeoff/raster.py`, at different DPIs and colour
depths, and the tiles are never scored against.

The two are kept apart on purpose. Tiles are RGB and resampled per level, so a glyph at level 8
is not the glyph the detector must judge; measuring one and counting the other would make the
numbers depend on where you happened to be zoomed. Every coordinate crossing between them goes
through `spaces.rebase_px` rather than assuming they share a scale — they do today, and a DPI
change would otherwise silently misalign every box.

## Two detectors, one gesture

Every symbol is selected the same way: drag a box, press **Count these**. What differs is what
gets *measured* from the selection, and that is decided by measuring — not declared per symbol.

| | `template` | `arc` |
|---|---|---|
| chosen when | the selection is a shape | the selection holds a clean circular curve |
| what it matches | rotated, scaled copies of the glyph you selected | a swept circle |
| size policy | `size_tolerance` + `scales` | radius measured from your selection, ±35% |
| used by | interior elevation marker | single swing door |

`detect.profile_selection` runs the arc test over **every piece of the selection**, largest ink
first — not over the glyph `Template.from_selection` would pick. For a door those differ: the
arc is thin, so a keynote bubble inside the swing is the larger blob and would be chosen as the
glyph while the arc, the thing actually pointed at, went unexamined. Density is what refuses
the other direction: a circle can be fitted through the elevation marker's diagonals, but it
fills 24% of its box, and shapes are matched as shapes.

`SymbolClass.detector` defaults to `"auto"`. Naming `template` or `arc` pins it, which is for a
symbol whose reading is known and must not drift — not the normal case. **Leaving it alone is
what lets a symbol nobody anticipated work without an edit**, and the door class pins neither
the detector nor the radius: a set drawn at another scale needs no change here.

There is one construction path. `build_entry` snaps the registry anchor and hands it to
`entry_from_selection`, so the tests exercise exactly what a person gets.

**Why doors need the other detector.** Not scale: every door on T5 is one width, fitted radius
107-121 px, a 1.1x spread, 3'-0" at 1/8in. It is that a template asks a candidate to be
neither more nor less than the reference, and line suppression leaves between 144 and 1640 px
of a door behind depending on whether its leaf survived, whether a dotted demolition line ran
through it, whether a keynote bubble sits in its swing. `scratch/spike8_doors.py` has the
measurements. A circle is invariant to all of it.

**The sweep is a grid, not RANSAC.** The spike used random sampling and returned 29-30 swings
across five seeds while agreeing with itself on only 82% of the set. Decision 10 needs ids
stable across re-runs, so `doors.find_arc` sweeps a fixed grid of centres and radii instead:
coarse pass, then refine around its best few. Same ink, same answer, every time. Sorting each
centre's distances once turns the radius sweep into two binary searches per radius, which with
the coarse pass took one sheet from 151 s to 8.5 s.

**A ring is refused before the sweep runs.** A full circle can always be re-read as a partial
arc — from a centre a few pixels off, the ink at any one radius forms a contiguous segment,
and on a 110 px grid bubble that segment measures a clean 140 degrees at occupancy 1.0. Span
and occupancy cannot refuse it; only noticing that the blob closes on itself can.

## Occlusion: repairing what suppression broke

A line drawn across a symbol is common on these sheets, and it breaks detection two different
ways.

**Severed.** The crossing line is long and axis-aligned, so suppression classes it as
structure and removes it — taking a slice of the symbol with it. The A/T9 marker on T5 is cut
into 30 pieces this way and was not findable at all.

**Contaminated.** The crossing line is diagonal, so suppression keeps it and it merges into
the symbol's component. The E\T9 marker has a leader arrow drawn straight through it.

`ink_layers` now **repairs** the first case: close small gaps in the symbol layer, then keep
only the closed pixels that were real ink. It cannot invent a symbol and cannot bridge a gap
wider than a line, so a marker and its label 14 px apart stay apart. Measured on T5:

| | before | after |
|---|---|---|
| A/T9 (line through it) | not findable | **0.904, counted** |
| B/T12 (line through it) | 0.820 | 0.868, review |
| A/T10 | 2 pieces, needed group matching | **one component, 1.000** |
| letter-`A` false positives | 0.808 | 0.792, *rejected* |

Real markers up, false positives down, and the symbol that needed reassembly no longer does.
Markers go 8 → 9 counted.

**Repair is per class, because it is not free.** It helps a matched *glyph* and hurts a swept
*arc*: the ink it restores beside a door swing is the jamb, which thickens the curve the sweep
measures and can merge the arc into the wall. On T5 that cost one real door, so `door_swing`
sets `repair_gap_px=0` and candidates are cached per (page, repair). The registry was already
the place per-symbol policy lives.

**Contamination is not fixed, and is handled honestly instead.** An arrow drawn through a
marker leaves ink no template can explain; forward coverage drops and the score follows. Those
land in **review**, not rejected — `B/T12` at 0.868 is a real marker the tool can only partly
see, and surfacing it for a person is the right answer. `E\T9` is worse: the arrow merges it
into a blob that fails the size gate outright, and it is still missed.

## T4, and the chair that is a perfect door

T4 carries three plan viewports at three scales and is full of furniture. **An office chair's
back is a continuous quarter-circle, one stroke wide, at very nearly a door's radius.** On
geometry alone it is a flawless door swing, and 17 of them were counted as doors.

What separates them is not a shape but a mechanism: **a door swings about its hinge, and the
hinge is a drawn jamb; a chair back curves about the middle of a seat, which is empty.**
`Arc.anchor_ink` samples a 0.027 in disc at the arc's centre of curvature, read from the *raw*
ink because a hinge is part of a wall and the wall is what line suppression removes.

The test is **learned from the selection, not hard-coded**. If the instance you selected
pivots on drawn ink, matches must too; if it does not, the test is not applied — so a chair
stays countable if a chair is what you selected. Nothing in `doors.py` knows what a door is.

Measured: on T4 the best chair reaches 0.343 and the weakest door 0.554; on T5 the weakest
door is 0.394, so the threshold sits at 0.37. **That margin is 0.05 — far tighter than the
score gaps elsewhere here, and drawn from two sheets.** It is the weakest number in the
project and wants re-deriving from ground truth rather than trusting.

**Identification could not work off the anchor sheet.** `identify` rebuilt each class on the
current page, and every class is anchored on T5, so every class was skipped and every door on
T4 came back "not a symbol registered yet" — losing its name, its caption pattern and its
calibrated thresholds. Reference entries are now built once per class from *its own* anchor
page and passed in; `takeoff/` cannot render a second page, so the server owns that library.

**Known limitation: one count covers one scale.** The radius band is measured from the
selection at ±35%, and T4's viewports differ threefold. Selecting a door in the large viewport
counts that viewport's 26; selecting one in the small viewport counts that viewport's. There
is no single pass over the sheet, and no warning that other-scale arcs were skipped —
`out-of-scope matches reported in a labelled bucket` (decision 4) is not implemented for
scale. Nothing about this is fixed by tuning; it needs the count to carry a scale per region.

## Judging an arc, not the blob it landed in

The door to room 217 was missing, and the swing itself was flawless — a continuous 95-degree
arc at 3.1 ft. What sank it was a **wall jamb sharing its connected component**: 1,059 of its
1,357 ink pixels are not on the arc, so every measure that compared the arc against its whole
blob scored it **0.47** and rejected it.

Two such measures were tried and both are wrong for the same reason. *Share* (the arc as a
fraction of the blob's ink) punishes a door for anything the drawing merged into it. *Stray*
(ink outside the swept circle) does the same, and scored it lower still at 0.25.

`Arc.quality` now looks only at the arc: **is it continuous, and is it one stroke wide.**

| | measure | real doors on T5 | everything else |
|---|---|---|---|
| continuity | `occupancy ** 4` | 1.00 exactly, all 29 | 0.75–0.93 |
| thinness | ink px per unit of arc length | 1.14–1.52 | 2.09–3.03 |

Stroke ratio is the measure that survives contamination: a drafted arc is one stroke wide
however much else is in its blob, while a circle threaded through a keynote ellipse or a
corner of leader lines collects two to three times the ink. Occupancy is raised to the fourth
power rather than gated hard, so a door with a stop symbol drawn across its swing is scored
down into review instead of vanishing.

Two other gates moved with it, each on a measured gap: the span floor from 55 to 65 degrees
(29 real doors span 70–95; the single detection at 55 was a leader line), and the thresholds
to 0.72 / 0.50. The result is **29 counted, all verified by eye, best non-door 0.46** — a
0.29 margin where the first attempt had 0.03.

The same `share` gate was also in `profile_selection`, where it made two of the 29 doors
**unselectable**: their arcs are a small part of their blobs, so the profiler read them as
shapes. Both places now ask the same question of an arc.

## The bug behind all of it

Doors were unselectable before any of this worked, and it did not look like a selection
problem. A door's arc carries **210 ink px** while the `EX` label beside it carries **254**, so
ranking blobs by ink made the letter the primary, the arc lost the protection that keeps a
symbol out of the text filter, and the filter deleted the door and kept the label. **7 of 27
doors could not be selected at all**, and the symptom — "the detector says template when I
drag a door" — pointed at the wrong module entirely.

`inside_minus_foreign_text` now protects both readings of "the thing pointed at": the densest
blob in the box and the largest. For a solid glyph they are the same blob and nothing changes;
for a thin curve they are not. With that fixed, all 27 doors profile as arcs from a sloppy drag
and every marker still profiles as a shape.

## Doors: one class, not seven

The T3 door legend has seven entries, but they differ on three axes at once — shape, lifecycle,
and a keynote type code. Seven entries collapse to **three shapes** (single swing, bi-fold
chevron, double leaf) plus a dashed demolition variant; one entry is not a door type at all but
a placement rule. Only single swing is registered so far.

The other two axes are reported per detection rather than split into classes:

- **Type code** comes from the text layer, exactly as a marker's sheet reference does. On T5:
  25 `EX`, 1 `WS/PA`, 1 with no code. `SymbolClass.label_pattern` is restricted to the
  legend's vocabulary on purpose — a bare two-by-two letter pattern also matches `GS/GC`, a
  *finish* keynote, and two of those sit inside a door's swing.
- **Lifecycle** (existing / new / demo) is decision 8's first-class field and stays for
  `lifecycle.py`.

The decisive argument against a class per legend entry is that the drawings do not populate
one: **11 keynote bubbles against 27 doors on T5, and none at all on T6-T8.** Most doors carry
no type code, so most instances would land in an "unknown type" bucket, and the surveyor's
actual question — how many doors, how many are new — would have to be reassembled from seven
counts.

## The ink that hides a symbol

The sweep in `doors.py` ranks circles by how much ink sits on them, which is the right first
guess and the wrong final answer when something denser than the swing shares the component.
Both `RE/EX` doors in room 218 on T5 have a keynote ellipse touching the arc: the sweep locks
onto the top of the bubble at stroke ratio 2.8, `Arc.quality` correctly scores that 0.000, and
a real door is lost with nothing to say a good arc was underneath it.

`find_swing` deletes the refused fit's ink and sweeps what is left, up to three rounds. Both
doors come back at radius 117 px — **3.1 ft, the same width as the 29 that never needed it**,
and agreement with the rest of the sheet is the reason to believe them. T5 goes 29 → 31.

Two cheaper fixes were tried first and are worth recording, because both look obviously right:

| | |
|---|---|
| rank circles by `quality` instead of ink | recovers both doors, **loses 15 of 35 arcs**. `quality` is scale-free, so a small clean arc anywhere in a blob also scores 1.000, and the widths it returned were 3.5–3.8 ft rather than 3.1. |
| keep the top 12 by ink, take the best by quality | safe, but **does not recover them** — the refine pass fills the shortlist with near-duplicates of the ellipse fit. |

Peeling costs 2.4x on the arc path, and only on blobs that fail: a fit that passes first time
is returned untouched, byte-identical, which `tests/test_doors.py` pins because detection ids
hash position and review state is keyed on them.

## A sheet is not all drawing

T4 carries two plan viewports, two columns of general notes, a legend and a title-block strip.
Only the viewports can hold an instance of anything, and **2,722 of its 5,775 candidates (47%)
are sheet furniture**. `regions.py` segments the sheet — remove runs over 6 in, dilate by a
0.30 in gutter, label — and classifies each block.

**Not by text density.** `scratch/viewport2.py` calls a block text above 150 characters per
square inch and by that test every block on both sheets is a drawing: measured densities are
7–9 in the plan viewports and 44–49 in the notes. The signal is real, the constant is not.
What separates them is that **set type is all one height and a drawing is not** — the share of
a block's components within 20% of its median height runs 0.31–0.48 for viewports and
0.66–0.97 for text, so the gate sits at 0.57.

**Measured off the raster, never off the text layer.** A gate built on `get_text` would give a
PDF and a scan of the same sheet different candidates, and the detector is not allowed to tell
them apart.

Counts are unchanged on both sheets — this removes work, not symbols. The template path on T4
goes 1.08 s to 0.49 s; the arc path gains nothing, because thinness already excluded set type.
A block with fewer than 20 components stays `unknown` and is treated as drawing: refusing to
guess must never cost a symbol. Selection still sees the whole sheet, so a legend entry can
still be dragged and a template built from it.

## Coordinate spaces

Three of them, and mixing two is the failure mode that produces confidently wrong numbers
rather than an exception. Measured on page 5 (T5, rotation 270):

| Space | Extent | Who uses it |
|---|---|---|
| `page_pt` | 1728 × 2592 pt, portrait | `get_text`, `get_drawings` — **always** |
| `sheet_pt` | 2592 × 1728 pt, landscape | `page.rect`, render clips, what a human sees |
| `px` | e.g. 10800 × 7200 at 300 DPI | the detection raster and the tile pyramid |

The first two are permutations of the same numbers, so a coordinate from the wrong one lands
inside the page and looks plausible. `tests/test_spaces.py` pins this down by transforming
every text box on T5 into pixels and checking it lands on ink (coverage 0.42), then repeating
with the two spaces confused and checking it lands on blank paper (0.02) — so the test can
actually fail, rather than passing for any invertible pair of transforms.

There is a second instance of the same hazard inside `px`: the detection raster and the tile
pyramid are both pixel spaces, and they share a DPI today, so code passing coordinates
straight between them would be accidentally right. Every conversion goes through
`spaces.rebase_px` instead, and `tests/test_server.py` checks the overlay boxes land on ink —
then rechecks them at half scale and offset by 40 px to show the test can fail.

Note: `scratch/viewport2.py` has this bug. Its region geometry is sound and worth porting;
its `caption()` passes a raster-derived rect into `get_text(clip=...)`, which reads `page_pt`.

## The rule the tests enforce

`raster.py`, `spaces.py`, `layout.py`, and `vector_gt.py` may import `pymupdf`. No other
module in `takeoff/` may, directly or through a chain of first-party imports.
`tests/test_raster_only.py` walks the AST to prove it and names the import chain when it
fails, so the brief's constraint is a CI failure rather than a review habit.

## Design decisions

Recorded in `PROGRESS.md`; the executable plan lives at
`~/.claude/plans/crystalline-gliding-bear.md`. The two that shape everything else:

- **Competitive assignment, not per-template thresholds.** A duplex receptacle is geometrically
  a subset of a quad; measured margin against the duplex template is 0.816 vs 0.681, too thin
  to threshold. Matches are assigned by argmax across the template library plus a margin test.
- **Two confidence numbers, `match` and `margin`.** They fail differently, so they stay
  separable, and drive three bands — counted / review / rejected — through two independently
  settable gates.
