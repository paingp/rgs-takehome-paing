# Symbol Spotter — session handoff

Last updated 2026-08-26. Research + spikes complete; no production code yet. Ready to build.

**Executable plan:** `C:\Users\paing\.claude\plans\crystalline-gliding-bear.md`  <- start here
**Narrative version:** https://claude.ai/code/artifact/4fd71584-3fad-4549-bfb0-d67e9bb2f833

## Build is GATED

Stop at each gate, report what works and what deliberately does not, hand over test instructions, and
wait. Detectors are built **one symbol class at a time** — Paing names the symbol, it goes fully
end-to-end, then stop and test before the next.

  Gate 1  foundations + viewer
  Gate 2  symbol selection UI          <- Paing explicitly wants to test here
  Gate 3  ground truth on T5
  Gate 4  first symbol end-to-end      <- Paing picks the symbol
  Gate 5..N  one symbol per gate
  Final   hardening, robustness, CI

Adding a symbol must be a registry entry in `takeoff/classes.py`, not a pipeline change. If it isn't,
report that at the gate — it means the core is under-general.

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
| `xycut.py` / `xycut2.py` | Recursive XY-cut splitter | **Rejected** — regressed pages 4/5, see decision 5 |

Rendered PNGs in `scratch/` are visual evidence for the findings above; `sym_crop.png` in particular
shows line suppression cleanly isolating door arcs, receptacles and markers.

---

## Next step

Read the plan file, then start **Gate 1** (foundations + viewer) and stop for testing. Ground truth and
the eval harness (Gates 3 and 4) deliberately land before the hard detector work, so nested-symbol
discrimination and the door arc detector are measured rather than eyeballed.
