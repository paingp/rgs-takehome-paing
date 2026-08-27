"""SPIKE 8 -- can the template path count door arcs, or does a door need doors.py?

Throwaway, per the scratch/ convention. VERDICT: doors need the parametric detector.

Measured on T5, 300 DPI:

  Radius is NOT the problem. The "3x spread" quoted earlier came from thin blobs generally,
  not from verified arcs. Fitting circles to the ink says every door on the sheet is one
  width: 107-121 px radius, a 1.1x spread, 2.9-3.2 ft at 1/8in = 1ft-0in. A 3'-0" door.

  Template matching fails anyway, and not for a reason tuning can reach. Scoring all 29
  swings against each other: only 47% of door-to-door pairs clear 0.90, and recall depends
  entirely on which instance you happen to drag -- from 0% to 68%. The cause is visible in
  one number: ink per swing varies 11.4x (144-1640 px), because line suppression leaves a
  different amount of each door behind. Symmetric coverage asks a candidate to be neither
  more nor less than the template, which nothing can satisfy across an 11x range.

  The parametric test works. RANSAC over each thin blob, asking whether a large subset of
  its ink lies on one circle, finds 29 swings in 0.83 s, all confirmed by eye in
  spike8_swings.png -- including the ones merged with a keynote bubble or a dotted demo
  line, which a whole-blob circle fit rejects (22 of 29) because the extra ink drags the fit.

  CAVEAT: RANSAC is stochastic. Across five seeds it returns 29-30 swings but only 82% the
  same set (27 always, 33 at least once). Decision 10 needs ids stable across re-runs, so a
  production version must be deterministic -- an exhaustive sweep over a radius grid, or
  hypotheses seeded from the blob's own corners, rather than random triples.

  SEPARATE FINDING, unrelated to the detector: dragging a box around the door beside the
  elevator snaps to the GS/GC keynote bubble sitting inside its swing, not to the arc. Door
  selection has a usability problem before detection is even reached.

There is no ground truth for doors, so nothing here reports accuracy. Two things stand in
for it: an independent geometric test that shares no machinery with template matching, and
contact sheets for the eye. Agreement between two unrelated methods is evidence; a number
from one of them is not.

Run:  .venv/Scripts/python.exe scratch/spike8_doors.py [stage]
      stages: population | arcfit | ransac | template   (default: all)
"""

from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from takeoff import candidates as cand
from takeoff import raster

OUT = Path(__file__).parent
PDF = "Skanksa.pdf"
T5 = 4

# A door arc is a thin curve: a lot of bounding box, very little ink in it. These bounds are
# deliberately loose -- the point of the spike is to find out what is actually in here.
ARC_SIZE_PX = (55, 210)
ARC_MAX_FILL = 0.12


def load():
    r = raster.render(PDF, T5, dpi=300)
    layers = cand.ink_layers(r)
    return r, layers, cand.find_candidates(r, layers)


def thin_blobs(found):
    out = []
    for c in found:
        w, h = c.bbox_px[2], c.bbox_px[3]
        if not (ARC_SIZE_PX[0] <= max(w, h) <= ARC_SIZE_PX[1]):
            continue
        if c.area_px / max(w * h, 1) > ARC_MAX_FILL:
            continue
        out.append(c)
    return out


# ------------------------------------------------------------------ independent arc test


