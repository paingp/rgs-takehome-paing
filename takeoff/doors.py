"""Parametric arc detector.

Doors get a swept radius range rather than a fixed size band -- a door's arc radius
*is* its width.

WHY A DOOR IS NOT A TEMPLATE (scratch/spike8_doors.py, measured on T5)

Template matching cannot count doors, and not because of scale. Every door on T5 is one
width: fitted radius 107-121 px, a 1.1x spread, 3'-0" at 1/8in = 1ft-0in. What defeats the
template path is that **ink per door varies 11.4x** (144-1640 px), because line suppression
leaves a different amount of each one behind -- some keep their leaf, some merge with a
dotted demolition line or a keynote bubble's leader. Symmetric coverage asks a candidate to
be neither more nor less than the template, which nothing satisfies across an 11x range.
Only 47% of door-to-door pairs clear 0.90, and recall runs 0%-68% purely on which instance a
person happened to drag.

A door's swing is a *circle*, though, and that is invariant to all of it. This module asks
one question of a blob of ink -- is there a circle most of this ink sits on, sweeping about a
quadrant -- and answers it without caring what else got merged in.

DETERMINISM

The spike used RANSAC and returned 29-30 swings across five seeds, agreeing on only 82% of
the set. Decision 10 requires detection ids stable across re-runs, so random sampling is not
usable here. This sweeps a fixed grid of radii and centres instead: the same ink always gives
the same answer, and the cost is bounded and predictable rather than probabilistic.

Raster-only module: must never import pymupdf, directly or transitively.
Enforced by tests/test_raster_only.py.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from takeoff.candidates import INK_THRESHOLD, BBox, Candidate

# A door's swing, in inches of radius. 2'-0" to 4'-0" at 1/8in = 1ft-0in covers a cupboard
# door through a wide single leaf; T5's are all 0.36-0.40 in (3'-0").
RADIUS_BAND_IN = (0.24, 0.52)

# Radii are swept at this step. Finer buys nothing: the inlier tolerance below is +/-2 px, so
# steps much under that are testing the same circle twice.
RADIUS_STEP_PX = 3.0

# How far an ink pixel may sit from the swept circle and still count as on it. Line work is
# 2-3 px wide at 300 DPI, so this is about one stroke.
INLIER_TOL_PX = 2.0

# A swing sweeps roughly a quadrant. Below the floor it is a corner, a fillet or a leader
# line seen from far enough off-centre to subtend an angle; above the ceiling it is a circle
# -- a grid bubble, a column, a keynote ellipse.
#
# 65 rather than 55 because a door is drawn open to about 90 degrees and nothing on T5 lands
# between: 29 real doors span 70-95, and the single detection at 55 is a leader line through
# a note. A door drawn at 45 degrees open would need this lowered, and would then need
# something else to keep that leader out.
SPAN_DEG = (65.0, 140.0)

# ...and it must sweep it continuously. A full circle with two opposite stubs has a wide span
# and almost no occupancy, which is how grid bubbles get in without this.
#
# Kept loose ON PURPOSE. Measured on T5 every real door sits at occupancy 1.00 exactly and
# every non-door below it, so a hard gate at 0.95 would separate them perfectly -- and would
# also make a door with a stop symbol drawn across its swing vanish silently instead of
# landing in review. The gate stays wide and OCCUPANCY_WEIGHT does the discriminating, so a
# broken arc is scored down rather than dropped.
MIN_OCCUPANCY = 0.75

# Occupancy is raised to this power in the quality score. A drafted swing is one continuous
# pass, so anything short of complete is a strong signal: on T5 the appliance box at 0.87 and
# the leader diagonals at 0.91 are the closest non-doors, and this pushes them to 0.57-0.59
# while every real door stays at 1.00.
OCCUPANCY_WEIGHT = 4

# The arc must be a real part of the blob, not six pixels of coincidence on a big one.
MIN_INLIERS = 55

# How many times a blob may be re-swept after its ink has been peeled back. The sweep ranks
# hypotheses by inlier count, so when something denser than the swing shares the component
# the densest circle is not the door: both RE/EX doors in room 218 on T5 have a keynote
# ellipse touching the swing, and the ellipse's top edge wins at stroke_ratio 2.8.
#
# Peeling deletes the ink of a fit that was refused and sweeps what is left. Measured on T5
# the top door needs one retry and the bottom one needs two, so three fits is the budget; a
# fourth found nothing anywhere on T5 or T4. Blobs whose first fit is accepted -- every one
# of the 29 doors already counted -- never peel and cost exactly what they cost today.
PEEL_ROUNDS = 3

# Ink pixels per unit of arc length. A drafted arc is ONE STROKE wide, so a genuine swing
# lands near 1.2 whatever else is in its blob; a circle threaded through a thick or clustered
# mass -- a keynote ellipse, a corner of leader lines -- collects far more. Measured on T5:
# real doors 1.16-1.50, everything else 2.09-3.03, and nothing in between.
#
# This is the one measure that survives contamination. Anything computed over the whole blob
# (what share of it the arc is, how much ink sits outside the swing) punishes a door for what
# the drawing merged into it, which is how the room 217 door came to be rejected at 0.47
# despite a flawless 95-degree swing: a wall jamb shared its component.
STROKE_RATIO_CLEAN = 1.3
STROKE_RATIO_JUNK = 2.2

# A closed ring is rejected before any sweep. This matters because a full circle can always
# be re-read as a partial arc: seen from a centre a few pixels off, the ink at any one radius
# forms a contiguous segment, and on a 110 px bubble that segment measures a clean 140
# degrees at occupancy 1.0. Span and occupancy cannot refuse it -- only noticing that the
# blob closes on itself can. T5's grid bubbles are circles of very nearly a door's radius.
RING_OCCUPANCY = 0.9        # of the full 360 about the blob's own centroid
RING_RADIAL_SPREAD = 0.22   # radial scatter, relative to mean radius

# Ink this dense is not a thin curve. A door arc on T5 fills 2-10% of its bounding box.
MAX_FILL = 0.14

# An arc's centre of curvature, sampled over a disc this big, in inches on paper.
ANCHOR_RADIUS_IN = 0.027        # 8 px at 300 DPI

# How much of that disc must be ink for the arc to count as ANCHORED -- pivoting about
# something the drawing actually drew.
#
# This is what separates a door from an office chair, and T4 is full of chairs. A chair back
# is a continuous quarter-circle, one stroke wide, at very nearly a door's radius: on the
# geometry alone it is a perfect door, and 17 of them were counted as such. The difference is
# physical. A door swings about its hinge and the hinge is a drawn jamb; a chair back curves
# about the middle of the seat, which is empty.
#
# Measured: on T4 the best chair reaches 0.343 and the weakest door 0.554; on T5 the weakest
# door is 0.394. 0.37 is the midpoint of the binding pair. The probe radius was swept and
# 0.027 in gives the widest gap -- smaller and a chair's seat lines fill the disc, larger and
# the wall beside a chair does.
#
# BE HONEST ABOUT THIS ONE: the margin is 0.05, far tighter than the score gaps elsewhere in
# this project, and it is two sheets' worth of evidence. It wants re-deriving from ground
# truth rather than trusting.
ANCHOR_FRACTION = 0.37

# Centres are searched on a grid this many pixels apart, over a box around the blob. A door's
# hinge sits at a corner of its own bounding box, so the search does not need to be wide.
CENTRE_STEP_PX = 2.0
CENTRE_COARSE_PX = 8.0      # first pass; the fine pass refines around its best few
CENTRE_REFINE_SEEDS = 3
CENTRE_MARGIN = 0.30        # of the blob's reach, beyond its bbox


@dataclass(frozen=True)
class Arc:
    """A circular arc found in a blob of ink."""

    centre_px: tuple[float, float]
    radius_px: float
    span_deg: float
    occupancy: float          # share of the span that actually has ink on it
    inliers: int
    share: float              # inliers over the blob's total ink
    stray: float              # ink that is neither on the arc nor inside the swing
    anchor_ink: float = 0.0   # share of a small disc at the centre that is ink

    def radius_in(self, dpi: float) -> float:
        return self.radius_px / dpi

    def width_ft(self, dpi: float, plan_scale: float = 8.0) -> float:
        """Door width in feet. `plan_scale` is feet per inch of paper: 8 at 1/8in = 1ft-0in."""
        return self.radius_px / dpi * plan_scale

    @property
    def arc_length_px(self) -> float:
        return self.radius_px * math.radians(self.span_deg)

    @property
    def stroke_ratio(self) -> float:
        """Ink pixels per unit of arc length: how thick the thing on the circle is."""
        return self.inliers / max(self.arc_length_px, 1e-9)

    @property
    def quality(self) -> float:
        """A single 0-1 number for banding: a continuous arc, one stroke wide.

        Two things, both about the arc alone: is it continuous, and is it one stroke wide.
        Every earlier version measured the arc against its whole blob -- what share of the ink it was, how much ink lay outside the swing -- and
        both punish a door for ink the drawing merged into it. The room 217 door has a
        flawless 95-degree swing and scored 0.47 because a wall jamb shares its component.

        Deliberately not a probability. It exists so an arc passes through the same two gates
        as a template match, and so a marginal one lands in review rather than being dropped.
        """
        span = (STROKE_RATIO_JUNK - STROKE_RATIO_CLEAN)
        thin = (STROKE_RATIO_JUNK - self.stroke_ratio) / span
        continuity = min(1.0, self.occupancy) ** OCCUPANCY_WEIGHT
        return float(continuity * max(0.0, min(1.0, thin)))


def _span_and_occupancy(angles: np.ndarray) -> tuple[float, float]:
    """Degrees of circle the ink covers, and how continuously it covers them.

    Buckets angles at 5 degrees and finds the widest empty run: the span is what is left
    after the largest gap, so an arc broken by a doorstop still reads as one arc rather than
    as two arcs 180 degrees apart.
    """
    hist = np.zeros(72, bool)
    hist[(angles // 5).astype(int) % 72] = True
    if not hist.any():
        return 0.0, 0.0

    doubled = np.concatenate([hist, hist])
    gap = run = 0
    for filled in doubled:
        run = 0 if filled else run + 1
        gap = max(gap, run)
    span = (72 - min(gap, 72)) * 5.0
    return span, float(hist.sum() * 5.0 / max(span, 1e-9))


def is_closed_ring(mask: np.ndarray) -> bool:
    """Does this blob close on itself at a near-constant radius -- a bubble, not a swing?

    Measured about the ink's own centroid: a ring's ink sits all the way round at one
    distance, an arc's covers a fraction of the circle. Both tests are needed. A filled
    square also spans 360 degrees but its radial scatter is wide; a short thick arc has tight
    scatter but covers only a slice.
    """
    ys, xs = np.nonzero(mask)
    if len(xs) < MIN_INLIERS:
        return False

    cx, cy = xs.mean(), ys.mean()
    dx, dy = xs - cx, ys - cy
    radial = np.hypot(dx, dy)
    mean_radius = radial.mean()
    if mean_radius <= 1e-6:
        return False
    if radial.std() / mean_radius > RING_RADIAL_SPREAD:
        return False

    angles = np.degrees(np.arctan2(dy, dx)) % 360
    hist = np.zeros(72, bool)
    hist[(angles // 5).astype(int) % 72] = True
    return hist.mean() >= RING_OCCUPANCY


def find_arc(
    mask: np.ndarray,
    bbox: BBox,
    dpi: float,
    radius_band_in: tuple[float, float] = RADIUS_BAND_IN,
    page_ink: np.ndarray | None = None,
) -> Arc | None:
    """The best circular arc in one blob of ink, or None.

    Exhaustive over a grid of centres and radii, so the same ink always gives the same arc.
    The candidate centres are bounded by the blob: a door hinges at a corner of its own
    bounding box, so there is no need to search the sheet.
    """
    ys, xs = np.nonzero(mask)
    if len(xs) < MIN_INLIERS:
        return None
    if is_closed_ring(mask):
        return None

    x = xs.astype(np.float64) + bbox[0]
    y = ys.astype(np.float64) + bbox[1]
    total = len(x)

    lo = max(RADIUS_STEP_PX, radius_band_in[0] * dpi)
    hi = radius_band_in[1] * dpi
    if hi < lo:
        return None

    reach = max(bbox[2], bbox[3])
    margin = CENTRE_MARGIN * reach
    x0, x1 = bbox[0] - margin, bbox[0] + bbox[2] + margin
    y0, y1 = bbox[1] - margin, bbox[1] + bbox[3] + margin
    radii = np.arange(lo, hi + 1e-9, RADIUS_STEP_PX)

    def evaluate(cx: float, cy: float) -> Arc | None:
        """The best arc centred here. Distances are sorted once, so each radius is two
        binary searches rather than a pass over every point."""
        dy = y - cy
        dist = np.sqrt((x - cx) ** 2 + dy * dy)
        sorted_dist = np.sort(dist)
        counts = np.searchsorted(sorted_dist, radii + INLIER_TOL_PX, "right") - np.searchsorted(
            sorted_dist, radii - INLIER_TOL_PX, "left"
        )
        viable = np.flatnonzero(counts >= MIN_INLIERS)
        if viable.size == 0:
            return None

        # Only the strongest few radii are worth the angular test; the rest are shifted
        # copies of the same ink and cannot beat them on inlier count.
        for k in viable[np.argsort(-counts[viable])][:4]:
            radius = float(radii[k])
            inlier = np.abs(dist - radius) <= INLIER_TOL_PX
            angles = np.degrees(np.arctan2(dy[inlier], (x - cx)[inlier])) % 360
            span, occupancy = _span_and_occupancy(angles)
            if not (SPAN_DEG[0] <= span <= SPAN_DEG[1]) or occupancy < MIN_OCCUPANCY:
                continue

            # Ink inside the swept circle is what a door's leaf and stop look like; ink
            # beyond it belongs to something else that got merged into this blob.
            within = dist <= radius + INLIER_TOL_PX
            stray = float((~(inlier | within)).mean())

            return Arc(
                centre_px=(float(cx), float(cy)),
                radius_px=radius,
                span_deg=span,
                occupancy=occupancy,
                inliers=int(counts[k]),
                share=int(counts[k]) / total,
                stray=stray,
                anchor_ink=(
                    anchor_ink_fraction((cx, cy), page_ink, dpi)
                    if page_ink is not None else 0.0
                ),
            )
        return None

    # Coarse pass, then refine around the best few. Searching every centre at the fine step
    # cost 42 s on one sheet for an answer the coarse pass already brackets: the inlier count
    # varies smoothly with the centre, so a true centre is always near a good coarse one.
    coarse: list[tuple[int, Arc]] = []
    for cx in np.arange(x0, x1 + 1e-9, CENTRE_COARSE_PX):
        for cy in np.arange(y0, y1 + 1e-9, CENTRE_COARSE_PX):
            arc = evaluate(cx, cy)
            if arc is not None:
                coarse.append((arc.inliers, arc))
    if not coarse:
        return None

    coarse.sort(key=lambda pair: -pair[0])
    best = coarse[0][1]
    for _, seed in coarse[:CENTRE_REFINE_SEEDS]:
        sx, sy = seed.centre_px
        for cx in np.arange(sx - CENTRE_COARSE_PX, sx + CENTRE_COARSE_PX + 1e-9, CENTRE_STEP_PX):
            for cy in np.arange(sy - CENTRE_COARSE_PX, sy + CENTRE_COARSE_PX + 1e-9, CENTRE_STEP_PX):
                arc = evaluate(cx, cy)
                if arc is not None and arc.inliers > best.inliers:
                    best = arc
    return best


def anchor_ink_fraction(centre: tuple[float, float], page_ink: np.ndarray, dpi: float) -> float:
    """How much of a small disc at an arc's centre of curvature is ink.

    Read from the RAW ink, before line suppression: a hinge is part of a wall, and the wall
    is exactly what suppression removes.
    """
    radius = max(3, int(round(ANCHOR_RADIUS_IN * dpi)))
    cx, cy = int(round(centre[0])), int(round(centre[1]))
    y0, y1 = max(cy - radius, 0), min(cy + radius + 1, page_ink.shape[0])
    x0, x1 = max(cx - radius, 0), min(cx + radius + 1, page_ink.shape[1])
    if y1 <= y0 or x1 <= x0:
        return 0.0
    window = page_ink[y0:y1, x0:x1]
    return float(window.sum()) / max(window.size, 1)


def page_ink_from(gray: np.ndarray) -> np.ndarray:
    """The raw ink of a whole page, which is where an arc's anchor has to be looked for."""
    return (255 - gray) > INK_THRESHOLD


