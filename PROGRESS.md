# Symbol Spotter — session handoff

Last updated 2026-08-26. Research + spikes complete; no production code yet. Ready to build.

**Executable plan:** `C:\Users\paing\.claude\plans\crystalline-gliding-bear.md`  <- start here
**Narrative version:** https://claude.ai/code/artifact/4fd71584-3fad-4549-bfb0-d67e9bb2f833

## Build is GATED

Stop at each gate, report what works and what deliberately does not, hand over test instructions, and
wait. Detectors are built **one symbol class at a time** — Paing names the symbol, it goes fully
end-to-end, then stop and test before the next.

  Gate 1  foundations + viewer                      DONE
  Gate 2  symbol selection UI                        DONE
  Gate 3  ground truth on T5                         TOOLING DONE - awaiting annotation
  Gate 4  first symbol end-to-end                    DONE - interior elevation marker
  Gate 5     single swing door                       DONE - parametric, see doors.py
             selection is one gesture for every symbol; the DETECTOR is measured from what
             was selected, not declared. classes.SymbolClass.detector defaults to "auto".
  Gate 6..N  one symbol per gate                     <- Paing picks the next symbol
  Final   hardening, robustness, CI

Gate 3 was skipped ahead of Gate 4 because the ground truth for T5 has not landed in the repo
yet. That means the elevation marker's counts (7 counted, 2 review) are the detector's own
numbers and have been graded by eye against a contact sheet, not against annotations. The eval
harness stays empty until real ground truth arrives -- do not seed it from detector output.

Adding a symbol must be a registry entry in `takeoff/classes.py`, not a pipeline change. If it isn't,
report that at the gate — it means the core is under-general.

As of Gate 5 an entry need not even name its detector: `profile_selection` measures the selection and
picks. A curve is swept for at the radius it measured; anything else is kept as a glyph and matched.
That is the property to protect — it is what lets an unseen symbol work with no edit at all.

---

## Environment

Already set up in this directory:

```
.venv/                 Python 3.14 venv
                       pymupdf 1.28.2, opencv 5.0.0, numpy 2.5.2, pillow
```

Run anything with `.venv/Scripts/python.exe <script>`.

Two gotchas that will bite on a fresh start:

- `import fitz` warns it is deprecated; `import pymupdf` is the current name.
- Console output is cp1252 on this machine, so any script printing the symbol glyphs
  (⊖ ⊕ ▲) needs `sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')`.

---

## Decisions locked with the client

1. Local web app (FastAPI + OpenSeadragon over DZI tiles), not Qt, not a notebook.
2. Classical detection with competitive assignment; learned embedding stays pluggable.
3. The user's symbol click declares the count scope; region shown as a named editable chip.
4. Out-of-scope matches reported in a labelled bucket, never dropped.
5. No recursive XY-cut splitter — dilate-and-label segmentation plus a per-class size filter.
6. Ground truth bootstrapped from the vector layer, then human-reviewed. Proposed, never trusted.
   Generated lazily per sheet, starting with T5 (page 5).
7. Receptacle variants are separate classes (duplex, quad, GFCI, floor-mounted).
8. Lifecycle (existing/new/demo) is a first-class output field with its own accuracy metric, read from
   the stroke core — anti-aliased edges of a black line are also mid-grey.
9. Two confidence numbers: `match` and `margin`. They fail differently and stay separable.
10. Detection IDs hash position + class, so review state and golden counts survive a re-run.
11. Three bands (counted/review/rejected), two gates, user-settable thresholds — global default with
    per-class override, seeded from the eval harness. Okabe-Ito colours: #0072B2 counted,
    #E69F00 review, #CC79A7 excluded, #999999 rejected.
12. Gated build, one symbol class at a time (above).
13. Real-world units (feet, building coordinates) deferred.

Ground truth must never require typing coordinates: the tool itself is the annotation tool.

**Hard architectural rule:** detection modules may never import `pymupdf`. Only `raster.py`,
`spaces.py`, `layout.py`, `vector_gt.py` may. Enforced by `tests/test_raster_only.py` walking the AST.

---

## Open questions

Only one remains: **real-world units** (feet and building coordinates derived from the viewport scale).
Paing deferred it until the core works — revisit rather than assume.

