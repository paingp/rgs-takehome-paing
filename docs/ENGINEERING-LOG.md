# Symbol Spotter — engineering log

> **This is a working log, not documentation.** It is the record of what was measured, what was
> tried, and what was tried and abandoned, written as the work happened and appended to rather
> than tidied. Entries near the top are the oldest; the newest work is further down.
>
> If you want to know **what the tool is and how to run it**, read [`../README.md`](../README.md).
> If you want to know **how it works and why it was built this way**, read
> [`../ARCHITECTURE.md`](../ARCHITECTURE.md). Come here for the evidence behind a specific
> number, or to find out whether an idea has already been falsified.

Research + spikes complete; no production code yet. Ready to build.

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
  Gate 6     duplex receptacle                       COUNTING, ungraded - E4 needs annotating
  Gate 7..N  one symbol per gate                     <- Paing picks the next symbol
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

## Gate 6 report: the duplex receptacle

**Counting on E4: 96 at the 0.95 gate, 57 in review.** Against 92 from `scratch/spike.py`'s
NCC over the same plan and 90 from vector motif clustering -- three methods sharing no code,
agreeing within six. Ungraded: E4 has no annotations, so `counted_at=0.95` and
`review_floor=0.85` are seeded from where the score distribution falls away (42 instances at
1.000, 96 >= 0.95, 99 >= 0.92, 103 >= 0.90, 121 >= 0.88) and must be re-derived.

**The gate's own question -- registry entry or pipeline change? -- answered honestly: BOTH,
twice.** Neither was a threshold; both were assumptions absorbed from two architectural
sheets, and this is the first electrical one.

1. **Ink is not nearly black.** Electrical devices are drawn on a thin CAD layer: this glyph's
   darkest pixel is 202 and its MEDIAN is 232, against a global cut at gray < 230. The default
   caught 76 of its ~279 px and left it as nine fragments of 11-18 px; `from_selection` kept
   the largest, and a template that size matched **2,312** things on the sheet. Fixed as
   `SymbolClass.ink_threshold`, a per-class knob beside `repair_gap_px`, because lineweight
   follows the symbol's CAD layer. Lowering it globally is NOT the fix and the harness proved
   it: at 15 the extra faint ink merges neighbouring components and T5 loses two doors and a
   marker. Ink coverage barely moves and E4's candidate count even FALLS, so neither is a
   proxy for accuracy.
2. **A plan viewport does not always look like a plan viewport.** E4's architectural
   background is screened, its device glyphs and text sit at similar heights, and the
   height-uniformity classifier reads all 50 MP of the plan as a notes column -- leaving 593
   of 6,565 candidates, none of them receptacles. The anchor glyph scored 1.000 against its
   own template while being absent from the sheet's detections entirely.

   `regions.countable` now refuses to delete a sheet: past `MAX_REMOVED = 0.75` it stands down
   and counts everything. That is a guard, not a fix -- the classifier is still wrong on E4.
   It is the right shape of guard because the gate was only ever an optimisation (47% of T4,
   14% of T5, identical counts either way), and an optimisation that can silently return an
   honest-looking zero has to fail safe.

   Structure coverage looked like the obvious replacement statistic and is not: E4's plan is
   2.51% structure, LOWER than T4's notes columns at 2.79% and 5.44%, because a screened
   background is barely ink at all. Whatever separates these sheet families, it is not that.

**Not on these sheets: the quad receptacle.** E1's legend draws four receptacle types --
duplex (5.64 pt circle + 2 lines), quad (the same circle + 4 lines), and switched versions of
both -- so duplex and quad are genuinely different shapes and would be two classes under the
rule the door gate set. But **no quad appears on any electrical plan in this set.** E2, E3 and
E4 are all of them, and three independent searches agree:

* ranking E4's candidates by `Score.asymmetry` against the duplex template -- what a nested
  symbol produces, template fully explained and extra ink carried -- returns text, not quads;
* counting circles with four PARALLEL prongs in the vector layer finds 17 on E4, all of them
  mechanical equipment (a cross inside a square is a diffuser, not a receptacle), and none at
  all on E2 or E3;
* the 96 counted duplexes have a tight unimodal ink distribution -- median 183 px, 90th
  percentile 193 -- with no second population where a quad's ~1.5x ink would sit.

The legend is a firm-wide standard block; this project does not use the symbol. Same class of
finding as the `D/T10` cross-references on T5: what the drawing SAYS exists is not what it
draws. Do not register a quad against the legend glyph -- it is drawn at a different scale
from the plan symbol and there would be nothing on any sheet to validate it against. Register
it when a drawing that uses it turns up.

**What the review band on E4 actually holds:** 67 items, and the receptacle-shaped ones are
OCCLUDED duplexes -- a heavy black bar drawn across the light glyph, scoring 0.885-0.932
instead of ~1.0. The rest are bold text (`W`, `M`, a hexagon tag). So E4's occlusion story is
the same one T5 had, and the same `fused_windows` machinery is what will or will not reach it.

**GRADED. E4 is annotated: 132 duplexes, 13 occluded.** First numbers, and they are not a
threshold problem:

```
receptacle_duplex   96 TP   0 FP  36 FN   P 1.000  R 0.727  F1 0.842   review 67
  occluded only      0 of 13
```

Only **102 of the 132 real instances are scored at all** -- 30 produce nothing above the
review floor, and 12 of the 13 occluded ones are among them. Moving the gate cannot reach
those: the best gate on this evidence is 0.895, which buys 5 instances for 4 false positives
(F1 0.842 -> 0.852) and gives up perfect precision to do it. Left at 0.95 until the 30 are
understood, because a threshold that trades precision for a fifth of the missing recall is
treating the symptom. The review band's real content is already known from the crops: occluded
duplexes with a heavy black bar drawn across them, plus bold text.

**The 36 misses, bucketed.** Only three are reachable by any threshold:

```
blob too BIG -- fused with something          7 clear   7 occluded
blob too SMALL -- shattered                   7 clear   1 occluded
no candidate at all                           6 clear   5 occluded
scored into review (0.88-0.91)                3 clear   0 occluded
```

The third bucket is not missing ink: those receptacles are fused into components of 202-421 px,
past the 201 px candidate cap (`SYMBOL_BAND_IN`), so they never become candidates -- two of them
share one blob. Together with the first bucket that is **~25 of 36 misses from one cause: the
glyph welded to neighbouring ink.** Same failure that cost T4 three doors and T5 two markers.

**Five fixes measured, all rejected. Do not spend another session on these.**

| tried | result |
|---|---|
| raise `MAX_FUSED_FOOTPRINTS` 4 -> 32 (12 of 14 blobs are refused by it) | +1 instance, 5x the runtime |
| lower the class's `ink_threshold` 15 -> 12 -> 6 | 12 identical; 10 and below WORSE (95 TP, 5 FP) |
| drop near-black ink, keeping the light glyph away from its dark occluder | WORSE, 96 -> 88 |
| move the counting gate | only 3 of 36 sit above the review floor |
| score fused windows by BACKWARD coverage alone (partial matching) | wrong windows beat recoveries 4:1 (at 0.98: 6 vs 31) |

`fused_windows` rescued T5 because its occluder was a 1-2 px leader a window could exclude.
E4's is a solid bar that sits INSIDE any window drawn around the glyph, which is why the same
machinery does not transfer.

**The one direction with evidence behind it: sweep for the glyph, do not match it.** A Hough
vote is immune to fusion by construction, which is why doors are parametric. An untuned
`cv2.HoughCircles` reaches only 21 of 132 overall -- it works off gradients, and this glyph is
a 1 px light stroke -- but it reaches **8 of the 13 occluded ones, where template matching
reaches 0**. `doors.py` sweeps the ink MASK rather than gradients, which is why it works on
exactly this kind of faint thin line, and `find_arc` already refuses closed rings (line 257)
because a swing never is one -- the machinery is there and pointed the other way.

