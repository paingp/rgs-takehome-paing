"""Orchestration: candidates -> score -> competitive assignment -> band -> scope.

Competitive assignment (argmax across the template library plus a margin test), not
per-template thresholds -- this is the answer to the nested-symbol problem.

The pipeline is deliberately class-agnostic. Everything specific to a symbol arrives through
`classes.REGISTRY`: which orientations to build, how close in size a blob must be before it
is worth scoring, and where the two gates sit. Adding the next symbol should touch classes.py
and nothing here -- if it does not, say so at the gate.

Raster-only module: must never import pymupdf, directly or transitively.
Enforced by tests/test_raster_only.py.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Sequence

import numpy as np

from takeoff import banding, classes, doors, regions as regions_mod, scoring, templates
from takeoff.candidates import BBox, Candidate, snap
from takeoff.schema import Raster
from takeoff.scoring import Scorer, StrokeCoverageScorer
from takeoff.templates import Template, TemplateVariant

# How far apart two pieces of one glyph may sit, as a fraction of the template's larger
# dimension. Line suppression is the thing that separates them -- it removes a drawing line
# crossing a symbol and takes a few pixels of the symbol with it -- so the gap to bridge is
# a stroke or two, not a drawing feature.
GROUP_GAP_FACTOR = 0.08

# A glyph made of more pieces than this is not a glyph. Bounds the search and, with the
# footprint bound below, is what stops proximity growing into the 11- and 17-part blobs that
# sank plain clustering in the spikes.
#
# Measured on T5: a line crossing a symbol splits it in two, two crossing lines in three, and
# nothing genuine needed more. At 6 a chain of small text beside the C\T9 marker reached 0.907
# and was counted a second time under that marker's own label. Four leaves headroom over what
# the sheet actually needs and still refuses a chain of characters.
MAX_GROUP_PARTS = 4

# Slack on the template's own part spacing, so an instance whose pieces sit a pixel further
# apart than the reference's still assembles. A stroke's width, no more.
PART_GAP_MARGIN_PX = 3.0

# How many more pieces an instance may arrive in than the reference had. Suppression, a
# threshold or a line drawn across the symbol all split a piece in two; none of them doubles
# the count of a nine-dash arc.
PART_COUNT_HEADROOM = 2.0

# A blob may be bigger than the symbol without being something else: a line drawn across a
# marker is one component with it, so the symbol arrives fused to its occluder. Both occluded
# markers on T5 are like this -- 77x153 and 116x146 px against a 44x129 marker -- and both
# were invisible to every threshold because an oversized blob never reaches scoring at all.
#
# So an oversized blob gets the symbol looked for INSIDE it. The bound is on how much foreign
# ink is worth searching through: past some multiple of the symbol's own ink this stops being
# an occluded instance and starts being a search for an accident in a wall. In MULTIPLES OF
# THE TEMPLATE'S INK, not of its bounding box -- a 200x200 patch of wall is mostly white and
# cheap to search, a 200x200 hatch fill is neither, and box area cannot tell them apart.
#
# It was 4.0 and read box area, which excluded every real case: see `fused_blobs`.
MAX_FUSED_INK = 48.0

# How much of the template's ink a blob must hold before the search is worth running, and how
# much it may hold before it is too solid to be hiding anything. A symbol fused with a line
# still carries all of its own ink, so the floor only excludes specks; the ceiling is measured
# in ink rather than box area for the reason given on `fused_blobs`.
FUSED_INK_SHARE = 0.5

# Coarse pass, then +/- one stride at single-pixel steps around the best window. Line work is
# 2-3 px wide at 300 DPI, so a coarse step of 3 cannot step over the symbol.
FUSED_STRIDE_PX = 3


@dataclass(frozen=True)
class ClassEntry:
    """One registered class with its orientation bank built."""

    symbol: classes.SymbolClass
    template: Template | None          # None once the selection turned out to be a curve
    bank: list[TemplateVariant]
    profile: SelectionProfile | None = None

    @property
    def detector(self) -> str:
        """What this entry will actually use.

        A class may name a detector, but by default the selection decides. That is the
        difference between a tool that counts the symbols someone thought of in advance and
        one that counts a symbol it has never seen: a person drags a box, the tool measures
        what is in it, and the measurement picks the method.
        """
        if self.symbol.detector != "auto":
            return self.symbol.detector
        return self.profile.detector if self.profile else "template"

    @property
    def is_arc(self) -> bool:
        return self.detector == "arc"

    @property
    def radius_band_in(self) -> tuple[float, float]:
        """Measured from the selection where there is one; the registry can still pin it."""
        if self.profile and self.profile.radius_band_in:
            return self.profile.radius_band_in
        return self.symbol.radius_band_in or doors.RADIUS_BAND_IN

    @property
    def footprints(self) -> list[tuple[int, int]]:
        """Every (w, h) the bank can present. Gating reads this, not `template.size_px`.

        Once the bank holds more than one scale, the template's own size stops being the
        thing a candidate has to match -- what matters is whether it matches SOME variant.
        """
        return sorted({v.size_px for v in self.bank})


@dataclass(frozen=True)
class Detection:
    """One counted, reviewable or rejected instance."""

    id: str
    class_id: str
    bbox_px: BBox
    centroid_px: tuple[float, float]
    match: float
    margin: float | None
    status: banding.Status
    reason: str | None
    variant_label: str
    runner_up: str | None

    # The score's two halves, kept rather than collapsed. `match` is min(forward, backward),
    # so on its own it never says WHICH way a near miss failed -- whether the candidate had
    # ink the template does not (forward low) or missed ink the template has (backward low).
    # A reviewer looking at a 0.81 needs that distinction to judge it.
    forward: float = 0.0
    backward: float = 0.0
    ink_px: int = 0
    parts: int = 1            # components merged to make this instance

    # Found by searching inside a blob too big to be the symbol, rather than by scoring a
    # component whole. Worth saying out loud: it changes what the score means (the best of
    # many windows, not one reading) and it changes what a reviewer should check -- "is there
    # really a symbol in here" rather than "is this 0.93 good enough".
    fused: bool = False

    @property
    def colour(self) -> str:
        return banding.BAND_COLOURS[self.status]

    @property
    def size_px(self) -> tuple[int, int]:
        return (self.bbox_px[2], self.bbox_px[3])

    @property
    def centre_px(self) -> tuple[float, float]:
        """The middle of the box, which is NOT `centroid_px`.

        `centroid_px` is where the ink is; this is where the instance is. For a swing arc
        they are far apart -- the ink of a quarter circle sits along the curve, up to 59 px
        from the box centre on T5's doors -- and grading compared one against the other until
        it was found: the door at (9412, 2894) came back as a false positive AND a miss, 73 px
        out on ink centroids and 19 px out on box centres, against a 66 px tolerance.
        """
        x, y, w, h = self.bbox_px
        return (x + w / 2.0, y + h / 2.0)

    @property
    def asymmetry(self) -> float:
        """How lopsided the fit is. Large means one shape contains the other."""
        return abs(self.forward - self.backward)

    def size_in(self, dpi: float) -> tuple[float, float]:
        return (self.bbox_px[2] / dpi, self.bbox_px[3] / dpi)

    def centre_in(self, dpi: float) -> tuple[float, float]:
        """Centre in inches from the sheet's top-left. Where it is, in a drafter's units."""
        return (self.centroid_px[0] / dpi, self.centroid_px[1] / dpi)


def detection_id(page_index: int, class_id: str, bbox: BBox) -> str:
    """Hashes position and class, per decision 10.

    Review state and golden counts survive a re-run because the id does not depend on the
    score, the threshold or the order candidates happened to come out of cv2.
    """
    key = f"{page_index}:{class_id}:{bbox[0]}:{bbox[1]}:{bbox[2]}:{bbox[3]}"
    return hashlib.blake2b(key.encode(), digest_size=8).hexdigest()


# Selecting a symbol is one gesture for every class. What differs is what gets MEASURED from
# the selection, and that is decided here rather than declared per symbol -- otherwise every
# new symbol would need someone to know, in advance, which detector suits it.
#
# The search band used while profiling is deliberately wide. It is measuring whatever the
# person pointed at, not checking it against a policy; the narrow band comes afterwards, from
# what was actually measured.
PROFILE_BAND_IN = (0.15, 0.80)

# How far either side of the measured radius the sweep then looks. A door schedule runs in
# 2-inch steps and the measurement is good to a few pixels, so this is generous enough to
# catch the neighbouring sizes without reaching the next symbol entirely.
RADIUS_TOLERANCE = 0.35

# An arc that SPANS what was selected is the symbol; an arc that spans half of it is a detail
# inside a bigger shape, and reading the whole selection as a curve because of it is how a
# square air device gets counted as door swings.
#
# Measured over 28 annotated doors on T4 and T5, each dragged generously the way a person
# does: radius / selection span runs 0.71 to 1.09, median ~1.0 -- a swing's arc is as big as
# the thing you drew a box around, which is what makes it the thing you were pointing at. The
# four-quadrant supply diffuser on M2 fits a clean 75-degree arc inside one of its corner
# brackets and scores 0.45. The gate sits in that gap, nearer the diffuser than the worst
# door, because losing a door is worse than misreading an air device.
#
# Ink share does NOT separate them and was tried first: the door's arc is 0.21 of its
# selection's ink and the diffuser's is 0.36, the wrong way round, because a swing is a thin
# curve beside a solid keynote bubble.
ARC_SPANS_SELECTION = 0.60

# How good an arc has to be before a selection is read as a curve rather than a shape. This
# is `Arc.quality`, which judges the arc alone -- deliberately NOT the arc's share of the
# blob it sits in. Gating on share meant the door to room 217 could not be selected as a
# door: a wall jamb shares its component, so its flawless swing is only 22% of the ink, and
# the profiler called it a shape. Two of the sheet's 29 doors were unselectable for the same
# reason the detector had been rejecting them.
MIN_ARC_QUALITY = 0.5


@dataclass(frozen=True)
class SelectionProfile:
    """What a selection turned out to be, and therefore how it will be counted."""

    detector: str                       # "arc" or "template"
    reason: str                         # shown to the person; this is not a silent choice
    arc: doors.Arc | None = None
    radius_band_in: tuple[float, float] | None = None
    anchored: bool | None = None        # did the selected instance pivot about drawn ink?


def profile_selection(
    selection, dpi: float, page_ink: np.ndarray | None = None
) -> SelectionProfile:
    """Decide how to count what a person just selected, by measuring it.

    The arc test runs over every piece of the selection, largest ink first -- not over the
    glyph `Template.from_selection` would pick. For a door those differ: the arc is thin, so
    a keynote bubble sitting inside the swing is the larger blob and would be chosen as the
    glyph while the arc, the thing actually pointed at, went unexamined.

    A symbol whose ink is dense is never read as an arc, whatever curve can be fitted through
    it: the hatched elevation marker fills 24% of its box and a circle can be run through its
    diagonals, but it is a shape, and shapes are matched as shapes.

    Nor is a symbol whose ink is spread over several pieces, when the arc only explains one of
    them. The test is per piece because a door's arc is a thin blob beside a fatter keynote
    bubble -- but the DECISION is about the whole selection, so an arc that does not span the
    selection is a detail inside a larger shape rather than the shape itself. See
    ARC_SPANS_SELECTION.
    """
    for c in sorted(selection.members, key=lambda c: -c.area_px):
        if not doors.thin_enough(c):
            continue
        # Peeling matters here too, and for the same reason it matters when counting: a
        # person dragging a box round a door whose keynote bubble sits on its swing would
        # otherwise have the selection read as a shape, and get the template detector.
        arc = doors.find_swing(
            c.mask, c.bbox_px, dpi, PROFILE_BAND_IN, page_ink, min_quality=MIN_ARC_QUALITY
        )
        if not doors.is_swing(arc, c.bbox_px):
            continue
        if arc.quality < MIN_ARC_QUALITY:
            continue
        # The arc has to be the size of what was selected, not a curve inside it.
        span = max(selection.bbox_px[2], selection.bbox_px[3])
        if span and arc.radius_px < ARC_SPANS_SELECTION * span:
            continue
        radius_in = arc.radius_px / dpi

        # Whether this thing pivots about drawn ink is read off the instance selected, not
        # decided here. A door's hinge is a jamb; an office chair's back curves about an
        # empty seat. Requiring of the matches whatever the selection had is what keeps T4's
        # chairs out of a door count without making chairs uncountable.
        anchored = None if page_ink is None else arc.anchor_ink >= doors.ANCHOR_FRACTION
        pivot = ""
        if anchored is True:
            pivot = ", pivoting on drawn ink"
        elif anchored is False:
            pivot = ", pivoting on nothing drawn"

        return SelectionProfile(
            detector="arc",
            reason=(
                f"a {arc.span_deg:.0f}-degree arc of radius {radius_in:.3f} in "
                f"({arc.width_ft(dpi):.1f} ft){pivot} -- counted by sweeping for that circle"
            ),
            arc=arc,
            radius_band_in=(
                round(radius_in * (1 - RADIUS_TOLERANCE), 4),
                round(radius_in * (1 + RADIUS_TOLERANCE), 4),
            ),
            anchored=anchored,
        )

    return SelectionProfile(
        detector="template",
        reason="a shape rather than a curve -- counted by matching it in every orientation",
    )


# What a selection is counted as when it matches nothing already registered. The tool still
# works on a symbol nobody has entered -- it just cannot name it, or bring a caption pattern
# and calibrated thresholds along. Defaults per detector, because a swept arc and a matched
# glyph do not score on the same scale.
UNKNOWN_TEMPLATE = classes.SymbolClass(
    id="selection",
    name="Selected symbol",
    anchor=classes.TemplateAnchor(page_index=0, drag_bbox_px=(0, 0, 1, 1)),
    detector="template",
    counted_at=0.90,
    review_floor=0.80,
)
UNKNOWN_ARC = classes.SymbolClass(
    id="selection",
    name="Selected symbol",
    anchor=classes.TemplateAnchor(page_index=0, drag_bbox_px=(0, 0, 1, 1)),
    detector="arc",
    counted_at=0.80,
    review_floor=0.60,
)

# How close a measured radius must be to a registered class's own to be that class.
IDENTIFY_RADIUS_TOLERANCE = 0.25


def identify(
    selection,
    raster: Raster,
    candidates: Sequence[Candidate],
    known: Sequence[classes.SymbolClass] | None = None,
    references: dict[str, "ClassEntry"] | None = None,
) -> tuple[classes.SymbolClass, str]:
    """Which registered symbol is this, if any?

    The alternative was a dropdown, and a dropdown is a second, silent input that can
    disagree with the drag. Selecting a marker while it still said "door" applied the door's
    thresholds to a triangle -- 11 counted instead of 8, 32 in review instead of 3, every
    result labelled `door_swing`. There is only one thing a person is pointing at, so there
    should only be one place that says what it is.

    `references` are entries already built from each class's own anchor page. They have to be
    supplied from outside, because a class is anchored wherever its reference instance lives
    and this module cannot render another page. Without them nothing could be recognised off
    the anchor sheet: every door on T4 came back "not a symbol registered yet", losing the
    class's name, its caption pattern, and its calibrated thresholds.

    Falling back to an unnamed class is deliberate: an unregistered symbol still counts, it
    just arrives without a name, a caption pattern, or thresholds anyone has calibrated.
    """
    profile = profile_selection(selection, raster.dpi, doors.page_ink_from(raster.gray))

    best: tuple[float, classes.SymbolClass, str] | None = None
    for symbol in known if known is not None else classes.all_classes():
        reference = (references or {}).get(symbol.id)
        if reference is None:
            try:
                reference = build_entry(symbol, raster, candidates)
            except ValueError:
                continue              # anchored elsewhere and no reference was supplied
        if reference.detector != profile.detector:
            continue

        if profile.detector == "arc":
            # Deliberately NOT matched on radius. A door is a door at any drawing scale, and
            # T4 alone carries three viewports whose doors differ threefold; the radius that
            # matters is the one measured from the selection, and that is already carried in
            # the profile. Telling two arc classes apart will need more than size when a
            # second one exists.
            score = 1.0
            measured = profile.arc.radius_px / raster.dpi
            if best is None or score > best[0]:
                best = (score, symbol,
                        f"an arc of radius {measured:.3f} in, matching {symbol.name.lower()}")
            continue

        glyph = Template.from_selection(symbol.id, selection, page_index=raster.page_index)
        match = scoring.best_variant(
            glyph.mask, reference.bank, raster.dpi, StrokeCoverageScorer()
        )
        if match.match >= symbol.counted_at:
            if best is None or match.match > best[0]:
                best = (match.match, symbol,
                        f"a {match.match:.2f} match for {symbol.name.lower()}")

    if best is not None:
        return best[1], best[2]

    fallback = UNKNOWN_ARC if profile.detector == "arc" else UNKNOWN_TEMPLATE
    return fallback, "not a symbol registered yet, so it is counted without a name"


def build_entry(
    symbol: classes.SymbolClass, raster: Raster, candidates: Sequence[Candidate]
) -> ClassEntry:
    """Rebuild one class's entry from its anchor, through the same snap a person drives.

    The anchor is a drag box, so this is the browser's path with the drag supplied from the
    registry instead of from a pointer. There is deliberately no second way to build an
    entry: if the two could differ, the tests would stop describing what a person gets.
    """
    if raster.page_index != symbol.anchor.page_index:
        raise ValueError(
            f"{symbol.id!r} anchors on page index {symbol.anchor.page_index}, "
            f"got a raster for {raster.page_index}"
        )
    selection = snap(candidates, symbol.anchor.drag_bbox_px, dpi=raster.dpi)
    return entry_from_selection(
        symbol.id, selection, page_index=raster.page_index, symbol=symbol,
        page_ink=doors.page_ink_from(raster.gray),
    )


def entry_from_selection(
    class_id: str,
    selection,
    page_index: int,
    symbol: classes.SymbolClass | None = None,
    page_ink: np.ndarray | None = None,
) -> ClassEntry:
    """Turn what a person selected into something that can count the rest of the sheet.

    One gesture for every symbol. What differs is what gets measured from it: a curve is
    measured for its radius and counted by sweeping for that circle, a shape is kept as a
    glyph and counted by matching it in every orientation. Nothing about which is which is
    declared per symbol unless a class insists.
    """
    symbol = symbol or classes.get(class_id)
    profile = (
        profile_selection(selection, selection.dpi, page_ink)
        if symbol.detector in ("auto", "arc")
        else SelectionProfile(detector="template", reason="the class pins this detector")
    )

    if symbol.detector == "arc" or (symbol.detector == "auto" and profile.detector == "arc"):
        return ClassEntry(symbol=symbol, template=None, bank=[], profile=profile)

    template = Template.from_selection(class_id, selection, page_index=page_index)
    return ClassEntry(
        symbol=symbol,
        template=template,
        bank=templates.variants(template, symbol.rotations, symbol.mirrors, symbol.scales),
        profile=profile,
    )


def _passes_size_gate_bbox(bbox: BBox, entry: ClassEntry) -> bool:
    """Within tolerance of some variant's footprint -- any rotation, any scale."""
    tolerance = entry.symbol.size_tolerance
    w, h = bbox[2], bbox[3]
    for a, b in entry.footprints:
        if a > 0 and b > 0 and abs(w - a) / a <= tolerance and abs(h - b) / b <= tolerance:
            return True
    return False