Unresolved and worth raising before it matters: who arbitrates when ground truth and the tool disagree
on a genuinely ambiguous instance (occluded arc, receptacle over a dimension string).

---

## Key measurements (all from Skanksa.pdf)

| Fact | Value |
|---|---|
| Sheets | 28, all 2592x1728 pt (36"x24"), rotated 90/270, born-digital vector |
| Plan region of one sheet at 300 DPI | 6450x3750 px = 24.2 MP |
| Full sheet at 400 DPI | 138 MP (414 MB RGB) — tiling is mandatory |
| Duplex receptacle glyph | 0.092" diameter = 28 px at 300 DPI |
| Line suppression | 0.1 s, removes 81% of ink, leaves ~1,900 candidates |
| Brute-force 48-variant NCC over 24 MP | 27.5 s |
| Nested-symbol margin (duplex 0.816 vs quad 0.681) | **0.135 — too thin to threshold** |
| E4 schematic receptacle (pictorial NEMA faceplate) | 0.93" x 1.41", 10-15x plan glyph |
| Vector motif clustering on E4 vs raster detection | **90 vs 92** — independent methods, agree within 2 |
| Duplex = 3 vector paths | 7.32pt circle + two 11.16pt parallel lines |
| Exact signature hashing failure | split 101 duplexes into 90+11 over a 0.2pt stub difference |
| Proximity clustering failure | chained neighbours into 11- and 17-path blobs in dense areas |
| Degenerate vector paths on page 5 | 1,007 zero-length lines, must be filtered |

Sheet inventory: plans at 1/8"=1'-0" on pages 5-8 (T5-T8), 14-15 (M2-M3), 18-20 (P2-P4), 24-26 (E2-E4).
Legends with SYMBOLS LIST on pages 13 (M1), 17 (P1), 23 (E1) — these seed the template library.
Page 4 (T4) is the adversarial case: three plan viewports at three scales, one labelled NOT IN THE SCOPE.

---

## Spike scripts in `scratch/`

Throwaway code — keep for reference, do not build on it.

| Script | What it does | Status |
|---|---|---|
| `spike.py` | Multi-angle NCC over E4 plan, NMS, overlay | Works. 92 detections at thr 0.65 |
| `spike2.py` | NCC vs ink-IoU on a duplex and a quad patch | Works |
| `spike3.py` | Coverage precision/recall at the NCC argmax | Works |
| `spike4.py` | Hull-restricted precision | **Buggy** — returns 0, unfixed, superseded by spike5 |
| `spike5.py` | Core-disc restricted precision | Works; margin still too thin |
| `spike6.py` | Line removal + connected-component statistics | Works. The important one |
| `spike7.py` | Scoring on line-removed ink | Works for 2 of 3 candidates; 3rd coord is bad |
| `vecgt.py` | Vector motif clustering for ground truth | **Works** — 90 duplexes on E4; needs tolerance + anchor grouping |
| `viewport.py` | First segmentation attempt | Fails — sheet border merges everything |
| `viewport2.py` | Segmentation with rule removal | **Works** on pages 4 and 5 — use this approach |
| `spike8_doors.py` | Template vs parametric arc for doors on T5 | **Decisive** — doors need `doors.py`, see below |
| `xycut.py` / `xycut2.py` | Recursive XY-cut splitter | **Rejected** — regressed pages 4/5, see decision 5 |

Rendered PNGs in `scratch/` are visual evidence for the findings above; `sym_crop.png` in particular
shows line suppression cleanly isolating door arcs, receptacles and markers.

---

## Next step

Gate 4 is done and the generic pipeline held: the elevation marker needed a registry entry in
`classes.py` and no pipeline change, which is the property that was meant to be tested.

Two things are deliberately not done, and both want Paing's input:

1. **Ground truth for T5.** Until it lands, every count is ungraded. It is the blocker for the
   eval harness and for re-deriving thresholds instead of eyeballing them.
2. **The margin gate is dark.** With one class registered there is no runner-up, so `margin` is
   reported as `None` and banding records the gate as unevaluated rather than passed. The
   nested-symbol problem (duplex 0.816 vs quad 0.681) is untested until a second class exists.