def fit_arc(mask: np.ndarray, bbox) -> dict:
    """Fit a circle to a component's ink and report how arc-like it is.

    Algebraic (Kasa) circle fit: minimising |p|^2 - 2c.p - r^2 + |c|^2 is linear in the
    unknowns, so it is a least-squares solve rather than an iteration, which matters when
    this runs over every thin blob on a sheet.

    Three numbers come out, and a door needs all three:
        residual   how far the ink sits from the fitted circle, in pixels
        span       how much of the circle the ink actually covers, in degrees
        occupancy  how much of that span has ink in it -- a full sweep with two opposite
                   stubs would otherwise look like a wide arc
    """
    ys, xs = np.nonzero(mask)
    if len(xs) < 12:
        return {"ok": False}
    x = xs.astype(np.float64) + bbox[0]
    y = ys.astype(np.float64) + bbox[1]

    A = np.stack([x, y, np.ones_like(x)], 1)
    b = x**2 + y**2
    try:
        sol, *_ = np.linalg.lstsq(A, b, rcond=None)
    except np.linalg.LinAlgError:  # pragma: no cover - degenerate ink
        return {"ok": False}
    cx, cy = sol[0] / 2, sol[1] / 2
    r2 = sol[2] + cx**2 + cy**2
    if r2 <= 0:
        return {"ok": False}
    radius = float(np.sqrt(r2))

    d = np.sqrt((x - cx) ** 2 + (y - cy) ** 2)
    residual = float(np.abs(d - radius).mean())

    angles = np.degrees(np.arctan2(y - cy, x - cx)) % 360
    hist = np.zeros(72, bool)                       # 5-degree buckets
    hist[(angles // 5).astype(int) % 72] = True
    # Widest run of EMPTY buckets is the gap; the span is what is left.
    doubled = np.concatenate([hist, hist])
    gap, run = 0, 0
    for v in doubled:
        run = 0 if v else run + 1
        gap = max(gap, run)
    gap = min(gap, 72)
    span = (72 - gap) * 5.0
    occupancy = hist.sum() * 5.0 / max(span, 1e-9)

    return {
        "ok": True,
        "cx": cx, "cy": cy, "radius": radius,
        "residual": residual,
        "span": span,
        "occupancy": min(occupancy, 1.0),
    }


def is_arc(fit: dict, bbox) -> bool:
    """A door's swing: a clean circular fit sweeping most of a quadrant, not much more."""
    if not fit["ok"]:
        return False
    reach = max(bbox[2], bbox[3])
    return (
        fit["residual"] <= 2.5                      # ink sits on the circle
        and 0.6 * reach <= fit["radius"] <= 1.8 * reach
        and 55 <= fit["span"] <= 135                # about a quadrant
        and fit["occupancy"] >= 0.75                # continuously, not in two stubs
    )


# ----------------------------------------------------------------------------- reporting


def contact_sheet(r, items, path, cols=10, cell=150, caption=None):
    rows = (len(items) + cols - 1) // cols
    sheet = np.full((max(rows, 1) * cell, cols * cell, 3), 255, np.uint8)
    for i, c in enumerate(items):
        x, y, w, h = c.bbox_px
        pad = 8
        patch = r.gray[max(y - pad, 0): y + h + pad, max(x - pad, 0): x + w + pad]
        if patch.size == 0:
            continue
        k = min((cell - 30) / patch.shape[0], (cell - 30) / patch.shape[1])
        patch = cv2.resize(patch, None, fx=k, fy=k, interpolation=cv2.INTER_AREA)
        patch = cv2.cvtColor(patch, cv2.COLOR_GRAY2BGR)
        ry, rx = divmod(i, cols)
        oy, ox = ry * cell + 22, rx * cell + (cell - patch.shape[1]) // 2
        sheet[oy: oy + patch.shape[0], ox: ox + patch.shape[1]] = patch
        if caption:
            cv2.putText(sheet, caption(c), (rx * cell + 5, ry * cell + 15),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.36, (140, 60, 0), 1, cv2.LINE_AA)
    cv2.imwrite(str(path), sheet)
    return path


def stage_population(r, layers, found):
    blobs = thin_blobs(found)
    print(f"thin sparse blobs in door-arc range: {len(blobs)}")
    dims = np.array([max(c.bbox_px[2], c.bbox_px[3]) for c in blobs])
    print(f"  reach px   min {dims.min()}  p25 {np.percentile(dims,25):.0f}  "
          f"median {np.median(dims):.0f}  p75 {np.percentile(dims,75):.0f}  max {dims.max()}")
    print(f"  spread     {dims.max()/dims.min():.1f}x")
    contact_sheet(r, sorted(blobs, key=lambda c: -max(c.bbox_px[2], c.bbox_px[3])),
                  OUT / "spike8_population.png",
                  caption=lambda c: f"{max(c.bbox_px[2], c.bbox_px[3])}px")
    print(f"  -> scratch/spike8_population.png")
    return blobs


def stage_arcfit(r, blobs):
    arcs, rejects = [], []
    for c in blobs:
        fit = fit_arc(c.mask, c.bbox_px)
        (arcs if is_arc(fit, c.bbox_px) else rejects).append((c, fit))
    print(f"\ngeometric arc test: {len(arcs)} of {len(blobs)} thin blobs are circular arcs")
    if arcs:
        radii = np.array([f["radius"] for _, f in arcs])
        print(f"  fitted radius  min {radii.min():.0f}  median {np.median(radii):.0f}  "
              f"max {radii.max():.0f} px   ({radii.max()/radii.min():.1f}x spread)")
        print(f"  in inches      {radii.min()/300:.2f} - {radii.max()/300:.2f} in")
        spans = np.array([f["span"] for _, f in arcs])
        print(f"  swept angle    median {np.median(spans):.0f} deg  "
              f"range {spans.min():.0f}-{spans.max():.0f}")
    contact_sheet(r, [c for c, _ in arcs], OUT / "spike8_arcs.png",
                  caption=lambda c: f"{max(c.bbox_px[2], c.bbox_px[3])}px")
    contact_sheet(r, [c for c, _ in rejects][:60], OUT / "spike8_notarcs.png",
                  caption=lambda c: f"{max(c.bbox_px[2], c.bbox_px[3])}px")
    print(f"  -> scratch/spike8_arcs.png, scratch/spike8_notarcs.png")
    return arcs


# ------------------------------------------- finding an arc INSIDE a noisy component
#
# The whole-component circle fit above rejected real doors: on this sheet an arc is often
# merged with the ink beside it -- a dotted demolition line, a keynote bubble's leader, the
# wall it swings from -- so the fit is dragged off by ink that was never part of the swing.
# RANSAC asks a different question: is there a circle that a large subset of this ink lies
# on, whatever else is in the blob?

RANSAC_ITERS = 260
RANSAC_TOL_PX = 2.0


def _span_and_occupancy(angles: np.ndarray) -> tuple[float, float]:
    """Degrees of circle the ink covers, and how continuously it covers them."""
    hist = np.zeros(72, bool)                       # 5-degree buckets
    hist[(angles // 5).astype(int) % 72] = True
    doubled = np.concatenate([hist, hist])
    gap, run = 0, 0
    for v in doubled:
        run = 0 if v else run + 1
        gap = max(gap, run)
    span = (72 - min(gap, 72)) * 5.0
    return span, float(hist.sum() * 5.0 / max(span, 1e-9))


def ransac_arc(mask: np.ndarray, bbox, rng: np.random.Generator) -> dict:
    ys, xs = np.nonzero(mask)
    if len(xs) < 30:
        return {"ok": False}
    pts = np.stack([xs + bbox[0], ys + bbox[1]], 1).astype(np.float64)

    reach = max(bbox[2], bbox[3])
    lo, hi = 0.55 * reach, 2.0 * reach
    best = {"ok": False, "inliers": 0}

    for _ in range(RANSAC_ITERS):
        i, j, k = rng.choice(len(pts), 3, replace=False)
        p, q, s = pts[i], pts[j], pts[k]
        d = 2 * (p[0] * (q[1] - s[1]) + q[0] * (s[1] - p[1]) + s[0] * (p[1] - q[1]))
        if abs(d) < 1e-6:
            continue
        px, qy, sz = (p**2).sum(), (q**2).sum(), (s**2).sum()
        cx = (px * (q[1] - s[1]) + qy * (s[1] - p[1]) + sz * (p[1] - q[1])) / d
        cy = (px * (s[0] - q[0]) + qy * (p[0] - s[0]) + sz * (q[0] - p[0])) / d
        radius = float(np.hypot(p[0] - cx, p[1] - cy))
        if not (lo <= radius <= hi):
            continue

        dist = np.abs(np.hypot(pts[:, 0] - cx, pts[:, 1] - cy) - radius)
        inlier = dist <= RANSAC_TOL_PX
        n = int(inlier.sum())
        if n <= best["inliers"]:
            continue

        angles = np.degrees(np.arctan2(pts[inlier, 1] - cy, pts[inlier, 0] - cx)) % 360
        span, occupancy = _span_and_occupancy(angles)
        best = {
            "ok": True, "inliers": n, "cx": cx, "cy": cy, "radius": radius,
            "span": span, "occupancy": occupancy, "share": n / len(pts),
        }
    return best


def is_swing(fit: dict, bbox) -> bool:
    """A door swing: a quadrant of clean circle, made of most of the blob's thin ink."""
    if not fit.get("ok"):
        return False
    reach = max(bbox[2], bbox[3])
    return (
        0.7 * reach <= fit["radius"] <= 1.6 * reach
        and 60 <= fit["span"] <= 130
        and fit["occupancy"] >= 0.8
        and fit["inliers"] >= 60
    )


def stage_ransac(r, blobs):
    rng = np.random.default_rng(0)
    hits, misses = [], []
    for c in blobs:
        fit = ransac_arc(c.mask, c.bbox_px, rng)
        (hits if is_swing(fit, c.bbox_px) else misses).append((c, fit))

    print(f"\nRANSAC arc-in-blob: {len(hits)} of {len(blobs)} thin blobs contain a door swing")
    if hits:
        radii = np.array([f["radius"] for _, f in hits])
        share = np.array([f["share"] for _, f in hits])
        print(f"  fitted radius  min {radii.min():.0f}  median {np.median(radii):.0f}  "
              f"max {radii.max():.0f} px  ({radii.max()/radii.min():.1f}x spread)")
        print(f"  in inches      {radii.min()/300:.2f} - {radii.max()/300:.2f}")
        print(f"  door width     {radii.min()/300*8:.1f} - {radii.max()/300*8:.1f} ft "
              f"(at 1/8in = 1ft-0in)")
        print(f"  arc is {share.min():.0%}-{share.max():.0%} of its blob's ink "
              f"(median {np.median(share):.0%}) -- low means merged with other ink")
    contact_sheet(r, [c for c, _ in hits], OUT / "spike8_swings.png",
                  caption=lambda c: f"{max(c.bbox_px[2], c.bbox_px[3])}px")
    print(f"  -> scratch/spike8_swings.png")
    return hits


# --------------------------------------------------- the template path, on the same sheet
#
# The comparison the spike exists for. One clean door is selected the way a person would
# select it, and the existing pipeline is asked to count the rest. Nothing about doors is
# special-cased -- if the generic path can do this, doors.py is not needed.

DOOR_DRAG = (6360, 2890, 155, 165)     # a clean single swing beside the elevator on T5


def stage_template(r, found, swings):
    import dataclasses

    from takeoff import banding, classes, detect

    door = classes.SymbolClass(
        id="door_swing",
        name="Single swing door",
        anchor=classes.TemplateAnchor(page_index=T5, drag_bbox_px=DOOR_DRAG),
        size_tolerance=0.30,
        counted_at=0.90,
        review_floor=0.80,
    )
    selection = cand.snap(found, DOOR_DRAG, dpi=r.dpi)
    entry = detect.entry_from_selection("door_swing", selection, page_index=T5, symbol=door)
    t = entry.template
    print(
        f"\ntemplate from one door: {t.size_px} px = "
        f"{t.size_in[0]:.2f} x {t.size_in[1]:.2f} in"
        f"   ink {t.ink_px} (fill {t.fill:.3f})   context blobs {t.context_blobs}"
    )

    for scales in ((1.0,), (0.8, 1.0, 1.25)):
        e = dataclasses.replace(entry, bank=__import__("takeoff.templates", fromlist=["x"])
                                .variants(t, door.rotations, door.mirrors, scales))
        dets = detect.detect(r, found, [e], keep_rejected=False)
        counted = [d for d in dets if d.status is banding.Status.COUNTED]
        review = [d for d in dets if d.status is banding.Status.REVIEW]

        # Agreement: did a template hit land on ink RANSAC also called a swing?
        agree = 0
        for d in counted:
            box = d.bbox_px
            if any(
                cand._intersection_area(c.bbox_px, box) > 0.3 * c.bbox_px[2] * c.bbox_px[3]
                for c, _ in swings
            ):
                agree += 1
        print(f"  scales {str(scales):18s} bank={len(e.bank):2d}  counted {len(counted):2d}  "
              f"review {len(review):2d}  agreeing with RANSAC {agree}/{len(counted) or 1}")
        if counted:
            m = np.array([d.match for d in counted])
            print(f"      match range {m.min():.3f}-{m.max():.3f}")
        contact_sheet(r, [type("C", (), {"bbox_px": d.bbox_px})() for d in counted],
                      OUT / f"spike8_template_{len(scales)}.png",
                      caption=lambda c: "")
    print(f"  RANSAC found {len(swings)} swings for comparison")


def main() -> None:
    stage = sys.argv[1] if len(sys.argv) > 1 else "all"
    r, layers, found = load()
    print(f"T5 at {r.dpi} DPI: {len(found)} candidates\n")

    blobs = stage_population(r, layers, found)
    if stage in ("all", "arcfit"):
        stage_arcfit(r, blobs)
    swings = stage_ransac(r, blobs)
    if stage in ("all", "template", "compare"):
        stage_template(r, found, swings)


if __name__ == "__main__":
    main()