def _passes_size_gate(candidate: Candidate, entry: ClassEntry) -> bool:
    """Within tolerance of the template's footprint in some orientation.

    Checked against both the upright and the quarter-turned footprint rather than against
    every variant, because the variants of a rotation bank only ever have those two shapes
    and the check runs on all several thousand candidates.
    """
    return _passes_size_gate_bbox(candidate.bbox_px, entry)


def _fits_footprint(bbox: BBox, entry: ClassEntry) -> bool:
    """Could this box still be one instance -- is it small enough for SOME variant?

    An upper bound only. Growing a group stops the moment the answer is no, which is what
    keeps proximity from chaining a marker into the note beside it.
    """
    tolerance = entry.symbol.size_tolerance
    w, h = bbox[2], bbox[3]
    return any(
        w <= a * (1 + tolerance) and h <= b * (1 + tolerance) for a, b in entry.footprints
    )


def _union_bbox(a: BBox, b: BBox) -> BBox:
    x0, y0 = min(a[0], b[0]), min(a[1], b[1])
    x1 = max(a[0] + a[2], b[0] + b[2])
    y1 = max(a[1] + a[3], b[1] + b[3])
    return (x0, y0, x1 - x0, y1 - y0)


def _group_mask(members: Sequence[Candidate]) -> tuple[np.ndarray, BBox]:
    """The composite ink of several components, over their shared bounding box."""
    bbox = members[0].bbox_px
    for c in members[1:]:
        bbox = _union_bbox(bbox, c.bbox_px)
    mask = np.zeros((bbox[3], bbox[2]), bool)
    for c in members:
        x, y, w, h = c.bbox_px
        mask[y - bbox[1] : y - bbox[1] + h, x - bbox[0] : x - bbox[0] + w] |= c.mask
    return mask, bbox