So the receptacle looked like it needed its specialised detector the way the door did. **It
was measured, and it does not.** Nothing was wired into `detect()`.

The ring sweep works, as a proposer. Two correlations over the ink mask -- an annulus for ink
ON the circle, a disc for ink INSIDE it -- reach **126 of the 132 annotated instances**,
including the fused ones the template path cannot see. It costs 15 s and fires 11,036 times to
do it, which is what a Hough accumulator is: it proposes, it does not decide.

Pairing it with the scorer that already has precision 1.000 is the obvious move and it fails:

```
gate 0.96   94 TP   0 FP  38 FN   P 1.000  R 0.712   occluded 1/13
template     96 TP   0 FP  36 FN   P 1.000  R 0.727   occluded 0/13
```

Handed a PERFECT centre for every instance, the scorer still refuses the fused ones -- the
window contains the occluder's ink, so `min(forward, backward)` falls under the gate. That is
the whole finding, and it is worth more than the counts: **candidate generation was never the
bottleneck on E4. The score is.** Six ways of reaching those instances have now been measured
and every one either recovers nothing or costs more precision than it buys, because they all
attack candidate generation.

**CORRECTION, and it undoes the framing above: 26 of the 36 misses are not occluded at all.**
They are the same duplex DRAWN BOLD. Measured per instance, the share of ink darker than 150:

```
mostly dark (>30%)   25 instances   25 of them missed
mixed (5-30%)         1 instance     1 missed
all light (<=5%)    106 instances   10 missed
```

Every bold instance on the sheet is missed, and the crops show why they looked like fusion: a
bold receptacle carries its circuit home-run into the same component, so it presents as a big
blob with a light glyph somewhere in it. It is the same symbol at a different lineweight, and
the template was built from a light one.

They are all duplexes -- Paing annotated them as such, and the class is right. The lineweight
is a rendering difference within one symbol, not a second symbol, so this is a pure detection
failure with the ground truth already in place to grade it.

What the lineweight MEANS is a separate question and is not answered. Screened-for-existing is
the usual convention and would make this decision 8's lifecycle axis (`takeoff/lifecycle.py`,
still an 8-line stub), but that is an inference from convention, not from this drawing -- E4's
own notes have not been read for it. Do not build a lifecycle field on the strength of it.

**Why they are missed, settled: they never become candidates.** A bold receptacle carries its
circuit home-run into the same component, so it presents as an oversized blob and the size
gate refuses it. Template quality was never the issue -- a template built from a bold
instance's own box matches **21 of the 25** bold instances scored in their own boxes, and the
same template run through `detect()` over the sheet reaches 2, because the candidates are not
there to score.

**Every component of a fix is now measured. None of them is a knob.**

| component | measured on E4 |
|---|---|
| ring vote proposes centres (two correlations over the ink mask) | reaches 23/25 bold, 102/106 light |
| a bold reference template | matches 21/25 bold in their own boxes, 7/106 light |
| the registered light template | matches 100/106 light |
| medial-axis thinning before scoring | bold median 0.894 -> 0.944; light unchanged |

Ceiling if they are combined: about 121 of 132, against today's 96. What it needs is a class
with TWO reference instances rather than one anchor -- the light glyph is 47x31 px, the bold
one 39x28, so they are different in weight AND size -- proposals from the vote rather than
from connected components, and each proposal scored at each reference's own footprint. A first
attempt scored vote proposals against the light footprint alone and reached 1 of 25, which is
the measurement that says the second reference is the load-bearing part.

Not attempted: whether the vote's proposals place a window as precisely as the annotated box
does. Every number above for the bold path is from GT boxes; the vote's boxes may be worse.

**Still true, for the other ten:** occlusion-aware scoring. The
score has to be able to say "this ink is not mine" rather than counting it against the fit --
a trimmed forward coverage (the partial-Hausdorff f-quantile the literature prescribes, done
properly rather than by dropping the direction entirely), or scoring only the pixels whose
grey matches the class's own linework, for which `Candidate.patch` is already carried. That
changes the scorer every class shares, so it needs the harness on all four annotated sheets
behind it, and it is the one change that would pay off on T4's doors and T5's markers too --
their remaining misses are the same fusion.

Until then receptacles stand at **P 1.000, R 0.727** with every miss visible in review. The switched axis is still
unmeasured: the legend draws it as vector decoration rather than text, so `label_pattern`
cannot reach it, but E3's text layer holds 19 lowercase `a`s where E4 has none. Measure that
before deciding whether switched is an attribute, a class, or out of scope.

---

## Generalisation, step 1: an unseen symbol on a sheet by another consultant

The product claim -- select any symbol on any rasterized drawing and count it -- had never
been tested. `tests/test_sources.py` proves a scan works using a PNG **crop of T5**, and
`documents/e604b67ffc6a.png` IS that crop, so until now the tool had only ever seen the
drawing it was built on.

First real test: **M2 (page 14), the mechanical plan**, drawn by a different consultant with a
vocabulary nothing in the registry has met -- diffusers, dampers, sprinklers, grilles, VAVs.
Dragged a cross-hatched square supply diffuser at (5610, 2886, 85, 85) through the server's
own endpoints, exactly as the viewer does. Nothing about it is registered.

```
select    snapped, 0.283 x 0.283 in, ink 2327, trimmed, 1 context blob
identify  'not a symbol registered yet, so it is counted without a name'
detector  template -- chosen by profile_selection from the drag, not declared
counts    10 counted, 0 review, 0 rejected; best matches 1.00 1.00 0.99 0.99 0.99
```

**It works.** And three things about it are worth more than the count:

* **Stable across selections.** A second drag, on a different instance, returned the same 10.
  That is the property doors failed early on, when recall ran 0-68% purely on which instance
  was dragged.
* **It discriminates without tuning.** On the generic 0.90 gate, the sheet's other devices
  land below it and stay out: hexagonal smoke dampers, plain square return grilles, a fan
  symbol and the letters `WO` score 0.64-0.74. The sheet's OTHER square device -- a square
  with an X and a centre circle, visually close -- is not counted at all.
* **The generic thresholds were adequate here.** counted_at 0.90 / review_floor 0.80, tuned
  for nothing in particular, put ten instances above 0.94 and everything else below 0.75.

**Two predictions I made before running it were wrong, and are recorded because they were
wrong in a useful way:**

1. M2's sheet-wide ink coverage is 2.10% against T5's 8.15%, so I expected E4's light-ink
   problem to repeat. It did not -- the diffuser is solid black and scored 1.000. Sheet-wide
   ink coverage says nothing about a particular symbol's lineweight.
2. Reading the plan at 28% I concluded there were screened and bold populations of the same
   diffuser, as on E4. There are not: thin black lines average to grey when downsampled. The
   symbols I took for screened are normal black and are a DIFFERENT device.

**DONE: a symbol is every piece the box held, not the biggest piece in it.** The template
kept the largest connected blob plus whatever the drawing had joined to it and discarded the
rest as "context" -- 64% of a supply diffuser's ink, 63% of a demolition door's -- so both
counted one instance: themselves.

Three rules for telling a symbol's own parts from a label lying beside it were measured and
**each one fails on its own**. Do not re-derive them singly:

| rule | why it fails alone |
|---|---|
| joined ink (`raw_id`, what was there) | the diffuser's quadrants and the demo door's dashes are separate paths, never joined |
| parts comparable in size | works for the diffuser (1.8x) against a marker plus label (7.5x), but the demo door spans **37x** -- a 594 px leaf against 16 px dashes |
| text-run grouping | splits the diffuser's four quadrants into two "runs" and keeps NONE of it, while not flagging the marker's label at all |

