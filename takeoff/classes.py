"""SYMBOL CLASS REGISTRY.

Adding a symbol must be an entry in this file, not a pipeline change. If a new symbol
needs code elsewhere, the core is under-general -- report that at the gate.

An entry carries the class's identity, where its reference glyph lives, and the two
thresholds that band it. Nothing here knows how scoring works.

The anchor is a *drag* box, not a tight bounding box: it is fed through `candidates.snap`
exactly as a browser selection is, so the template the registry rebuilds is the template a
person would get by dragging around the same glyph. That keeps one extraction path instead
of two, and it means a change to snapping shows up in the detector's tests immediately.

Raster-only module: must never import pymupdf, directly or transitively.
Enforced by tests/test_raster_only.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from takeoff.candidates import BBox


@dataclass(frozen=True)
class TemplateAnchor:
    """Where a class's reference glyph lives, as a drag a person could have made.

    `source` names the document the reference instance lives in, and it is deliberately NOT
    whatever the person is looking at. A registered symbol has to stay recognisable when the
    tool is pointed at another drawing -- a different PDF, or a scan -- and it can only do
    that by comparing against a reference it can still reach. Without this, every symbol on
    an uploaded document comes back "not registered yet" and silently loses the class's name,
    caption pattern and calibrated thresholds.
    """

    page_index: int
    drag_bbox_px: BBox
    dpi: int = 300
    source: str = "Skanksa.pdf"


@dataclass(frozen=True)
class SymbolClass:
    """One countable symbol.

    Thresholds are per class with a global default behind them, per decision 11. They are
    seeded by eye from the score gap on the sheet the class was drawn from and are meant to
    be re-derived by the eval harness once ground truth exists -- not hand-tuned per sheet.
    """

    id: str
    name: str
    anchor: TemplateAnchor

    # "auto" measures the selection and picks: a thin curve that holds a circle is swept for
    # that circle, anything else is kept as a glyph and matched. Naming "template" or "arc"
    # pins it, which is for a symbol whose reading is known and should not drift -- not the
    # normal case. Leaving this alone is what lets an unseen symbol work without an edit.
    detector: str = "auto"

    # Arc detector only: the swept radius band, in inches on paper. A door's arc radius IS
    # its width, so this is a size policy expressed the way the symbol actually varies.
    radius_band_in: tuple[float, float] | None = None

    # How wide a gap to close when repairing what line suppression severed. None takes the
    # global default. Repair helps a matched GLYPH -- it puts a symbol back together after a
    # wall was drawn through it -- and hurts a swept ARC, because the ink it restores is the
    # jamb beside the swing, which thickens the curve and can merge it into the wall. On T5
    # that cost one real door. A class that is found by sweeping should turn it off.
    repair_gap_px: int | None = None

    # A candidate must be within this fraction of the template's size, in some orientation,
    # before it is worth scoring. Cheap, and it keeps the scored pool in the dozens.
    size_tolerance: float = 0.30

    # Which orientations to build. Four exact quarter turns suit anything drafted on axis;
    # a class that appears at arbitrary angles asks for a finer sweep and pays for resampling.
    rotations: tuple[float, ...] = (0.0, 90.0, 180.0, 270.0)
    mirrors: tuple[bool, ...] = (False, True)

    # Scale belongs here rather than in `size_tolerance`, because the tolerance also bounds
    # how far a group may grow before it is no longer one instance. Widening it to admit a
    # bigger marker let groups swallow their neighbours: on T5 that took the count from 8
    # down to 4. Extra scales in the bank admit a bigger instance and keep the bound tight.
    scales: tuple[float, ...] = (1.0,)

    # Two gates. `counted` needs both; above `review_floor` but failing either lands in review.
    counted_at: float = 0.90
    review_floor: float = 0.80
    margin_at: float = 0.10

    # What this symbol's caption looks like, if it has one. Proximity alone picks the wrong
    # word often enough to matter -- a dimension string can sit closer to a marker than the
    # marker's own reference -- and what counts as a caption is per-symbol knowledge, so it
    # belongs in the registry rather than in the text-layer code.
    label_pattern: str | None = None

    notes: str = ""


REGISTRY: dict[str, SymbolClass] = {}


def register(symbol: SymbolClass) -> SymbolClass:
    if symbol.id in REGISTRY:
        raise ValueError(f"symbol class {symbol.id!r} is already registered")
    REGISTRY[symbol.id] = symbol
    return symbol


def get(class_id: str) -> SymbolClass:
    if class_id not in REGISTRY:
        raise KeyError(f"no symbol class {class_id!r}; registered: {sorted(REGISTRY)}")
    return REGISTRY[class_id]


def all_classes() -> list[SymbolClass]:
    return [REGISTRY[k] for k in sorted(REGISTRY)]


# --------------------------------------------------------------------------- the registry

ELEVATION_MARKER = register(
    SymbolClass(
        id="elev_marker",
        name="Interior elevation marker",
        # The marker beside the existing elevator on T5. A generous drag around it, so the
        # anchor exercises snapping rather than assuming a pre-trimmed box.
        anchor=TemplateAnchor(page_index=4, drag_bbox_px=(6470, 2870, 62, 148), dpi=300),
        # Measured on T5: seven true markers score 0.988-1.000 and the best false positive,
        # a letter `A`, scores 0.808. 0.90 sits in the middle of that gap; the review floor
        # is set just under the `A` so a near miss surfaces rather than vanishing.
        counted_at=0.90,
        review_floor=0.80,
        # `C\T9`, `B/T10`, `A/T12` -- a detail letter, a separator drawn either way, and a
        # sheet number. Distinct per instance, which is what makes it worth reporting.
        label_pattern=r"^[A-Z][/\\][A-Z]?\d+$",
        notes=(
            "Hatched triangle with a sheet/detail reference beside it. Appears at all four "
            "quarter turns on T5. The hatching is what separates it from a plain arrowhead, "
            "so scoring must be symmetric -- a bare outline would otherwise match perfectly."
        ),
    )
)


SWING_DOOR = register(
    SymbolClass(
        id="door_swing",
        name="Single swing door",
        # No template, so the anchor only records where a reference instance lives. Kept so
        # a person can still be shown "this is what I am counting" and so tests have a
        # fixed example; nothing is extracted from it.
        anchor=TemplateAnchor(page_index=4, drag_bbox_px=(6395, 2915, 108, 112), dpi=300),
        # No radius band pinned: it is measured from whatever door was selected, so a set
        # drawn at another scale needs no edit here. Setting it would override that.
        #
        # Repair off: an arc is a thin curve, and restoring the jamb ink beside it thickens
        # the stroke the sweep measures. Measured on T5, repair cost one real door and gained
        # nothing for this class.
        repair_gap_px=0,
        # Measured on T5: 29 genuine swings score 0.78-1.00 and the best non-door -- an
        # appliance box whose outline holds a broken arc -- scores 0.64. 0.72 sits in that
        # gap. The lowest real door is the one to room 217, held down to 0.78 because a wall
        # jamb shares its component and thickens the ink counted along its arc.
        counted_at=0.72,
        review_floor=0.50,
        # The door legend on T3 defines the vocabulary: EX. / EX/PA / WS/PA / WB/BF / WD/DL,
        # plus RE for reused hardware. Restricted to those prefixes on purpose -- a bare
        # two-letter/two-letter pattern also matches GS/GC, a FINISH keynote, and on T5 two
        # of those sit inside a door's swing and were being reported as its type.
        #
        # Most doors carry no code at all: 11 keynote bubbles against 27 doors on T5, and
        # none on T6-T8. This reports a code where the drawing gives one and None where it
        # does not, rather than pretending every door has a type.
        label_pattern=r"^(EX\.?|(EX|RE|W[SBD])[/\\][A-Z]{2})$",
        notes=(
            "Counted by sweeping for the swing's circle, not by matching a glyph. Ink per "
            "door varies 11.4x once line suppression has run, which no template survives; "
            "the arc's radius does not vary at all. Lifecycle (existing/new/demo) and the "
            "door type code are separate axes reported per detection, not separate classes."
        ),
    )
)