def is_swing(arc: Arc | None, bbox: BBox, require_anchor: bool | None = None) -> bool:
    """Does this arc read as a door's swing rather than as some other curve?

    `require_anchor` is not a policy decision made here -- it is carried in from whatever
    the person selected. Select a door and its hinge is ink, so matches must have one too
    and the chairs drop out. Select something that curves without pivoting and the test is
    not applied, so that symbol can still be counted.
    """
    if arc is None:
        return False
    reach = max(bbox[2], bbox[3])
    if not (0.65 * reach <= arc.radius_px <= 1.7 * reach):
        return False
    if require_anchor and arc.anchor_ink < ANCHOR_FRACTION:
        return False
    return True


def thin_enough(candidate: Candidate, max_fill: float = MAX_FILL) -> bool:
    """A cheap pre-filter. An arc is mostly empty bounding box; solid glyphs are not."""
    w, h = candidate.bbox_px[2], candidate.bbox_px[3]
    return candidate.area_px / max(w * h, 1) <= max_fill


def _peeled(mask: np.ndarray, bbox: BBox, arc: Arc) -> np.ndarray:
    """The blob with one fitted circle's ink removed.

    A stroke either side of the circle, matching the inlier tolerance the fit used, so what
    comes out is exactly what the refused fit was measuring and nothing more.
    """
    ys, xs = np.nonzero(mask)
    dist = np.hypot(xs + bbox[0] - arc.centre_px[0], ys + bbox[1] - arc.centre_px[1])
    kill = np.abs(dist - arc.radius_px) <= INLIER_TOL_PX + 1
    out = mask.copy()
    out[ys[kill], xs[kill]] = False
    return out