**Their CONJUNCTION works, and that is the resolution** -- see the caption filter below. Size
alone condemns the demo door's dashes; chaining alone condemns the diffuser's quadrants; a
piece has to fail *both* before it is dropped, and no real symbol part does. The drag still
decides for everything the filter does not judge, and a person corrects that: `/select` returns every piece, the viewer
outlines them, and clicking one drops it (`Selection.without`, `exclude_parts_image_px`).
Grouping reach and the part cap now come from the template's own spacing rather than global
constants that were measured on single-blob symbols.

**The cost this used to carry has since been paid off.** For one day a drag that also caught
the `C/T9` label built a glyph-plus-label template, which matches only itself -- 10 counted
became 1 -- and identification failed, costing the class its name, caption pattern and
thresholds. The manual click still exists and is still the backstop, and exclusions are still
applied BEFORE identification for that reason, but a caption no longer needs one:

**`candidates.inside_minus_foreign_text`, the caption filter.** A piece inside the drag is
dropped only if all three hold: it chains into a **line of >=2** characters; that line, *with
the symbol set aside*, is entirely **smaller than the symbol by 3x** (`RUN_SIZE_RATIO`); and
none of it **touches the symbol's ink** (`_ink_touches`, measured on ink, not bounding boxes).

Two subtleties cost a measurement each and are the reason the rule reads the way it does:

* **Runs must be tested in both orientations.** `C/T9` on T5 is stacked vertically. The
  original chaining was horizontal-only, so it never formed a run and was never dropped.
* **A glyph can chain into the line it labels, and then protect it.** The `B/T9` marker is a
  44 px triangle against 24 px letters -- a ratio of 1.83, inside any height band loose enough
  for real text -- so it joined the run, the run "included the symbol", and `FILM` plus four
  dashes went into the template: **9 counted became 0**. Setting the symbol aside before
  judging the line is what fixes it. Bounding-box protection also failed here and is why
  `_ink_touches` exists: the triangle points left, its box is mostly empty wedge, and the word
  printed in that wedge touches the box while being nowhere near the ink.

Measured, with the label fully enclosed by the drag: `C/T9` **10**, `B/T9` **9**, identical to
the tight drags. The diffuser keeps 4 parts, the demolition door 9, the split `A/T10` marker 2.
T4, T5 and E4 are unchanged.

**Still not solved: a very generous drag.** Pulling in surrounding geometry -- wall stubs,
dimension lines, leader arrows -- still degrades the count (`C/T9` at +30 px -> 3, at +60 px ->
0), because those pieces are not text and no rule judges them. Paing's idea of dropping pieces
whose raw component continues outside the box was **tested and does not work**: the marker's
own component leaves the box through the wall network it sits on, so at any threshold that
catches an arrow it also catches the symbol (measured: dropping at >=0.05 outside-share takes
`C/T9` from 1 to 1104 counted). The manual click covers this case today.

Also fixed on the way: `profile_selection` read the diffuser as a CURVE, because each corner
bracket fits a clean 75-degree arc, so the tool swept for door swings and boxed fragments --
and a return/exhaust grille contains a fittable arc too, which is why one was counted as a
diffuser. `ARC_SPANS_SELECTION` now requires the arc to span what was selected: measured over
28 annotated doors it runs 0.71-1.09, the diffuser scores 0.45, gate at 0.60.

T4, T5 and E4 were unchanged throughout this work -- 24/1/3, 31/0/1, 10/0/2, 96/0/36.
(The marker and receptacle figures moved later, when fused instances were held for
review; see the occlusion section below.)

**Paing tried it on the real diffuser and it failed in two ways, both now diagnosed.** The
symbol I tested above was NOT a diffuser -- M1's legend defines `SUPPLY DIFFUSER/REGISTER` as
a square with an X and a centre circle, `RETURN/EXHAUST GRILLE` as a square with a SINGLE
diagonal, and `LINEAR SLOT DIFFUSER` as a bar. The dense cross-hatched square I dragged is
some other device and I mislabelled it.

1. **The boxes landed on quadrants, not the whole square.** Dragging the real diffuser makes
   `profile_selection` read the selection as a CURVE: the four corner brackets each fit a
   clean 75-degree arc, so the tool swept for door swings of radius 0.15 in and boxed the
   fragments. A return/exhaust grille contains a fittable arc too, which is why one was
   counted as a diffuser -- Paing spotted that from the legend before I did.

   **FIXED: `detect.ARC_SPANS_SELECTION`.** An arc is the symbol only if it spans what was
   selected. Measured over 28 annotated doors on T4 and T5, each dragged generously: radius
   over selection span runs **0.71 to 1.09**, median ~1.0. The diffuser scores **0.45**. Gate
   at 0.60, nearer the diffuser than the worst door because losing a door is worse than
   misreading an air device. Ink share was tried first and is the wrong test -- the door's arc
   is 0.21 of its selection's ink against the diffuser's 0.36, the wrong way round, because a
   swing is a thin curve beside a fat keynote bubble. T4, T5 and E4 all score identically
   after the change; 197 tests pass.

2. **RESOLVED: the template used to be one quadrant.** With the detector now right, the diffuser
   goes down the template path and `Template.from_selection` keeps the largest piece plus
   pieces sharing its `raw_id` -- the pieces the drawing had joined before suppression. The
   diffuser's four quadrants are separate paths that were never joined, so **three are
   discarded as context: 499 of 774 ink px, 64% of the symbol**, and it counts 1.

   The trim is load-bearing and cannot simply be removed: it is what stops a marker dragged
   together with its `C/T9` sheet reference from becoming a template of glyph-plus-label.
   Measured, the two cases separate cleanly by how comparable the pieces are:

   ```
   marker + label   6 pieces  1340, 179, 165, 144, 92, 65   primary 7.5x the next
   diffuser         4 pieces  275, 183, 159, 157            primary 1.8x the smallest
   ```

   A symbol's parts are comparable; an annotation's characters are an order of magnitude
   smaller. The 3x bound suggested here is now in the code as `RUN_SIZE_RATIO` -- but only as
   the second half of the caption filter, applied to pieces that already chain as a line of
   text. On its own it drops the demolition door's dashes, which are 37x smaller than the leaf
   and are the entire symbol.

**Still unknown: recall.** Ten counted, but nobody has said how many cross-hatched supply
diffusers M2 actually holds. That needs an annotation pass, and it is the only thing standing
between this and a graded number.

**P2 (page 18) is a poor second test and should not be forced.** Its countable pool is
dominated by text -- `EX`, `FLR`, `WC` -- because plumbing fixtures are drawn as detailed
outlines rather than compact glyphs. Counting a water closet is a different detection problem
from counting a symbol, and pretending otherwise would measure the wrong thing.

---

## Occlusion: symbols fused to the geometry they are drawn on

The single largest deficit, diagnosed rather than guessed. E4's duplex recall was 0.727 while
everything else ran 0.96+, and occluded recall was 1 of 19 across everything annotated. All 36
E4 misses were bucketed:

| cause | count |
|---|---|
| the glyph is joined to bigger geometry, so it never becomes a candidate | 25 |
| a candidate is right there and the scorer rejects it | 11 |

**Line suppression was the suspect and is NOT the cause.** 31 of the 36 misses keep >70% of
their ink through it -- the same as all 96 counted instances. A first pass said otherwise by
testing whether the truth box's CENTRE pixel survived; a duplex receptacle is a circle with
two bars and its box centre is inside the hollow, so that measured nothing. The share of ink
surviving is the right question and it exonerates suppression completely.

The glyph is simply drawn touching a wall or casework, so its connected component is the wall:
host blobs run 108-421 px on the larger side against a 46 px glyph, median 208.

**Three gates were in the way, and only the first was the one I expected.**