def candidate_groups(
    candidates: Sequence[Candidate], entry: ClassEntry
) -> list[tuple[Candidate, ...]]:
    """Every set of nearby components that could together be one instance.

    A symbol is not always one connected blob. Sometimes it never was -- a door's arc and
    leaf are drawn apart -- and sometimes line suppression made it so: the A/T10 marker on
    T5 is a single component in the raw ink, and becomes two once the drawing's centre line
    is removed from across its apex and the dilation takes the apex junction with it.

    Growth is greedy and bounded twice over, because unbounded proximity chaining is what
    sank clustering in the spikes. A piece joins only if it is within a stroke of the group,
    and only if the group still fits inside the template's footprint afterwards. Single
    components are groups of one, so the simple case is unchanged.

    The bounds come from the TEMPLATE where it has something to say. A symbol drawn as
    separate parts knows how many it has and how far apart they sit, and a global constant
    measured on single-blob symbols cannot: the demolition door in T3's legend is a dashed arc
    of nine pieces, which a cap of four can never assemble however close they are. A
    single-part template keeps today's numbers exactly, so nothing already measured moves.

    What this still will not do is assemble a symbol its occluder FUSED with rather than
    broke -- that is `fused_windows` -- and it stays blind to the template's SHAPE, because it
    runs for every component on the sheet and scoring is what judges the result.
    """
    if not candidates:
        return []

    # Reach far enough to bridge this symbol's own pieces, never less than the default.
    template = entry.template
    gap = max(3.0, GROUP_GAP_FACTOR * max(max(f) for f in entry.footprints))
    limit = MAX_GROUP_PARTS
    if template is not None and len(template.parts) > 1:
        gap = max(gap, template.part_gap_px + PART_GAP_MARGIN_PX)
        # Headroom over what the reference had: an instance can arrive in more pieces than
        # the one that was dragged -- a dash lost to a threshold, a wall crossing one arm.
        limit = max(limit, int(round(PART_COUNT_HEADROOM * len(template.parts))))
    boxes = np.array([c.bbox_px for c in candidates], float)
    x0, y0 = boxes[:, 0], boxes[:, 1]
    x1, y1 = x0 + boxes[:, 2], y0 + boxes[:, 3]

    # Only pieces small enough to be part of an instance can seed or join one.
    eligible = {i for i, c in enumerate(candidates) if _fits_footprint(c.bbox_px, entry)}

    groups: list[tuple[Candidate, ...]] = []
    seen: set[tuple[int, ...]] = set()

    def emit(members: list[int]) -> None:
        key = tuple(sorted(members))
        if key not in seen:
            seen.add(key)
            groups.append(tuple(candidates[i] for i in key))

    for seed in sorted(eligible):
        members = [seed]
        bbox = candidates[seed].bbox_px
        emit(members)

        # Every step of the growth is a group in its own right. Scoring only the maximal one
        # is wrong: a group is allowed to grow to the largest footprint in the bank, so with
        # more than one scale registered the maximal group swallows its neighbours and the
        # correct, smaller reading is never offered. Emitting each step lets them compete.
        while len(members) < limit:
            near = (
                (x0 <= bbox[0] + bbox[2] + gap)
                & (x1 >= bbox[0] - gap)
                & (y0 <= bbox[1] + bbox[3] + gap)
                & (y1 >= bbox[1] - gap)
            )
            best_index, best_bbox, best_growth = None, None, None
            for j in np.flatnonzero(near):
                j = int(j)
                if j in members or j not in eligible:
                    continue
                grown = _union_bbox(bbox, candidates[j].bbox_px)
                if not _fits_footprint(grown, entry):
                    continue
                growth = grown[2] * grown[3] - bbox[2] * bbox[3]
                if best_growth is None or growth < best_growth:
                    best_index, best_bbox, best_growth = j, grown, growth
            if best_index is None:
                break
            members.append(best_index)
            bbox = best_bbox
            emit(members)

    return groups