**Gate finding (RESOLVED): group matching landed.** `detect.candidate_groups` now scores sets of
nearby components, bounded by the bank's footprints and by MAX_GROUP_PARTS. This was forced by
a second, independent bug: the A/T10 marker on T5 is one component in the raw ink and two after
line suppression removes the centre line crossing its apex. Doors, grid bubbles and receptacles
should now be matchable without further pipeline work -- untested until a second class lands.

**DONE (Gate 5). Doors need `doors.py`; the template path cannot do them (spike 8).** Not for the reason
previously assumed. Every door on T5 is one width — fitted radius 107-121 px, a 1.1x spread,
3'-0" at 1/8in scale — so the "3x radius spread" quoted earlier was thin blobs generally, not
verified arcs. The template path fails on something tuning cannot reach: ink per door varies
**11.4x** (144-1640 px) because line suppression leaves a different amount of each one, and
symmetric coverage asks a candidate to be neither more nor less than the template. Only 47% of
door-to-door pairs clear 0.90, and recall runs 0%-68% purely on which instance was dragged.

A RANSAC circle fit over each thin blob finds 29 swings in 0.83 s, all confirmed by eye,
including the ones merged with a keynote bubble or dotted demo line. It is stochastic though —
82% set agreement across seeds — so a production version must be deterministic (radius-grid
sweep, or hypotheses seeded from blob geometry) to satisfy decision 10's stable ids.

**Door selection has its own problem, before detection.** Dragging round the door beside the
elevator on T5 snaps to the GS/GC keynote bubble sitting inside its swing, not to the arc.

**Open: T4 counts one scale at a time.** Its three viewports differ threefold in door radius,
and the band is measured from the selection at +/-35%. Selecting a large-viewport door counts
that viewport's 26 and silently ignores the rest. Decision 4 says out-of-scope matches go in a
labelled bucket, never dropped -- that is not implemented for scale, and tuning will not do it.

**Open: the door anchor threshold is the weakest number in the project.** 0.37, from a 0.05
margin (best T4 chair 0.343, weakest T5 door 0.394) over two sheets. Re-derive it from ground
truth before trusting it.

**DONE: both `RE/EX` doors in room 218 on T5 are counted.** Paing found them missing. It was
not a candidate-generation failure -- the candidate exists, 935 ink px at 2.9% fill -- but an
arc-SELECTION failure: `find_arc` ranks hypotheses by inlier count at every stage, and each of
these doors has a keynote ellipse touching its swing and sharing its component, so the sweep
returns the ellipse's top edge at stroke ratio 2.85 and `Arc.quality` refuses it. `find_swing`
peels the refused fit's ink and re-sweeps, up to 3 rounds. T5 29 -> 31 counted, both at radius
117 px = 3.1 ft, matching the sheet's other 29. T4 unchanged at 25, so no chair became a door.
Ranking by quality instead was tried and LOSES 15 of 35 arcs -- quality is scale-free, so any
small clean arc scores 1.000. Do not revisit that one.

**DONE: sheet furniture is out of the counting pool.** `takeoff/regions.py` segments a sheet
into blocks and classifies each as drawing or set type from component-height uniformity
(viewports 0.31-0.48, notes columns 0.66-0.97, gate 0.57). On T4 that is 47% of candidates;
on T5 14%. Counts identical on both. The T4 template path goes 1.08s -> 0.49s; the arc path
gains nothing because thinness already excluded text. viewport2.py's 150 chars/in2 classifier
does NOT transfer -- it calls every block on both sheets a drawing. Measured off the raster
only, never the text layer, or a PDF and a scan of one sheet would get different candidates.

**DONE: a door dragged in the viewer is recognised as a door.** Found while verifying the
above. `/count` identified a selection only on the DEFAULT segmentation, and the door class
turns repair off precisely because repair merges its thin arc into the jamb -- so on the
default pool a door's arc is not a candidate, the profiler saw only the keynote bubble, and
every door came back "not a symbol registered yet" with generic thresholds instead of the
class's. `-m eval.suites` was right all along because it builds each class on its own ink.
`_identify_anywhere` now tries each registered class's segmentation, default first.

**Open: peeling costs 2.4x on the arc path.** T5 5.5s -> 13.1s, T4 19.6s -> 52.7s for the raw
sweep. Only blobs that fail their first fit peel, so the 29 easy doors are free. If it needs
to come down: skip peeling when the first arc already explains most of the blob, or cap the
rounds at 2 and give up the second RE/EX door.