1. `MAX_FUSED_FOOTPRINTS = 4.0` capped the fused search at 4x the template's box area. The 25
   host blobs run 5x-49x, so **the search written for this problem excluded every real case**.
   It had recovered the one occluded T5 marker, whose host happened to be small, and that lone
   success hid the gate. Now `MAX_FUSED_INK`, measured in multiples of the template's INK: a
   200x200 patch of wall is mostly white and cheap to search, a 200x200 hatch fill is neither,
   and box area cannot tell them apart.

2. **`find_candidates` was the real gate.** Raising the cap alone recovered 4 of 25. The size
   band stops at 0.67 in = 201 px, so the host blobs were never in the pool `fused_blobs`
   filtered. `candidates.host_blobs` returns the components ABOVE the band; `detect` takes them
   as a separate `hosts` argument so they can never reach grouping or the size gate -- a wall
   network is not an instance and is not part of one.

3. **`fused_windows` returned one window per blob.** 6 of the 19 host blobs hide two
   receptacles each. It now returns every non-overlapping window above the floor, and the
   assignment loop claims the WINDOW rather than the host blob, which it previously did by
   candidate id -- blocking the second instance twice over.

**Recoveries land in review, never in the count** (`banding.band(..., ceiling=REVIEW)`, decided
with Paing). A fused window's score is the best of hundreds of positions chosen by the search,
not one reading of one component, so it does not mean what a class's gate was tuned to mean.
Precision is the property worth protecting: 1.000 on two of three graded sheets.

**The cap, swept on E4.** Precision holds at 1.000 throughout and saturates at 48x:

```
 cap  blobs   TP  FP  FN   prec  recall  +review  review   secs
  4x    303   95   0  37  1.000   0.720    0.773      61   36.5
 16x    450   95   0  37  1.000   0.720    0.818      78   71.9
 32x    478   95   0  37  1.000   0.720    0.848      98  113.0
 48x    484   95   0  37  1.000   0.720    0.856     108  139.1   <- chosen
 64x    485   95   0  37  1.000   0.720    0.856     108  144.3
```

(The sweep predates the sub-floor fallback below, which lifted 48x to 0.864 / 110 review.)

The cost is real and worth stating: a sheet takes ~2.3 min against ~36 s, a 3.8x slowdown, and
review volume rises from 67 to 110 against 95 counted.

**The metric had to be built first, and this is the part that would have sunk the work.** The
harness graded the counted band only and reported review as raw volume, so an instance
deliberately routed to review stayed a false negative AND raised review volume -- every number
moved the wrong way and real progress read as a regression. `eval/harness.py` now matches the
review band a second time against the instances the counted band missed, splitting it into
`recovered` and `review_spurious`, and reports `recall_with_review` beside `recall`. Neither
`precision` nor `recall` changed definition.

**Result.** Counted recall is deliberately slightly LOWER -- the two instances the fused path
used to count outright are now held for confirmation:

```
                 before                     after
E4  duplex   96/0/36  R 0.727  +rev 0.773   95/0/37  R 0.720  +rev 0.864   review 67 -> 110
T5  markers  10/0/2   R 0.833  +rev 1.000    9/0/3   R 0.750  +rev 1.000   review  8 ->  11
T5  doors    31/0/1   R 0.969                31/0/1  R 0.969               unchanged
T4  doors    24/1/3   P 0.960  R 0.889       24/1/3  P 0.960  R 0.889      unchanged
```

E4's occluded recall-with-review went 0.077 to 0.385 (1 of 13 to 5 of 13). Precision is 1.000
on E4 and T5 and 0.960 on T4, exactly as before -- which was the point of the review ceiling.
208 tests pass.

**One thing nearly went dark in silence, and it is the reason to keep a test on a mechanism
rather than only on a number.** The margin gate -- the second confidence, "how much better was
this than the next class" -- was only ever exercised because a fused blob reported its best
window even when that window scored below the class floor: a sub-floor reading is worthless as
a detection and is exactly what tells a door that a marker also looked at its ink. Returning
only windows worth counting took T5 from two rivals to **zero**, with every other number on the
sheet unchanged. `fused_windows` now falls back to its best window when nothing clears the
floor, and the test that records this says why.

Rivalry and claiming also had to be separated: a fused window SITS ON the host blob (that is
what it competes for) but CLAIMS only its own box (so a second instance in the same host is
still reachable). Using one set of ids for both is what broke the margin.

**What is still missing on E4, measured.** Of 20 instances now genuinely not found: 13 have a
window that scores 0.757-0.877 against a 0.85 `review_floor` (11 would clear 0.80), 4 sit in
blobs above even the 48x ink cap, 2 have a candidate right there that scores out, 1 has no
window land on it. The 13 are a threshold question, not a search question, and the earlier
finding argues against moving that gate: E4's real and false review scores OVERLAP
(0.879-0.928 against 0.851-0.932), so no floor separates them. That is calibration work and
was deliberately left alone.

---

## The drag is a ceiling, and a dropped piece stays on screen

Two things Paing hit while selecting diffusers on M2.

**1. Snap returned more than the box contained.** A component is kept when most of its ink is
inside the drag, and it was then kept WHOLE -- so a diffuser with a duct line curling off one
corner came back bigger than the box drawn round it, and no amount of care with the mouse could
exclude the curl. Measured: a tight **82x106 drag returned a 120x123 selection**.

`candidates.clipped_to` now cuts every piece at the boundary and `snap` re-tightens to the ink
that survives. The rule is simply that the symbol is never larger than the box.

The alternative -- refusing any component that sticks out -- loses the symbol instead of the
curl, which is the wrong half. Occlusion still means foreign ink can lie INSIDE the box; that
is what the part list is for. This rule handles the edge, not the interior.

**It caught a bug in the registry on the way in, which is the part worth remembering.** Two of
the three stored anchors are drags that touch their own symbol, so making the box a ceiling
made them clip the reference they exist to define:

* The door's arc reaches 9 px left of its box. Harmless, and verified rather than assumed --
  the fitted radius is **111.0 px clipped or not** and the band identical at 0.2405-0.4995 in.
  Its anchor was left alone: widening it would change a reference for no measured reason.
* The duplex receptacle's glyph runs to x=2516 and its box stopped at 2513, so the template
  silently became **44x31 / 176 ink px instead of 47x31 / 181**. That is a degraded reference,
  and it showed up as an apparent IMPROVEMENT -- E4's recall-with-review read 0.879 against
  0.864 -- which is exactly how a bug like this gets kept. The anchor is now 66 px wide.

With that fixed, every graded number is **identical to before the change**: T4 24/1/3 P 0.960,
T5 31/0/1 and 9/0/3 +review 1.000, E4 95/0/37 P 1.000 +review 0.864. M2's diffuser template is
still 76x100 with 4 parts and still counts 5. The fix does what was asked and moves nothing.

An anchor exists to define a symbol, so it must contain it. Worth a check if a fourth class is
ever registered.

**2. Parts stopped appearing for elevation markers**, because the caption filter had started
doing its job. A generous drag round a marker used to return glyph-plus-label as six pieces to
choose between; once a line of characters that does not include the symbol was dropped
automatically, the drag returned **one** piece and there was nothing left to click. The default
became the only reachable answer.

`Selection.set_aside` now carries what a rule removed. Those pieces are not in the mask and not
in the template, but they travel to the viewer, are drawn switched off, and a click puts them
back (`Selection.plus`, the inverse of `without`; a piece dropped by hand joins the same pile,
so that click can be taken back too). `/select` reports every piece with an `active` flag, and
`/count` takes `include_parts_image_px` beside the existing excludes.

Only DEVIATIONS from the server's own decision travel. It drops captions by default, so
re-including one is an explicit include and dropping a kept piece is an explicit exclude --
otherwise any caller that sends nothing could not get the default behaviour.

```
C/T9, label inside the drag   1 member + 5 set aside   -> re-included: 6 members
B/T9, label inside the drag   1 member + 19 set aside  -> re-included: 20 members
```

---

## Evaluate, and a truth overlay that follows what is being counted