def _window_ink(host: Candidate, box: BBox) -> tuple[int, tuple[float, float]]:
    """The ink inside one window of a host blob, and where it sits."""
    x, y, w, h = box
    ox, oy = host.bbox_px[0], host.bbox_px[1]
    window = host.mask[y - oy:y - oy + h, x - ox:x - ox + w]
    ys, xs = np.nonzero(window)
    if not len(xs):
        return 0, (x + w / 2.0, y + h / 2.0)
    return len(xs), (x + float(xs.mean()), y + float(ys.mean()))


def _boxes_overlap(a: BBox, b: BBox) -> bool:
    return not (a[0] + a[2] <= b[0] or b[0] + b[2] <= a[0]
                or a[1] + a[3] <= b[1] or b[1] + b[3] <= a[1])


def _overlaps_claim(box: BBox, taken: Sequence[BBox]) -> bool:
    return any(_boxes_overlap(box, t) for t in taken)


def fused_windows(
    candidate: Candidate,
    entry: ClassEntry,
    dpi: int,
    scorer: "Scorer",
    floor: float | None = None,
) -> list[tuple[scoring.Score, BBox]]:
    """Look for the symbol INSIDE a blob too big to be the symbol.

    Occlusion on these sheets is usually not a symbol broken into pieces. It is a symbol
    welded to whatever crosses it: line suppression removes long horizontal and vertical runs
    only, so a leader drawn at 50 degrees through a marker stays, joins its component, and the
    result is one blob 116x146 px where the marker is 44x129. Nothing downstream can see it --
    the size gate refuses the blob, so it is never scored, and there is no threshold anywhere
    that could have recovered it. Both occluded markers on T5 failed exactly this way.

    Sliding the template's own footprint across the blob asks the right question of it: is
    there a window in here that IS the symbol? Measured on T5: the blob at (6518, 2506) scores
    0.504 whole and 0.960 at its best window; (9189, 2291) scores 0.711 whole and 0.830.

    This is the expensive move in the pipeline, so it runs on oversized blobs alone -- a few
    hundred on a sheet, against several thousand components.

    RETURNS EVERY WINDOW IT FOUND, not just the best one. A host blob is a run of wall or
    casework with symbols drawn on it, and it holds as many instances as it holds: 6 of the 19
    host blobs on E4 hide two receptacles each, and returning one window capped recovery at 19
    of 25 before anything else could go wrong. Windows are returned best-first and never
    overlap each other, so the same ink is not reported twice.
    """
    if not entry.bank:
        return []
    floor = entry.symbol.review_floor if floor is None else floor
    height, width = candidate.mask.shape
    ox, oy = candidate.bbox_px[0], candidate.bbox_px[1]

    def score_at(fw: int, fh: int, dx: int, dy: int) -> scoring.Score | None:
        window = candidate.mask[dy:dy + fh, dx:dx + fw]
        if not window.any():
            return None
        return scoring.best_variant(window, entry.bank, dpi, scorer)

    # Coarse pass. Everything that could be an instance is kept, not just the best -- which
    # one is "best" says nothing about whether the second one is real.
    hits: list[tuple[scoring.Score, BBox]] = []
    top: tuple[scoring.Score, BBox] | None = None
    for fw, fh in entry.footprints:
        if fw > width or fh > height:
            continue
        for dy in range(0, height - fh + 1, FUSED_STRIDE_PX):
            for dx in range(0, width - fw + 1, FUSED_STRIDE_PX):
                found = score_at(fw, fh, dx, dy)
                if found is None:
                    continue
                if found.match >= floor:
                    hits.append((found, (dx, dy, fw, fh)))
                if top is None or found.match > top[0].match:
                    top = (found, (dx, dy, fw, fh))

    # If nothing cleared the floor the blob still reports its best window. That row is
    # rejected by banding, but it is not useless: `margin` is the best score ANOTHER class got
    # for the same ink, so a sub-floor reading of a blob is what tells a door that a marker
    # also looked at it. Dropping these took the margin gate dark -- no detection on T5 had a
    # runner-up -- after the fused search had been the first thing ever to make it fire.
    if not hits and top is not None:
        hits = [top]

    # Best first, and each winner blocks the ink it sits on. Every accepted window is then
    # refined at single-pixel steps: the window IS the box that gets reported and graded, so
    # a 3 px offset is worth removing.
    hits.sort(key=lambda hit: -hit[0].match)
    taken: list[BBox] = []
    out: list[tuple[scoring.Score, BBox]] = []
    for found, (dx, dy, fw, fh) in hits:
        if _overlaps_claim((dx, dy, fw, fh), taken):
            continue
        best = (found, (dx, dy, fw, fh))
        for ry in range(max(0, dy - FUSED_STRIDE_PX),
                        min(height - fh, dy + FUSED_STRIDE_PX) + 1):
            for rx in range(max(0, dx - FUSED_STRIDE_PX),
                            min(width - fw, dx + FUSED_STRIDE_PX) + 1):
                closer = score_at(fw, fh, rx, ry)
                if closer is not None and closer.match > best[0].match:
                    best = (closer, (rx, ry, fw, fh))
        window, box = best
        if _overlaps_claim(box, taken):
            continue
        taken.append(box)
        out.append((window, (ox + box[0], oy + box[1], box[2], box[3])))

    return out


