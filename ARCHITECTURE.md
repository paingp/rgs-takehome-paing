# How Symbol Spotter works

This document explains what happens between dragging a box around a symbol and getting a count,
why each class is detected the way it is, and why the tool was built with classical computer
vision rather than a neural network.

---

## 1. The idea in one paragraph

A construction drawing is a page of black lines on white paper. A symbol — a door, a receptacle
— is a small, repeated arrangement of those lines. If you can isolate each small arrangement of
ink on the sheet, and you have one example of the symbol to compare against, then counting the
symbol is a matter of asking *"which of these blobs look like this one?"* The work is in the
three hard parts: symbols are physically attached to the walls they sit in, they appear at four
rotations, and some symbols are geometrically contained inside others.

---

## 2. The pipeline

Six stages. Each produces something you can look at, which is what makes the tool debuggable.

### Stage 1 — Rasterize (`takeoff/raster.py`)

The page is rendered to a greyscale image at 300 dots per inch. A 36″×24″ sheet becomes a
10,800 × 7,200 pixel array. Nothing after this point knows the drawing was ever a PDF.

Two separate renders come out, and they are never mixed. The **detection raster** is a single
greyscale array that only the detector sees. The **tile pyramid** is a set of small PNG tiles at
several zoom levels, which is how the browser displays a sheet far too large to send as one
image. The viewer never scores anything; the detector never draws anything.

### Stage 2 — Separate structure from symbols (`takeoff/candidates.py`)

This is the stage that makes everything else possible.

On a floor plan, a receptacle is drawn *touching* the wall it is mounted on. If you simply group
connected black pixels, every symbol in the building merges into one enormous blob. So the tool
first removes **structure** — long straight runs of ink, which is what walls, grid lines, leader
lines and the sheet border are — using a morphological "opening" with long horizontal and
vertical elements. Think of it as sliding a long thin ruler across the image and keeping only
ink the ruler fits inside.

What survives is symbol ink. On an electrical sheet this removes about **81%** of the ink in
0.1 seconds and leaves the symbols sitting as clean, isolated shapes.

The remaining ink is grouped into **connected components** — islands of touching pixels — and
filtered to a plausible size band (`SYMBOL_BAND_IN = 0.027″ to 0.67″`, i.e. 8 to 201 pixels).
Those are the **candidates**: a few thousand per sheet instead of millions of pixels.

### Stage 3 — Snap the drag to a symbol (`candidates.snap`)

Nobody drags a pixel-perfect box. `snap` takes the rough box and works out which candidates are
really inside it — a component counts if at least 60% of its *ink* falls in the box — then
unions them into one selection.

Two rules matter here:

- **The box you drew is a hard ceiling.** Ink outside it is cut off, even if it is connected to
  ink inside. Without this, dragging around a diffuser that a curly leader line happens to touch
  silently swallows the leader.
- **A label is not part of the symbol.** An elevation marker is usually drawn next to its
  reference, `C/T9`. A run of similar-sized characters that does not include the main glyph is
  set aside — but it is still shown, greyed out, and one click puts it back. The rule is visible
  and reversible rather than a silent deletion.

### Stage 4 — Score (`takeoff/scoring.py`, `takeoff/templates.py`)

The selection becomes a **template**: a small binary picture of the symbol. It is then rotated
to 0°, 90°, 180° and 270° and mirrored, giving a **bank** of eight variants, because a door can
be hung either hand and a marker can point any way.

Each candidate is compared to each variant using **stroke coverage**, which asks two questions:

- *Forward:* how much of the candidate's ink sits on the template's ink?
- *Backward:* how much of the template's ink does the candidate explain?

The score is **the worse of the two**, and that is deliberate. Forward coverage alone rates a
bare triangle outline as a perfect match for a *hatched* triangle, because every pixel it has
lands on template ink. Backward coverage alone rates a solid blob perfect for the reverse
reason. Taking the minimum requires a candidate to be neither more nor less than the template.

### Stage 5 — Assign competitively (`takeoff/detect.py`)

The tool does not ask *"does this exceed the threshold for a duplex receptacle?"* It asks
**"which registered symbol explains this best, and by how much does it beat the runner-up?"**

Every candidate goes to the class that scores highest, and each physical piece of ink can be
claimed only once. Two numbers come out:

- **`match`** — how good the best fit is.
- **`margin`** — how far ahead of the second-best class it is.