**The ground-truth overlay is filtered by the class under test.** With nothing counted it
still shows everything recorded on the page -- that is what annotating needs, and it is the
only way to see a sheet has been covered at all. Once a class has been counted, every box that
is not that class reads as something the tool missed, so `visibleTruth()` narrows the overlay,
the click target and the legend to the class being counted. The legend narrows too: listing
the other classes would offer "none on this sheet" beside a class that has annotations and is
merely hidden, which is a tickbox asserting a falsehood.

**The grading panel is now Evaluation, and the button runs.** It used to say "not graded yet,
run `eval.suites`" -- a button that reports the absence of a thing it will not do.
`POST /api/pages/N/evaluate` scores the detections the viewer is ALREADY HOLDING against the
page's annotations. The detection pass has happened; repeating it to produce a number would
cost minutes and make the viewer a second place where a run happens.

It reuses `harness.score_class` and `report.payload` outright, and shares the report-to-boxes
conversion with the stored-run endpoint (`_grade_view`), so the live and stored paths cannot
drift. Verified against the harness on T5: **9 of 12 found, recall 0.750, precision 1.000,
+review 1.000, 11 in review** -- identical to `-m eval.suites --page 5`.

Only the classes actually counted are scored. T5 has doors recorded as well as markers, and
grading doors off a run that never looked for them would report all 32 as misses; they are
named in `not_graded` instead.

The panel now leads with the sentence a person actually wants -- `9 of 12 recorded instances
found` -- and puts recall, precision, F1, the review split and the occluded-only recall
underneath. A live evaluation is cleared the moment a new count arrives, because it describes
the count it was run on; a stored sheet run is left alone, because it describes the sheet.

**The list contradicted itself and now does not.** `Score.missed` is every instance the COUNTED
band failed to claim -- the right denominator for `recall`, and the wrong thing to put in front
of a person, because an instance found and held for confirmation is in there too. So the panel
listed the same three T5 markers twice: once as missed and once as found. `Score.not_found` is
the subset with nothing pointing at it, and that is what the report's `missed` array and the
overlay now carry. `false_negatives` and `recall` are untouched.

The three states a recorded instance can be in now partition it, and a test asserts the sum:

```
elev_marker  12 recorded = 9 counted + 3 found, in review + 0 not found
door_swing   32 recorded = 31 counted + 0 found, in review + 1 not found
```

**False positives are drawn but listed only on request.** They answer a different question from
the rest of the list -- a claim about a piece of ink, not an outcome for a recorded instance --
and precision is 1.000 on two of the three graded sheets, so the row is usually absent anyway.
The button says how many it is hiding and does not appear when there are none: a control that
reveals nothing invites a click that seems to do nothing and leaves a person unsure whether the
list is filtered or simply empty.

---

## Panel changes, and one that was hiding a broken counter

Paing's list, with two worth recording as findings rather than as edits.

**`Rejected` was a permanent zero and is gone from the tally.** It is the detector's third
band -- ink below the class's review floor -- and `/count` does not return those unless
`keep_rejected` is asked for, so the row could only ever read 0. Worse, it sat beside a review
bar whose `R` key also says "reject", so pressing R and watching the number stay at 0 read as a
broken counter. They are different things: the band is what the DETECTOR discarded, the verdict
is what a PERSON discarded. The band still exists in `takeoff.banding` and `diagnose` still
reports it when explaining why a symbol is absent; it just has no place in a list of results.
`Counted` is now `Detected`, and accepted/rejected are counted separately under Evaluation.

**A verdict now replaces the band colour instead of decorating it.** The old rule was that a
reviewer's mark is drawn ON TOP of the detector's colour, never in place of it, so both facts
stay legible. That was the wrong trade in practice: once somebody has judged a box, whether it
scored 0.93 or 1.00 stops mattering, and the thing worth seeing across a sheet is what is still
unjudged. Green accepted, red rejected, band colour until then.

**Evaluate waits for a complete review.** Accepting a detection writes it into ground truth and
rejecting takes it out, so evaluating half way through scores the detector against a person who
has not finished disagreeing with it -- the number would move on every keypress with nothing
about the detector having changed. The button says how many are left.

Also: the candidates hint is gone; Evaluation sits above Ground truth; `Show on sheet` is gone
(an evaluation is shown because you asked for it, and Reset is how you are rid of it); the
false-positive button no longer carries a count; the ground-truth instructions collapsed from
four paragraphs into a `?` toggle; and the selection preview is bounded (`max-height` plus
`object-fit: contain`) after a tall glyph rendered past its box and over the stats below it.

---

## The preview disagreed with the answer

Paing dragged a box around a duplex receptacle on E4 and got back the circle without its two
bars -- a 31x31 selection where the glyph is 47x31 -- despite having boxed the whole thing.

**Two separate causes, and only one was what it looked like.**

1. **Five fragments were below the candidate size floor.** At the default ink cut this glyph
   is nine pieces and five are under 0.027 in (8 px), so `find_candidates` never returned them:
   not dropped from the selection, unreachable -- not set aside, not clickable. That floor is
   about the SHEET, which holds millions of specks; inside a box a person drew there is no such
   problem. `candidates.fine_candidates` labels a window around the drag with no floor and
   returns only the sub-band pieces.

   **They arrive SET ASIDE, never as members.** Making them members first was measured and
   reverted: sub-band ink is mostly the frayed edge of a stroke, and folding it into the
   template changed a registered class -- the T5 elevation marker went from 9 counted to 8.
   Offered and switched off costs nothing and is what Paing asked for as the fallback.

2. **The bars were not sub-band, they were sub-THRESHOLD.** At the default cut there is no ink
   there at all; they exist only at the receptacle's own `ink_threshold` of 15. So no amount of
   finer labelling could reach them, and the fix is that `/select` now runs the same
   segmentation probe identification does and returns the reading that recognised something.

   The count was always right -- `/count` re-snaps on the class's own ink -- so what this fixes
   is the preview disagreeing with the answer. It costs one candidate pass per segmentation on
   first use and nothing after; `/count` was already paying it.

```
                     before                      after
E4 duplex receptacle  31x31, ink  57, 4 pieces    47x31, ink 181, read as receptacle_duplex
T5 elevation marker   44x129, ink 1340            unchanged, 1 speck offered
T5 door               108x112, ink 1748           unchanged
```

**Also, from the same round of feedback.** Evaluate is pressable again when the review is
unfinished: a disabled button gives no reason, so a person presses it, nothing happens, and the
tool has told them nothing -- it now says how many are left and why it matters. The
false-positive control no longer hides itself when the count is zero, which made it vanish on
exactly the sheets where precision is perfect and read as a bug rather than as an answer; the
count moved to its title. And false positives are now off by default on the SHEET as well as in
the list, since showing them beside the misses makes a sheet look worse than it scored.

---

## A person can name a symbol now

The built-in vocabulary is four classes, which is a fair bet against no real drawing set: a
mechanical sheet has diffusers and dampers, a plumbing sheet has fixtures. An unknown symbol
was already COUNTABLE -- it came back `unnamed` on the generic gates -- but ground truth and
grading both key on a class id, so there was no way to record what a count found or to measure
it. Naming is what turns a count into something that can be checked.

**A new class is a name for a SELECTION, not a bare label.** The drag becomes the class's
anchor, and that one decision is why it needs no special case anywhere: every consumer of the
registry reads `symbol.anchor` -- `_class_library`, `eval.suites._entry_for`, `detect.identify`
-- so an anchored class is identified on other sheets, built into a template bank by the
harness and offered when annotating, exactly like a built-in. An anchorless class would have
needed a guard in each of those and still could not have been counted.

Measured on M2: naming one supply diffuser and then dragging a DIFFERENT instance comes back
`supply_diffuser` at 0.92, 5 counted. That is the test -- being recognised somewhere other than
the box it was cut from is the whole difference between a class and a label.