def fused_blobs(candidates: Sequence[Candidate], entry: ClassEntry) -> list[Candidate]:
    """Blobs big enough to hide the symbol, small enough to be worth searching.

    THE CAP USED TO BE 4x THE FOOTPRINT AND THAT EXCLUDED EVERY REAL CASE. Measured against
    E4's 36 missed duplex receptacles: 25 of them are joined to surrounding geometry, and
    their host blobs run 5x to 49x the footprint -- so the search written to find exactly this
    never looked at a single one of them. It had recovered one occluded marker on T5, whose
    host happened to be small, and that lone success hid the gate.

    Raising it is cheap and saturates: on E4 the pool goes 366 blobs at 4x to 460 at 32x, and
    32x and 64x admit the same 460 because nothing bigger passes the other two tests. The
    window scoring roughly triples, ~662k to ~1.78M, which is the whole cost of the change.

    The area test reads INK, not the bounding box. A 200x200 blob of wall with a receptacle
    on it is mostly white and cheap to search; one that is solid is a hatch fill or a filled
    detail, and no symbol is hiding in it. Judging by box area confuses the two and spends the
    budget on the wrong one.
    """
    if entry.template is None or not entry.bank:
        return []
    template_ink = max(int(v.mask.sum()) for v in entry.bank)
    return [
        c for c in candidates
        if c.area_px >= FUSED_INK_SHARE * template_ink
        and not _fits_footprint(c.bbox_px, entry)
        and c.area_px <= MAX_FUSED_INK * template_ink
    ]