def find_swing(
    mask: np.ndarray,
    bbox: BBox,
    dpi: float,
    radius_band_in: tuple[float, float] = RADIUS_BAND_IN,
    page_ink: np.ndarray | None = None,
    require_anchor: bool | None = None,
    min_quality: float | None = None,
) -> Arc | None:
    """The best arc in a blob that reads as a swing, looking past ink that is not one.

    `find_arc` returns the circle the most ink sits on. That is the right first guess and the
    wrong final answer when something denser than the swing shares the component: the sweep
    locks onto the dense thing, `Arc.quality` correctly scores it as not-a-stroke, and the
    door is lost with no indication that a good arc was sitting underneath. Peeling the
    refused fit's ink away and sweeping again is what finds it.

    `min_quality` of None means do not peel at all, which is the single-pass behaviour and
    what a caller measuring a blob rather than counting one wants. When peeling fails the
    FIRST fit is returned, so a blob that cannot be rescued reports exactly what it reports
    without this -- the caller's own gates then refuse it as they always did.

    Deterministic: `find_arc` sweeps a fixed grid and peeling is a fixed rule over its
    output, so the same ink gives the same answer on every run (decision 10).
    """
    if min_quality is None:
        return find_arc(mask, bbox, dpi, radius_band_in, page_ink)

    working = mask
    first: Arc | None = None
    for _ in range(PEEL_ROUNDS):
        arc = find_arc(working, bbox, dpi, radius_band_in, page_ink)
        if arc is None:
            break
        if first is None:
            first = arc
        if arc.quality >= min_quality and is_swing(arc, bbox, require_anchor):
            return arc
        working = _peeled(working, bbox, arc)
        if int(working.sum()) < MIN_INLIERS:
            break
    return first


def swings_in(
    candidates: list[Candidate],
    dpi: float,
    radius_band_in: tuple[float, float] = RADIUS_BAND_IN,
    page_ink: np.ndarray | None = None,
    require_anchor: bool | None = None,
    min_quality: float | None = None,
) -> list[tuple[Candidate, Arc]]:
    """Every door swing among a list of candidates, in a stable order.

    Size-filtered before the sweep, because the sweep is the expensive part: a blob whose
    reach cannot hold an arc in the radius band can never produce one.
    """
    lo, hi = radius_band_in[0] * dpi, radius_band_in[1] * dpi
    out: list[tuple[Candidate, Arc]] = []

    for c in candidates:
        reach = max(c.bbox_px[2], c.bbox_px[3])
        if reach < 0.55 * lo or reach > 2.2 * hi:
            continue
        if not thin_enough(c):
            continue
        arc = find_swing(
            c.mask, c.bbox_px, dpi, radius_band_in, page_ink, require_anchor, min_quality
        )
        if is_swing(arc, c.bbox_px, require_anchor):
            out.append((c, arc))

    out.sort(key=lambda pair: (pair[0].bbox_px[1], pair[0].bbox_px[0]))
    return out