Thresholds are the generic 0.90/0.80, and that is the honest position rather than a gap:
naming a symbol changes what it is CALLED and what it can be graded against, not how it scores.
They are exactly the numbers an unregistered symbol was already counted on.

Stored in `classes.json` (gitignored -- it is a person's vocabulary, not the tool's) and loaded
at import. `classes.BUILT_IN` is frozen before that load, so a class somebody added can always
be told from one that ships: they grade identically and only one of them can be removed. A
built-in always wins an id collision; the file is not authoritative.

**Also: accepting a match records it as ground truth, and the panel now says so.** It always
did -- `truthFromVerdict` adds on accept and removes on reject -- and it is not a detail. It is
why Evaluate waits for the review to be finished, and somebody pressing A a hundred times
should know they are building the annotations rather than tidying a list.

**Removing one, behind its own button.** The vocabulary was write-only: a class added by
mistake, or misnamed, stayed forever. `Classes` toggles a roster of everything the tool can
annotate and grade, with `Remove` on the rows that can be removed and `built in` on the rows
that cannot. `DELETE /api/classes/{id}` refuses a built-in with 403 -- its anchor lives in
`takeoff/classes.py` rather than in data, so a button offering to delete one would be a code
change pretending to be a button.

**Annotations recorded against a removed class are LEFT ALONE**, and the confirmation says how
many there are, because that is the fact that decides it. They are somebody's work; deleting
them quietly would be the worst possible reading of "remove the class". They keep their id, and
re-adding the same name picks them straight back up.

The roster is a separate question from the legend and gets separate furniture. The legend
answers "what is on this sheet"; the roster answers "what can I annotate". An earlier attempt
put tickboxes and a delete on the legend rows, which made one list answer both questions and
neither well -- a tickbox that hides a class sat beside a tickbox that ASSERTS a class has no
instances, two opposite meanings in the same column.

---

## The detail marker, promoted from somebody's file into the vocabulary

Paing added it from the viewer, where it landed in `classes.json` on one machine, uncommitted.
It is now a built-in, which is a claim about the drawing set rather than about one person's
session -- and the numbers are what justify it.

A circle split by a horizontal bar, detail number above and sheet number below, fused to a
hatched arrowhead on a leader. **Three features at once**, and that is why it separates better
than anything else here:

```
sheet   real markers            best thing that is not one
T3      1 at 0.913              0.574   (the same marker's leader, cut off below it)
T9      5 at 0.977-1.000        0.338
T10     9 at 0.976-1.000        0.407
T4-T8   none                    0.561
```

15 genuine instances, a **0.339 gap** to the best false positive. Thresholds set from that
gap -- `counted_at=0.85`, `review_floor=0.70` -- not left on the 0.90/0.80 a class gets when it
is named in the viewer, where the lowest real instance would have had 0.013 of headroom.

**The caption is INSIDE the glyph, which no other class has to deal with.** The reference is
two words stacked across the bar -- `4` over `T12` -- so the template cannot be separated from
one instance's number: the caption filter takes `T12` out as a run of characters and leaves the
lone `4` in, because a single character beside a symbol is not a caption by any measure the
filter has. That is tolerable and measured rather than assumed: the digit is 210 px of ink
against 3,626 for the glyph, and instances reading `7 / T11` and `1 / T12` still score 0.977 and
better against a template that reads `4 / T12`.

**Reported as `label_pattern=r"^[A-Z]{1,2}\d+(\.\d+)?$"`, which is the sheet half.** `label_for`
returns one word and the nearest word is always the detail number, which alone means nothing.
Composing a caption out of two words is a real gap in the core -- **reported at the gate, not
worked around here**, because one class wanting it is not yet evidence the core is under-general.

**Cross-talk with the elevation marker was the thing to check**, because both carry the same
hatched wedge. Neither scores above 0.35 against the other's template, and running the two
together on T5 -- which has elevation markers and no detail ones -- counts zero detail markers
and leaves the elevation count at 9. `-m eval.suites` on all three graded sheets is unchanged:
T4 24/1/4, T5 doors 31/0/1, T5 markers 9/0/3 +review 1.000, E4 95/0/37 +review 0.864.

Also worth saying: `T9` is the sheet the marker is drawn ON; `T12` inside the circle is the
sheet it POINTS AT. I wrote the first version of these notes with the two swapped.

---

## Ranking arcs by quality: falsified twice, and now measured properly

Paing saw doors going undetected on the URI sheets and thought the arcs were losing pixels to
low resolution. **It is not resolution**, and that is worth stating first because it is cheap
to test and easy to believe: rendering the same sheet at 600 DPI gives an identical answer.

```
                300 DPI    600 DPI
radius          144 px     282 px      -- the same 0.48 in
occupancy       0.769      0.769
stroke_ratio    1.708      1.685
quality         0.191      0.200
```

The pixels are found. The wrong circle is chosen, and then thrown away.

### The diagnosis, which stands

`find_arc` ranks hypotheses by **inlier count**; `is_swing` and the banding gate judge by
**`Arc.quality`**. Ranking on one and gating on the other means the arc that would pass is
discarded before the gate runs. Every viable radius on URI's room-113 door:

```
radius  inliers  quality
    87      406    0.082   <- returned: most inliers
   105      373    0.239
   153      196    0.350   <- best quality, never considered
```

Note that **nothing here reaches the 0.5 gate**, so this door was unreachable either way. The
disagreement is real; it is not sufficient on its own.

### The fix, and why it is wrong

Ranking by quality among hypotheses that already clear `MIN_INLIERS` looked decisive in
isolation -- over 40 components a side, T5 went 25 -> 39 clearing the gate and URI 10 -> 32,
and sheet-wide `swings_in` on URI went from 25 of 80 arcs passing to 76 of 76.

**It fails the moment it is run through the pipeline instead of measured beside it.** Eight
tests in `tests/test_doors.py` break, and they break in the two ways that matter:

* **Precision.** T4 counts 39 doors where 29 exist -- the chairs come back.
* **Stability.** On T5, `test_selecting_any_door_gives_the_same_count` goes from one answer to
  `{1, 39}`. Quality is scale-free, so the sweep starts returning a small clean curve *inside*
  the door blob; the radius measured from the selection is then wrong, and the sheet-wide
  sweep hunts for the wrong size.

A strictly smaller variant -- keep the centre ranked by inliers, choose only among the four
radii at that centre by quality -- is **worse**, breaking even
`test_a_quarter_circle_is_found_with_its_radius_and_centre`.

**Both reverted.** This is the second time; the earlier note said *"quality is scale-free, so
any small clean arc scores 1.000. Do not revisit that one."* That note was right and the
`MIN_INLIERS >= 55` floor is not the safeguard I took it for -- a 55-pixel curve is still tiny
next to a door. This entry supersedes it with the specific failures, so a third attempt costs
nothing to rule out.

### What the fix actually needs

Not a ranking tweak. The downstream gates all assume `find_arc` returns the *dominant* circle
by evidence, so changing what it returns breaks `is_swing`, the radius measured from a
selection, and `ARC_SPANS_SELECTION` at once. Either `find_arc` returns **several** ranked
hypotheses and lets the caller choose -- which is the honest interface, since only the caller
knows whether it is profiling a selection or sweeping a sheet -- or the gates stop judging an
arc by the blob it landed in, which is the T4 work already scoped above. The two are the same
piece of work approached from opposite ends, and it is a session on its own.

### Delivered instead: an under-resolved sheet now says so

`documents/test.png` is a 993x349 screenshot of a whole 36-inch sheet: **27.6 DPI of paper**,
where a door arc is 7.8 px and the detector needs 72-156. `MIN_INLIERS` alone forbids it. No
change makes that file work -- the information is not in it -- but returning a silent zero
made it look like a detector failure. `detect.too_coarse` names it:

> This image is 993x349 px, which at 300 DPI is only 3.3x1.2 in of paper -- smaller than any
> drawing sheet, so it is a full sheet captured at too low a resolution. Across a 36 in sheet
> it works out at about 28 DPI, where this symbol's 0.24-0.52 in radius is roughly 7 px and
> the detector needs 72-156. Re-export the sheet at a higher resolution, or open the PDF.

Keyed on physical size alone, so `detect.py` stays raster-only and a real sheet -- 36x24 in at
its own DPI -- can never trip it.

---

## Generalisation, step 2: a drawing set by another firm entirely

Step 1 was M2 -- a different consultant, the same PDF. This is the first drawing the tool has
seen that was drawn by a different **firm**, for a different owner, at a different scale.

`documents/uri_2511plans.pdf`, 6.3 MB, 33 sheets: **University of Rhode Island resident hall
door and lock replacement**, Tecton Architects, 2014, from Rhode Island's public purchasing
portal (`purchasing.ri.gov/rivip/externalbids/quasipublicagencies/uribids/2511plans.pdf`). A
public bid document. Sheet BA-A2.101 -- Barlow Hall, 1st and 2nd floor plans -- is the one
measured below. Three things differ from Skanksa.pdf and each could have broken something:

```
                Skanksa.pdf          URI 2511
page rotation   90 / 270             0
plan scale      1/8" = 1'-0"         3/32" = 1'-0"    <- doors are 0.75x on paper
draughtsman     (the bundled set)    Tecton Architects
```

**The infrastructure generalised.** Rasterisation, `spaces.py`, region segmentation, line
suppression, candidate generation, snapping, the viewer and the count endpoint all ran with no
edit. Rotation 0 did not misplace a single coordinate -- `origin_sheet_pt` is `(0, 0)` here and
the round trip is identity, which is the case the property test never exercised on Skanksa.
Suppression removes 71.6% of ink against 81% on E4 and leaves 5,378 candidates.

**The door detector generalised. Door IDENTIFICATION did not, and that is the whole finding.**

A drag around a Barlow Hall door comes back `not a symbol registered yet`. On every one of the
four segmentations, on all four registered classes. So `identify` never offers `door_swing`,
the arc detector never runs, and the generic template path counts instead:

```
what a person gets today      27 detections, template path, scores 0.813-1.000
doors.swings_in on the same sheet, run directly:
  registered band 0.24-0.52in    66 swings   radius median 87 px
  scaled band     0.18-0.39in    75 swings   radius median 84 px
  wide open       0.15-0.60in    80 swings   radius median 84 px
the sheet's own door tags                    ~90 across two viewports
```

**27 against 75.** The arc detector is fine -- it finds the doors at a median radius of 84 px,
which is exactly what 3/32" predicts from Skanksa's 107-121 px (0.75 x 107 = 80). Everything
lost is lost at the identification step, before the right detector is ever chosen.

### Why identification refuses it, measured

`profile_selection` reads the selection and picks a detector. For the door in room 113 it finds
the arc and then throws it away on three gates at once:

```
arc found            radius 81.0 px = 0.270 in        <- correct, this IS the door
quality              0.000  vs MIN_ARC_QUALITY 0.5    <- rejected
is_swing             False                            <- rejected
ARC_SPANS_SELECTION  needs r >= 99.0, arc is 81.0     <- rejected
```

The third one is a mistake this file already documents in another place. `is_swing` requires
`radius >= 0.65 * reach` where reach is the **blob's** bounding box, and `ARC_SPANS_SELECTION`
requires `radius >= 0.60 * span` where span is the **selection's**. Both judge an arc by the
box it landed in, which is exactly what `Arc.quality` was rewritten to stop doing. On Skanksa
a door's component is roughly the arc alone, so radius / reach is about 1.0 and nobody noticed.
Tecton draws the leaf flat against the wall, so the jamb shares the arc's component: reach 146
px against a radius of 81, a ratio of **0.55**, under both thresholds. On T4 this cost three
doors out of 55 and was filed as "worth doing only if doors on other sheets fail the same way".

**They do. That question is now answered, and it is the same bug.**

The scale difference compounds it rather than causing it: a smaller radius inside the same
fused component pushes the ratio further under the gate. And `quality 0.000` is a second,
independent refusal -- the sweep at `min_quality=0.5` returns the true 81 px arc but scores it
zero, while at `min_quality=0.0` it returns a spurious 144 px circle at quality 0.184. The
hypothesis ranking is picking wrong on this drawing too.

### What this says about the tool, fairly

The claim being tested is "select any symbol on any rasterized drawing and count it". On an
unseen firm's drawing the honest scorecard is:

* **Everything that reads the drawing generalised.** No coordinate bug, no crash, no tuning.
* **Template-matched symbols generalise.** A selection is counted on the generic gates, which
  is what the M2 diffuser test predicted.
* **The one class with a bespoke detector did not**, because recognising WHICH class you
  selected is a separate problem from counting it, and it is the weaker of the two. The
  detector was measured on Skanksa; the identifier's thresholds were measured on Skanksa's
  drawing conventions, and conventions are the thing that varies between firms.

Not fixed here, deliberately: this session was asked to test, and the fix is the real piece of
work the T4 entry already scoped -- report an arc's own extent as its box rather than the
component's, and separate a door fused to a jamb from a circle fused to a line. It is now
worth doing, which it demonstrably was not before.

**Ungraded.** Nothing on this sheet is annotated, so "~90 doors" is counted from the drawing's
own door tags in the text layer (179 tag-shaped words; 86 numbers appear exactly twice, once as
a room label and once as a door tag, and 7 appear once). That is a cross-check, not ground
truth, and the 27 and the 75 are both detector output. Annotate a URI sheet before quoting a
recall number from this.

---

## Evaluate now scores the review, not the bands

Paing pressed Evaluate with nothing reviewed and no reminder appeared. It was appearing --
`note()` writes to the panel note at the TOP of the Selection section, which on a page with
results is about a thousand pixels above the Evaluate button. The tool had the right answer
and put it where nobody was looking. It now sits 6 px under the button, in vermillion.

**The bigger change is what the numbers mean.** `score_class` grades a detector run: the
counted band is the claim, the review band is a question, nobody has looked. That is right for
`-m eval.suites` and wrong for a button somebody presses after going through every match. Once
a person has confirmed an instance the tool held back, calling it a half-find describes the
tool's uncertainty rather than the answer.

So `harness.score_review` grades the other thing, and the two live side by side:

```
                        score_class (eval.suites)      score_review (Evaluate)
what is a detection     the counted band               what the reviewer accepted
what is a false pos.    counted, on no annotation      what the reviewer rejected
the review band         carried as volume, unresolved  resolved -- it has a verdict
```

On T5, accepting all twelve markers reads **12 of 12**, where the suite reads 9 of 12 with
three held for review. Neither is wrong; they answer different questions, and the panel says
which it is showing ("Evaluated … the count on screen" vs "Graded … a full sheet run").

**Six rows, and `average precision` is the one that earns its place.** Precision is a single
operating point. AP walks the precision-recall curve in the detector's own score order, so it
sees something no count of outcomes can:

```
same run, twelve found, one wrong        precision   recall    AP
the wrong one scored 0.50 (below all)      0.923      1.000   1.000
the wrong one scored 1.00 (above all)      0.923      1.000   0.583
```

The second is the run a score threshold would get wrong, and only AP says so. Measured live on
T5: 12/12 detected, 8 false positives, recall 100%, **AP 98.8%** -- one rejection outranked one
accepted match, which is exactly the defect the number is for. It is computed against the
SHEET, not against what was claimed: three of forty found and all three right is AP 0.075.

**Occlusion is reported as found-of-recorded with no false-positive count**, because a false
positive sits on no instance and there is no instance to say whether it was occluded. Same
argument as `restricted_to_occluded`, which has always dropped them.