def detect(
    raster: Raster,
    candidates: Sequence[Candidate],
    entries: Sequence[ClassEntry],
    scorer: Scorer | None = None,
    keep_rejected: bool = False,
    regions: Sequence["regions_mod.Region"] | None = None,
    hosts: Sequence[Candidate] = (),
) -> list[Detection]:
    """Score every candidate against every class and assign it to at most one.

    Competitive: a candidate gets the class whose best orientation scores highest, and the
    margin is the distance to the *next class*, not to the next orientation of the same one.
    Orientations of one template are near-duplicates of each other, so a margin measured
    across them would be ~0 for every hit and would reject the entire sheet.

    `regions` narrows the pool to ink that is not set type. A sheet carries general notes, a
    legend and a title block, and none of them can hold an instance of anything -- on T4 that
    is 47% of the candidates, grouped and size-gated and swept for nothing. Passing None
    counts the whole sheet, which is what a caller with no segmentation should get.

    The filter is here rather than in `find_candidates` on purpose: SELECTION still sees the
    whole sheet, so a legend entry can be dragged and a template built from it. What this
    narrows is only what gets counted.

    `hosts` are components too big to be a symbol -- `candidates.host_blobs` -- searched for
    instances welded to them and never scored whole. They are a separate argument because
    they must not reach grouping or the size gate: a wall network is not an instance and is
    not part of one. Passing none disables fused recovery on oversized blobs, which is what a
    caller doing a quick pass should get.
    """
    scorer = scorer or StrokeCoverageScorer()
    if regions is not None:
        candidates = regions_mod.countable(list(regions), candidates)
        hosts = regions_mod.countable(list(regions), list(hosts))

    # Score every plausible grouping, then let them compete for the ink they claim. Without
    # that, the A/T10 marker -- two components after suppression -- is counted three times:
    # once for each half and once for the pair.
    scored: list[
        tuple[float, ClassEntry, scoring.Score, tuple[Candidate, ...], BBox, bool]
    ] = []
    for entry in entries:
        if entry.is_arc:
            # The parametric path. An arc class is scored per component rather than per
            # group: a swing is one curve, and grouping it with the wall or the keynote
            # bubble beside it would only dilute the fit the sweep already found.
            band = entry.radius_band_in
            page_ink = doors.page_ink_from(raster.gray)
            anchored = entry.profile.anchored if entry.profile else None
            for candidate, arc in doors.swings_in(
                list(candidates), raster.dpi, band, page_ink, anchored,
                min_quality=entry.symbol.counted_at,
            ):
                score = scoring.Score(
                    match=arc.quality,
                    forward=arc.occupancy,
                    backward=arc.share,
                    variant_label=f"{entry.symbol.id}@r{arc.width_ft(raster.dpi):.1f}ft",
                )
                scored.append(
                    (arc.quality, entry, score, (candidate,), candidate.bbox_px, False))
            continue

        for members in candidate_groups(candidates, entry):
            mask, bbox = _group_mask(members)
            if not _passes_size_gate_bbox(bbox, entry):
                continue
            best = scoring.best_variant(mask, entry.bank, raster.dpi, scorer)
            if best.match > 0:
                scored.append((best.match, entry, best, members, bbox, False))

        # Then the instances fused to whatever crosses them. The loop above cannot see these
        # at all -- the blob is oversized, so it never passes the size gate and is never
        # scored -- and they are most of what occlusion means on these sheets.
        for blob in fused_blobs([*candidates, *hosts], entry):
            for window, box in fused_windows(blob, entry, raster.dpi, scorer):
                if window.match > 0:
                    scored.append((window.match, entry, window, (blob,), box, True))

    scored.sort(key=lambda row: (-row[0], len(row[3])))

    out: list[Detection] = []
    claimed: set[str] = set()
    claimed_boxes: list[BBox] = []
    for match, entry, best_score, members, bbox, fused in scored:
        # WHICH INK THIS SITS ON, and WHAT IT CLAIMS, are two different questions for a
        # fused window. It sits on the host blob -- that is what it competes with other
        # classes for -- but it claims only the box it occupies, because one host can hide
        # more than one instance (6 of the 19 on E4 hide two) and claiming the whole blob for
        # the first window would hide the rest.
        ink_ids = {c.id for c in members}
        claim_ids = set() if fused else ink_ids
        if claim_ids & claimed or (fused and _overlaps_claim(bbox, claimed_boxes)):
            continue

        # The runner-up is the best score a DIFFERENT class got for the same ink.
        def contests(row) -> bool:
            """Is another class claiming the same ink as this detection?"""
            if row[1].symbol.id == entry.symbol.id:
                return False
            if not {c.id for c in row[3]} & ink_ids:
                return False
            # Two windows inside one host blob only contest each other where they overlap;
            # at opposite ends of a wall run they are looking at different symbols.
            if fused and row[5]:
                return _boxes_overlap(row[4], bbox)
            return True

        rival = next((row for row in scored if contests(row)), None)
        margin = None if rival is None else match - rival[0]

        # A fused instance can never be counted outright -- see `banding.band`.
        placed = banding.band(
            match, margin, entry.symbol,
            ceiling=banding.Status.REVIEW if fused else None,
        )
        if placed.status is banding.Status.REJECTED and not keep_rejected:
            # Rejected ink stays unclaimed: something else may legitimately explain it.
            continue
        claimed |= claim_ids

        if fused:
            claimed_boxes.append(bbox)
            # The host blob is a run of wall with the symbol on it; reporting its ink and its
            # centroid would describe the wall. Read them from the window instead, which is
            # the part being claimed.
            ink, (cx, cy) = _window_ink(members[0], bbox)
        else:
            ink = int(sum(c.area_px for c in members))
            cx = sum(c.centroid_px[0] * c.area_px for c in members) / max(ink, 1)
            cy = sum(c.centroid_px[1] * c.area_px for c in members) / max(ink, 1)

        out.append(
            Detection(
                id=detection_id(raster.page_index, entry.symbol.id, bbox),
                class_id=entry.symbol.id,
                bbox_px=bbox,
                centroid_px=(cx, cy),
                match=round(match, 4),
                margin=None if margin is None else round(margin, 4),
                status=placed.status,
                reason=placed.reason,
                variant_label=best_score.variant_label,
                runner_up=None if rival is None else rival[1].symbol.id,
                forward=round(best_score.forward, 4),
                backward=round(best_score.backward, 4),
                ink_px=ink,
                parts=len(members),
                fused=fused,
            )
        )

    out.sort(key=lambda d: -d.match)
    return out