**DONE: an occluded symbol is found inside the blob it is fused to.** `detect.fused_windows`
slides the class's own footprint across any blob too big to BE the symbol and keeps the best
window. This was the E\T9 case, and the diagnosis it was filed under was wrong: the marker is
not shattered into too many pieces, it is welded to the leader drawn through it -- one blob of
116x146 px where a marker is 44x129, which the size gate refuses, so nothing ever scored it and
no threshold could have reached it. Whole-blob 0.504, best window 0.960. T5 markers went 9 -> 10
counted with precision still 1.000, and occluded recall went 0.000 -> 0.500, the first occluded
instance the tool has ever found. Costs ~15 s on T4, nothing measurable on T5.

Two things were tried first and reverted, both aimed at the wrong failure: growing groups past
MAX_GROUP_PARTS when the pieces shared a raw-ink component, and completing an undersized
fragment by adding neighbours that explain more of the template. Neither moved a single
instance. Greedy growth adds the neighbour that costs the least bounding box, which for a
symbol in pieces walks up the nearest wall -- the 4-part group at the (9185, 2299) marker grew
to (9189, 2241, 20, 74), away from the glyph.

**Open: the other occluded marker sits in review at 0.831.** Found now, not counted -- see the
gate measurement below.

**Superseded: A/T9 on T5.** It is a larger marker than the registered template and
falls outside the 30% size gate. `SymbolClass.scales` was added for exactly this and is left at
(1.0,) because a second scale did not recover it -- suppression leaves that instance in far more
than MAX_GROUP_PARTS pieces. Wants ground truth before further tuning; do not chase it by
loosening thresholds, which measurably makes the sheet worse (30% -> 60% tolerance took the
count from 8 to 4).

**Open: the counting gates, now measured rather than eyeballed.** With T4 and T5 annotated,
every detection above the review floor can be scored against truth:

| class | real instances | worst real | best non-instance | today's gate | best gate |
|---|---|---|---|---|---|
| `door_swing` | 55 | 0.751 | **1.000** | 0.72 | 0.751 -> 55 TP, 1 FP, 0 FN |
| `elev_marker` | 12 | 0.831 | 0.825 | 0.90 | 0.83 -> 12 TP, 0 FP, 0 FN |

Two readings, and they point opposite ways. The door gate is already right and moving it can
achieve nothing: the one false positive on T4 scores a **perfect 1.000**, so no threshold
anywhere separates it -- it is a shape problem, not a confidence problem. The marker gate at
0.90 is measurably too high: it costs 2 of 12 real instances, both of which sit in review.
0.83 scores perfectly on both sheets, but the gap under it is **0.006** -- five of the eleven
non-instances score 0.800-0.825, and four of those appear at IDENTICAL coordinates on both
sheets, which means they are sheet furniture that `regions.segment` is not excluding. Widening
that gap by fixing the regions is the robust move; dropping the gate onto a 0.006 margin is
not. Left at 0.90 deliberately, with the two real instances visible in review, until the
furniture is out of the pool. Do not move it without re-running this measurement.

**Not a marker:** the two `D/T10` strings on T5 are cross-references inside a note ("SEE 1 &
D/T10"), not callouts. A sheet reference in the text layer does not imply a marker, so the count
of references is not a ground-truth count of markers.

**Superseded finding: the core was under-general in one specific way.** `detect()` scores one
connected component at a time, so only single-component symbols can be counted. A template
spanning disconnected blobs is unmatchable in principle -- best available score on T5 was
0.780 against 4,770 candidates, below the review floor. Found when Paing selected the
elevation marker together with its `C\T9` sheet reference and got a silent 0/0.

Handled for now by taking the selection's largest connected glyph and reporting the rest as
context, which is correct for a per-instance label but wrong for a real multi-part symbol.
Lifting it means matching over candidate *groups* -- cluster candidates in a neighbourhood,
union their masks, score the group. That is the prerequisite for doors, grid bubbles and
receptacles, so it probably has to land before Gate 5 rather than after.

The door is the natural second class and is the harder one on purpose: its leaf is 63% removed
by line suppression and its arc radius spans 62-193 px, so it needs the parametric detector in
`doors.py` rather than the template bank. That is the test of whether the core is under-general.