**The evaluation now scores the annotations the VIEWER holds, not the file on disk.** Accepting
a match records the instance immediately and Save truth is a separate gesture, so scoring
against the file counted everything somebody had just confirmed as a miss -- the button
punished you for using it and then not saving.

**Show false positives is gone.** It filtered a list; the number it revealed now has its own
row, so it was a control for something already on screen.

### Worth knowing: rejecting deletes a recorded annotation

Not a change, and not a bug I introduced -- `truthFromVerdict` has always removed the linked
instance on reject, and the panel says so. But it matters more now that `detected X / Y` puts
Y on screen: **Y moves while you review**. Reject a match that was adopted onto a saved
annotation and that annotation goes, so the denominator shrinks. On a synthetic review of T5
that cost one of the two occluded markers before I noticed what I was looking at.

That is defensible for an instance the accept created and wrong for one somebody carefully
recorded earlier, and the two are indistinguishable once adopted. Flagged rather than changed:
it is the accept/reject contract, not the evaluation, and redesigning it quietly under a
request about the panel would be the wrong call.

---

## Reading a sheet before somebody asks a question about it

**The first drag on a sheet cost ~23 s. Every drag after it cost ~0.3 s.** Both numbers are the
same cache, and none of the work depends on where the box was drawn:

```
_class_library      12.07s   one reference template per class, each from its own anchor page
_candidates_for      3.70s   the default segmentation of this sheet
  gap=0  cut=25      3.42s   what door_swing needs
  gap=10 cut=15      3.94s   what receptacle_duplex needs
                    ------
                    23.13s   paid by whoever dragged first, after the gesture was finished
```

So it is started when the sheet is on screen instead. `POST /api/pages/N/warm` returns in 6 ms
and builds all of it off-thread; the viewer fires it from OpenSeadragon's `open` handler and
polls the same shape back to drive one busy line in the panel.

**The part that actually needed care is the drag that lands while the warm is still running**,
which is the normal case on a fresh sheet, not an edge one. The cache locks were held only
across a dict access, so two threads would cheerfully do the same 4 s pass at once -- on a
machine that had just been asked to be quick. `_builder_lock(key)` gives each cache entry its
own lock and re-checks the cache inside it, so the second caller joins the pass in flight:

```
POST /warm returns              0.006s
drag 1.0s after open            8.94s   (joins the library build; was 23s)
drag after the warm finishes    0.19s
```

**Parallelising the warm is not worth it, and I measured rather than assumed.** The four passes
are independent and numpy/OpenCV release the GIL, so a thread pool looked free:

```
serial     15.99s over 4 jobs
parallel   14.44s over 4 jobs      <- 10%, for a thread pool and four-way peak memory
```

Memory bandwidth on a 7200x10800 raster dominates, not the GIL. Left serial.

**And a spinner regardless**, because none of this makes a cold sheet instant and the honest
answer to "why is nothing happening" is to say what is happening. One line in the panel, most
specific wait first: a selection in flight beats the background read, because somebody who has
just dragged a box is waiting on THAT and saying "reading the sheet" underneath it would be
describing a different wait.

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
| `detail_marker.py` | Draws the detail marker's anchor, then sweeps its score over T3-T10 | Works — the 0.913/0.574 gap the class's gates come from |
| `dm_crosstalk.py` | Every class at once on T5, T9, T10 | Works — the detail and elevation markers do not take each other |
| `dm_words.py` | What the text layer holds around each detail marker | Works — the caption is two words, stacked inside the circle |
| `time_select.py` | Where the wall clock goes on a selection, cold and warm | Works — 23.1 s cold, 0.24 s warm; the reason `/warm` exists |
| `time_warm.py` | A drag landing while the sheet is still being read | Works — 8.94 s, joins the pass rather than duplicating it |
| `time_parallel.py` | Serial vs threaded warming | **Falsified** — 16.0 s vs 14.4 s, not worth a thread pool |
| `uri_test.py` | Select + count on the URI sheet, through the server's own endpoints | Works — 27 doors by the template fallback |
| `uri_diag.py` | Why a tight drag on a URI door snapped to nothing | Works — the arc is a candidate; the drag held <60% of its ink |
| `uri_identify.py` | Which class a URI door is identified as, on every segmentation | **Decisive** — none; `profile_selection` refuses to read it as an arc |
| `uri_doors.py` | `doors.swings_in` run directly on the URI sheet | **Decisive** — 66-80 swings; the detector is fine, identification is not |

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

**Correction: this is NOT why T4's three doors are missed.** All 27 annotated T4 doors are in
one drawing block at one scale, larger side 103-150 px, and the three misses are 120-134 px --
mid-range. The scale bucket is still worth building; it will not recover these.

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
small clean arc scores 1.000. Do not revisit that one. **Revisited anyway and reverted again;
see "Ranking arcs by quality: falsified twice" for the specific failures and for what the fix
would actually take.**

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

**Open, and measured to a standstill: T4's three missed doors.** Four hypotheses, each
falsified by measurement rather than argument. Do not spend another session on gate tuning.

1. *Suppression eats them.* Half true and not the cause. It does take 35-81% of the ink inside
   their boxes, against 0% for every door that is found -- but what it takes is the wall and
   the door leaf drawn flat against it. The arc itself survives, plainly visible in the symbol
   layer.
2. *The arc is not a candidate.* True, for two of them, and not sufficient. Their components
   are 246x246 and 414x236 px because a diagonal leader line is fused into the arc, and
   `SYMBOL_BAND_IN` caps a candidate at 0.67 in = 201 px. Raising the cap to 1.0 in admits
   them and changes the score by nothing, at 36 s -> 51 s on T4.
3. *The sweep cannot find the arc.* False. Given the component, `find_swing` returns a clean
   arc: radius 117 px inside the 72-150 band, span 85 degrees, occupancy 1.000, anchor ink
   0.80. The arc is right there.
4. *The gate is what refuses it.* True, and it cannot simply be relaxed. `is_swing` requires
   `radius >= 0.65 * reach` where reach is the BLOB's bounding box, so a leader line fused
   into a door inflates reach to 246 px and a perfect 117 px arc fails. This is the same
   mistake `Arc.quality` already documents and fixed for the score -- judging an arc by the
   blob it landed in -- surviving in the gate. Waiving the lower bound for an arc that pivots
   on drawn ink recovers nothing and costs precision: 0.960 -> 0.923 on T4, because a diagonal
   line through the centre of a circle satisfies `anchor_ink`, so a row of circles at
   (7559, 2204) becomes a door. It also does not land the recovery it was for -- an arc
   detection reports the COMPONENT's box, so the fused blob's centre sits 106 px from the
   door against a 67 px tolerance and scores as a false positive.

What would actually be needed: report an arc's own extent as its box rather than the
component's, AND a discriminator that separates a door fused to a leader from a circle fused
to one. The distinguishing fact is context the detector does not model -- a door sits in a
wall opening. That is a real piece of work, not a threshold, and it is worth doing only if
doors on other sheets fail the same way. Two of the three are occluded, so they are also the
whole of T4's occluded recall.

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
non-instances score 0.800-0.825. Left at 0.90 deliberately, with the two real instances
visible in review. Do not move it without re-running this measurement.

**Correction: region work will NOT widen that gap.** Four of those non-instances repeat at
identical coordinates on both sheets, which looked like sheet furniture, and two of them are:
they sit in an `unknown` region (the title block) that `regions.countable` still counts, and
excluding unclassified blocks would remove them. But the two nearest the gate, 0.824 and
0.822, are TEXT INSIDE THE DRAWING BLOCK -- the `SEE 1 & D/T10` note among them. No region
classifier can remove those without removing the plan. Separating a hatched triangle from a
run of characters at 0.82 is a scoring problem, and it is what actually gates 0.83.

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