def diagnose(
    raster: Raster,
    candidates: Sequence[Candidate],
    entry: ClassEntry,
    detections: Sequence[Detection],
    scorer: Scorer | None = None,
) -> dict:
    """Why a run found what it found -- and, when it found nothing, which gate stopped it.

    A count of zero has three quite different causes and they need different fixes: nothing
    was the right size, things were the right size but looked wrong, or the template itself
    is unmatchable. Reporting a bare 0 sends a person hunting for a symbol that is there.
    """
    scorer = scorer or StrokeCoverageScorer()
    if entry.is_arc:
        band = entry.radius_band_in
        thin = [c for c in candidates if doors.thin_enough(c)]
        note = None
        if not detections:
            note = (
                f"{len(thin)} thin curves were swept for a circle of "
                f"{band[0]:.2f}-{band[1]:.2f} in radius and none held one."
            )
        return {"size_gate_admitted": len(thin), "best_match": 0.0, "note": note}

    admitted = [c for c in candidates if _passes_size_gate(c, entry)]
    best = 0.0
    for candidate in admitted:
        best = max(best, scoring.best_variant(candidate.mask, entry.bank, raster.dpi, scorer).match)

    tw, th = entry.template.size_px
    note = None
    if not detections:
        if not admitted:
            note = (
                f"No blob on this sheet is within {entry.symbol.size_tolerance:.0%} of the "
                f"template's {tw}x{th} px footprint."
            )
        else:
            note = (
                f"{len(admitted)} blobs were the right size but the best scored "
                f"{best:.3f}, under the {entry.symbol.review_floor:.2f} floor."
            )
        if entry.template.trimmed:
            note += (
                f" The selection also held {entry.template.context_blobs} separate piece(s) "
                f"({entry.template.context_ink_px} ink px) that matching ignores."
            )

    return {
        "size_gate_admitted": len(admitted),
        "best_match": round(best, 4),
        "note": note,
    }


def summarise(detections: Sequence[Detection]) -> dict:
    """Counts by band and by class, for the panel and for the eval report."""
    per_class: dict[str, dict[str, int]] = {}
    for d in detections:
        bucket = per_class.setdefault(d.class_id, {s.value: 0 for s in banding.Status})
        bucket[d.status.value] += 1
    return {
        "total": len(detections),
        "by_band": banding.tally([banding.Band(d.status, d.reason) for d in detections]),
        "by_class": per_class,
    }