Section 6 explains why one number is not enough.

### Stage 6 — Band (`takeoff/banding.py`)

Each result lands in **counted**, **needs review**, or **rejected**, based on those two numbers
against the class's thresholds. Banding is pure arithmetic and re-runnable — the expensive
detection has already happened — so thresholds can be re-applied without a re-run.

---

## 3. The rasterization boundary, and how it is enforced

The rule is that detection sees pixels and nothing else. Rules like this decay into good
intentions unless something checks them, so `tests/test_raster_only.py` parses the syntax tree
of every detection module and fails if it can reach the PDF library — directly *or through a
chain of first-party imports*.

```
may import pymupdf:   raster.py  spaces.py  layout.py  vector_gt.py
may never:            candidates  templates  scoring  detect  doors
                      lifecycle  banding  schema  classes  regions
```

`layout.py` reads the text layer, but only for captions and sheet metadata — the same words a
person reads off the drawing. `vector_gt.py` uses the vector geometry to bootstrap ground truth
for *grading*, never for detecting. That separation is what makes the cross-check in section 7
meaningful: if the vector layer fed the detector, agreement between them would prove nothing.

---

## 4. How each class is detected

Four classes ship with the tool. Three are matched as pictures; one cannot be.

| Class | Detector | Counted / review | The thing that makes it hard |
|---|---|---|---|
| Interior elevation marker | template bank | 0.90 / 0.80 | hatched, and its caption sits beside it |
| Detail marker | template bank | 0.85 / 0.70 | its own reference number is *inside* the glyph |
| Duplex receptacle | template bank | 0.95 / 0.85 | drawn on a very light CAD layer |
| Single swing door | arc sweep | 0.72 / 0.50 | ink per door varies **11.4×** |

### Interior elevation marker — the straightforward case

A hatched triangle with a sheet reference beside it, appearing at all four quarter turns. It is
the class the template path was built on and it behaves: seven real markers score 0.988–1.000
and the best false positive — a letter `A` — scores 0.808.

The hatching is what separates it from a plain arrowhead, and it is why the score has to be
symmetric: a bare outline would otherwise match perfectly.

### Detail marker — when the caption is inside the symbol

A circle split by a horizontal bar, detail number above and sheet number below, fused to a
hatched arrowhead. **Three features at once**, and nothing else on an architectural sheet has
all three, which is why it separates better than any other class: 15 real instances score
0.913–1.000, and the best thing that is not one scores 0.574.

Its quirk is that the reference is drawn *inside* the glyph, so the template inevitably carries
one instance's number. That is tolerable and was measured rather than assumed: the digit is
210 pixels of ink against 3,626 for the glyph, so markers reading `7/T11` still score 0.977+
against a template reading `4/T12`.

### Duplex receptacle — when the ink is too faint to see

A circle with two parallel lines through it, 0.092″ across — 28 pixels.

This class forced a change to the core, and it was not a threshold. Electrical devices are drawn
on a thin CAD layer: this glyph's *darkest* pixel is 202 and its median is 232, against a global
ink cut-off at 230. At the default setting it arrived as **nine fragments** of a dozen pixels
each, and a template that size matched 2,312 things on the sheet.

The fix is a per-class ink threshold (`ink_threshold=15`), because line weight follows the
symbol's CAD layer rather than the sheet. Lowering it globally is *not* the fix, and the
measurement says so: at a global 15 the extra faint ink merges neighbouring components and the
architectural sheet loses two doors and a marker.

### Single swing door — why a template cannot do it

Doors get a completely different detector, and the reason is measured.

Every door on the reference sheet is the same width — fitted radius 107–121 pixels, a 1.1×
spread. So scale is not the problem. The problem is that **ink per door varies 11.4×**
(144–1,640 pixels), because line suppression leaves a different amount of each one behind: some
keep their leaf, some merge with a dotted demolition line or a keynote bubble. Since stroke
coverage requires a candidate to be neither more nor less than the template, only 47% of
door-to-door pairs clear 0.90, and recall runs anywhere from 0% to 68% purely on *which door you
happened to drag*.

A door's swing is a **circle**, though, and that is invariant to all of it. `takeoff/doors.py`
sweeps a fixed grid of centres and radii and asks one question of each blob: *is there a circle
that most of this ink sits on, sweeping about a quarter turn?* It reports three human-readable
numbers instead of a correlation — the radius, the angle it sweeps, and how continuously it
sweeps it. The radius *is* the door width: 117 pixels is a 3′-0″ door.

The sweep is a fixed grid rather than random sampling on purpose. The original spike used RANSAC
and returned 29–30 swings across five random seeds, agreeing on only 82% of the set; detection
IDs have to be stable across re-runs, so the same ink must always give the same answer.

One more gate separates a door from an office chair, and it is not geometric. A chair back is a
continuous quarter-circle, one stroke wide, at very nearly a door's radius — on shape alone it
is a perfect door, and 17 of them were counted as such. The difference is physical: **a door
pivots about its hinge, and a hinge is a drawn jamb**; a chair back curves about the middle of
an empty seat. The tool samples a small disc at the arc's centre of curvature and asks whether
there is ink there.

---

## 5. Counting a symbol nobody registered

This is the property that makes the tool general, and it is the one worth protecting.

When you drag a box, `detect.profile_selection` **measures what you selected** and picks the
detector from the measurement:

- Is the ink thin — a curve rather than a filled shape?
- Does a circle fit most of it, sweeping about a quadrant?
- Is that circle the size of the thing you selected, rather than a detail inside it?

If yes, it is swept as an arc at *the radius your selection had*. If no, it is kept as a glyph
and matched against a template bank built from your selection. Nothing is declared; nothing is
looked up in a table.

That is why an unregistered symbol counts with no code change. It was tested on a mechanical
sheet drawn by a different consultant, with a vocabulary the tool had never met — diffusers,
dampers, VAVs. Dragging a cross-hatched square diffuser gave 10 counted, 0 in review, best
matches 1.00/1.00/0.99, with the sheet's other devices — hexagonal smoke dampers, plain return
grilles, a fan symbol — landing at 0.64–0.74 and staying out. A second drag on a *different*
instance returned the same 10, which is exactly what the template path could not do for doors.

Registering a class adds a name, a calibrated pair of thresholds and a caption pattern. It does
not change how the symbol is found.

---

## 6. Two confidence numbers, three bands

A single confidence score hides the failure that produces wrong counts.

Consider a **duplex receptacle** ⊖ and a **quad** ⊕. The duplex is *geometrically a subset* of
the quad: every stroke of the duplex is present in the quad, so a quad scores well against a
duplex template almost by construction. Measured against that template, a real duplex scores
**0.816** and a quad scores **0.681** — a gap of only 0.135, which is far too thin to place a
threshold in once line-weight variation and anti-aliasing are accounted for. Three attempts to
widen it all failed.

A single threshold cannot reliably separate those. But `match` and `margin` together do:

- A blurry duplex: **low match, high margin** — "it is a duplex, badly drawn."
- A crisp quad: **high match, low margin** — "looks great, might be the wrong symbol."

One number scores those roughly the same and waves the second straight through. That second case
is the wrong-number failure this project exists to prevent, so it goes to **review**:

| | Meaning |
|---|---|
| **Counted** | Both gates passed. The tool asserts this is real. |
| **Needs review** | Found something, not confident enough to assert it. A question, not a claim. |
| **Rejected** | Below the floor. Not shown. |

---

## 7. Measuring it

Accuracy claims here are graded against annotations a person made, not against the detector's
own output.

You annotate in the viewer — accepting a match records it, `+ Missed` records what the detector
never proposed, and `O` marks an instance something crosses. `eval/harness.py` then matches
detections to annotations by **centre distance** within half a symbol width, rather than by box
overlap. That is deliberate: a partly hidden symbol has poor box overlap with its own annotation
while being unmistakably the same instance, and hidden symbols are exactly what needs measuring.

Where it stands today:

```
sheet  class        TP  FP  FN   precision  recall   +review
T5     doors        31   0   2      1.000    0.939     0.939
T5     markers       9   0   3      1.000    0.750     1.000
T4     doors        24   1   5      0.960    0.828     0.828
E4     receptacles  95   0  37      1.000    0.720     0.864
```

`+review` is recall counting the review-band hits that landed on a real instance — the ceiling
recall would reach if a person confirmed every one. It sits *beside* recall rather than
replacing it, because the gap between the two is exactly the human effort the tool is asking for.

Two independent cross-checks support the receptacle count on E4: a normalised cross-correlation
spike found 92, and clustering repeated *vector* geometry found 90, against the detector's 95.
Three methods sharing no code agreeing within five is the strongest evidence available that the
approach is sound.

---

## 8. Why these design choices

### Why classical computer vision rather than deep learning

**The user picks the symbol at runtime.** That single constraint eliminates most of the
literature.

A trained detector — YOLO, Faster R-CNN — learns a fixed list of classes from thousands of
labelled examples. This tool has no fixed list. An estimator opens a mechanical sheet nobody has
seen, drags a box around a damper, and expects a count. There is nothing to pre-train on, and no
labelled set for a symbol invented thirty seconds ago. This is **one-shot matching**, not
supervised detection, and template matching does one-shot natively while a neural detector
cannot do it at all.

The scale of that gap is worth stating concretely. SkeySpot, the closest published work on
electrical layout symbols, reports 82.5% mAP — **with 2,450 hand-labelled instances across 34
classes**. Producing that labelling is the entire cost this tool is meant to avoid, and it buys
a model that still cannot count the thirty-fifth symbol.

Three further reasons:

- **Interpretability.** A door is reported as *"radius 117 px = 3.1 ft, sweeping 88°, pivoting
  on drawn ink"*. An estimator can check that against the drawing. `0.94` from a neural network
  is not checkable, and a wrong number that cannot be argued with is worse than no number.
- **No training infrastructure.** No GPU, no dataset, no retraining when a firm's drafting
  conventions differ. The whole tool is a `pip install` and a Python process.
- **The door stays open.** `takeoff/scoring.py` defines a `Scorer` protocol — a small interface
  taking two masks and returning numbers. A learned embedding drops in behind it without
  touching the pipeline, *once the classical baseline is measured rather than assumed*. Choosing
  classical first is not a rejection of learning; it is refusing to pay for it before knowing
  what it has to beat.

### Why detection runs on raster

A large share of real drawings are scans. A detector reading vector paths would score
beautifully on clean exports and fail completely on a photocopy, and the failure would arrive
late, in front of a customer. Working from pixels means a scan and a born-digital PDF take
exactly the same path, so the tool cannot be accidentally over-fitted to one input format.

It also makes the vector cross-check meaningful. Because `vector_gt.py` is grading-only and
cannot reach the detector, the 90-vs-95 agreement on E4 is genuine independent evidence rather
than a system agreeing with itself.

### Why competitive assignment instead of per-template thresholds

Because of the nested-symbol problem in section 6. Asking *"which template explains this best,
and by how much?"* dissolves a case that independent thresholds cannot separate at all — the
quad is in the library, scores near 1.0 against its own template, and stops stealing duplexes.
The margin then becomes an honest confidence signal instead of a tuning parameter.

### Why doors get their own detector

Because the measurement said so, and because it is the test of whether the core is
under-general. The 11.4× ink variation is not something a threshold can reach. A door's *radius*
does not vary at all, so the right model is a parametric one. The registry records which
detector a class needs, and `profile_selection` chooses automatically for anything unregistered.

### Why adding a symbol is a registry entry, not a pipeline change

`takeoff/classes.py` holds one entry per class: where its reference glyph lives, its two
thresholds, and any per-class quirk such as an ink threshold. If adding a symbol ever requires
touching the detector, that is evidence the core is under-general — and it is reported as such
rather than patched over. It has happened twice, both times for the duplex receptacle, and both
times the fix was a new per-class knob rather than a special case.

---

## 9. Honest limits

- **Doors do not yet generalise to another firm's drawings.** Tested against a University of
  Rhode Island bid set by a different architect, the arc detector itself works — run directly it
  finds 66–80 swings at exactly the radius the sheet's scale predicts. But the step that decides
  *which class you selected* refuses to read those doors as arcs, so the generic template path
  runs instead and finds 27 where the arc sweep would find 75. The cause is measured and written
  up; the fix is a real piece of work, not a threshold.
- **A fix for it was tried twice and reverted twice.** Ranking arc hypotheses by quality rather
  than by evidence looks decisive in isolation and breaks precision when run through the full
  pipeline. Both attempts are recorded so a third costs nothing to rule out.
- **The chair/door separation is thin.** The margin between the best chair and the weakest door
  is 0.05, from two sheets. It wants re-deriving from more ground truth rather than trusting.
- **An under-resolved image cannot work**, and the tool now says so instead of returning a
  silent zero.

The full record — every measurement, and every hypothesis that turned out to be wrong — is in
[`docs/ENGINEERING-LOG.md`](docs/ENGINEERING-LOG.md).
