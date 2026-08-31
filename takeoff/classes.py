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

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

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

    # How light this symbol's linework is, as an ink threshold on 255 - gray. None takes the
    # global default, which is measured on architectural sheets. A class drawn on a thin CAD
    # layer needs a lower one: the duplex receptacle's median pixel is 232 against a global
    # cut at 230, so at the default it arrives as nine fragments and cannot be matched at all.
    # This is per class rather than per sheet because lineweight follows the symbol's layer,
    # and because lowering it for a whole sheet costs real instances -- a global 15 merges
    # neighbouring ink and loses two doors and a marker on T5.
    ink_threshold: int | None = None

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


# ------------------------------------------------------------------ classes a person adds

# Symbols this file does not know about. The registry above is the built-in vocabulary --
# doors, markers, receptacles -- and it is a fair bet against any real drawing set: a
# mechanical sheet has diffusers and dampers, a plumbing sheet has fixtures, and none of them
# can be counted against a name until somebody supplies one.
#
# A user class is a NAME FOR A SELECTION. It carries a real anchor, which is what lets it work
# everywhere the built-ins do -- identified on other sheets, graded by the harness, offered
# when annotating -- with no special case anywhere. An anchorless class would have needed a
# guard in every consumer of the registry and would still not be countable.
USER_CLASSES_PATH = Path("classes.json")

_SLUG = re.compile(r"[^a-z0-9]+")


def slug(name: str) -> str:
    """A class id from a human name. Stable, so re-adding the same name is a clash."""
    return _SLUG.sub("_", name.strip().lower()).strip("_")


def user_class(name: str, anchor: TemplateAnchor, **kw) -> SymbolClass:
    """A class named by a person, on the generic thresholds.

    Nothing here is measured, which is the honest position: 0.90/0.80 is what an unregistered
    symbol is already counted on, so naming one changes what it is CALLED and what it can be
    graded against, and not how it scores. Re-derive both once the class has ground truth.
    """
    return SymbolClass(
        id=kw.pop("id", None) or slug(name),
        name=name.strip(),
        anchor=anchor,
        notes=kw.pop("notes", "Added from a selection in the viewer."),
        **kw,
    )


def load_user_classes(path: Path | None = None) -> list[SymbolClass]:
    """Register everything in the user file. Missing or empty is the normal case."""
    path = USER_CLASSES_PATH if path is None else path
    if not path.exists():
        return []

    added: list[SymbolClass] = []
    for row in json.loads(path.read_text(encoding="utf-8")).get("classes", []):
        a = row["anchor"]
        symbol = user_class(
            row["name"],
            TemplateAnchor(
                page_index=int(a["page_index"]),
                drag_bbox_px=tuple(int(v) for v in a["drag_bbox_px"]),
                dpi=int(a.get("dpi", 300)),
                source=a.get("source", "Skanksa.pdf"),
            ),
            id=row.get("id"),
            counted_at=float(row.get("counted_at", 0.90)),
            review_floor=float(row.get("review_floor", 0.80)),
            notes=row.get("notes", "Added from a selection in the viewer."),
        )
        if symbol.id in REGISTRY:
            continue                       # a built-in wins; the file is not authoritative
        added.append(register(symbol))
    return added


def save_user_class(symbol: SymbolClass, path: Path | None = None) -> None:
    """Append one class to the user file, creating it if need be."""
    path = USER_CLASSES_PATH if path is None else path
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"classes": []}
    data.setdefault("classes", [])
    data["classes"] = [c for c in data["classes"] if c.get("id") != symbol.id]
    data["classes"].append({
        "id": symbol.id,
        "name": symbol.name,
        "anchor": {
            "page_index": symbol.anchor.page_index,
            "drag_bbox_px": list(symbol.anchor.drag_bbox_px),
            "dpi": symbol.anchor.dpi,
            "source": symbol.anchor.source,
        },
        "counted_at": symbol.counted_at,
        "review_floor": symbol.review_floor,
        "notes": symbol.notes,
    })
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def remove_user_class(class_id: str, path: Path | None = None) -> SymbolClass:
    """Unregister a class a person added, and take it out of the file.

    Built-ins are not removable: they are the tool, and their anchors are in this module
    rather than in data. Annotations already recorded against a removed class are LEFT ALONE
    -- they are somebody's work and deleting them silently would be the worst possible reading
    of "remove the class". They keep their id, the editor still offers it as an unregistered
    label, and re-adding the same name picks them back up.
    """
    if class_id in BUILT_IN:
        raise ValueError(f"{class_id!r} ships with the tool and cannot be removed")
    if class_id not in REGISTRY:
        raise KeyError(f"no symbol class {class_id!r}")

    symbol = REGISTRY.pop(class_id)
    path = USER_CLASSES_PATH if path is None else path
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        data["classes"] = [c for c in data.get("classes", []) if c.get("id") != class_id]
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return symbol


def is_user_class(class_id: str) -> bool:
    """Whether this class came from the user file rather than from this module."""
    return class_id in REGISTRY and class_id not in BUILT_IN


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


DETAIL_MARKER = register(
    SymbolClass(
        id="detail_marker",
        name="Detail marker",
        # T9, the sheet Paing selected it from. A circle split by a bar -- detail number
        # above, sheet number below -- fused to a hatched arrowhead on a leader line. The drag
        # is generous and stops where the leader leaves the box, which is what a person would
        # draw; the leader tail is not part of the symbol and does not need to be.
        anchor=TemplateAnchor(page_index=8, drag_bbox_px=(4656, 4583, 146, 177), dpi=300),
        # Measured over T3-T10: 15 genuine markers (1 on T3, 5 on T9, 9 on T10) score
        # 0.913-1.000, and the best thing that is not one scores 0.574 -- the widest gap of
        # any class here, because the circle, the bar and the hatched wedge are three features
        # at once and nothing else on an architectural sheet carries all three. 0.85 sits
        # below every real one with 0.28 of clearance above the best false; the floor is well
        # under the lowest real instance, so a marker drawn at another scale surfaces for
        # review rather than vanishing.
        counted_at=0.85,
        review_floor=0.70,
        # The sheet half of the reference. The full reference is TWO words stacked inside the
        # circle -- `4` over `T12` -- and `layout.label_for` returns one, so this reports the
        # sheet the detail lives on rather than a bare digit that means nothing alone.
        # Composing a caption from two words is a real gap; it is reported at the gate rather
        # than fixed here, because one class wanting it is not yet evidence the core is
        # under-general. The detail number is always the NEAREST word, so a fallback to
        # proximity would report the useless half.
        label_pattern=r"^[A-Z]{1,2}\d+(\.\d+)?$",
        notes=(
            "Circle with a horizontal bar, detail number over sheet number, fused to a "
            "hatched arrowhead. Shares the hatched wedge with the interior elevation marker "
            "and is separated from it by the circle: neither scores above 0.35 against the "
            "other's template. Lives on the interior-elevation sheets T9 and T10 with one on "
            "T3; the plan sheets T4-T8 carry none, which is what their 0.561 ceiling says. "
            "The `4 / T12` inside the circle points at the detail, and is not the sheet the "
            "marker is drawn on."
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
DUPLEX_RECEPTACLE = register(
    SymbolClass(
        id="receptacle_duplex",
        name="Duplex receptacle",
        # On E4, the sheet scratch/spike.py measured. A circle with two parallel lines through
        # it; the lines run on to the wall, so the drag takes the glyph and the snap trims the
        # rest -- the template comes out 47x31 px with no context blobs.
        # Widened from 58 px to 66 px wide when the drag became a hard ceiling on the
        # selection: the glyph runs to x=2516 and the old box stopped at 2513, so it clipped
        # 3 px off its own reference and the template silently became 44x31 with 176 ink px
        # instead of 47x31 with 181. An anchor exists to define the symbol; it must contain it.
        anchor=TemplateAnchor(page_index=25, drag_bbox_px=(2455, 1057, 66, 58), dpi=300),
        # THE REASON THIS CLASS NEEDED A PIPELINE CHANGE. Electrical devices are drawn on a
        # thin CAD layer: this glyph's darkest pixel is 202 and its MEDIAN is 232, against a
        # global cut at gray < 230. At the default it arrives as nine fragments of 11-18 px,
        # `from_selection` keeps the largest, and a template that size matched 2,312 things on
        # the sheet. At 15 it is one component of 181 px and its own instance scores 1.000.
        ink_threshold=15,
        # Provisional, and the only numbers here that are NOT measured against annotations.
        # On E4: 42 instances score 1.000, 96 score >= 0.95, 99 >= 0.92, 103 >= 0.90, 121
        # >= 0.88. Two independent earlier methods put the sheet at 92 (NCC, scratch/spike.py)
        # and 90 (vector motif clustering), so the shoulder at 0.95 is where the real ones
        # end. Re-derive both from ground truth before trusting them -- that is what the gate
        # is waiting on, and until E4 is annotated this class is counted but ungraded.
        counted_at=0.95,
        review_floor=0.85,
        notes=(
            "Circle plus two parallel lines, 0.092 in across. The nested-symbol case lives "
            "here: a quad receptacle CONTAINS a duplex, and the spikes measured 0.816 against "
            "0.681 -- a 0.135 margin that is too thin to threshold. That is the case the "
            "margin gate was built for and it is still untested; registering the quad as its "
            "own class is what will exercise it."
        ),
    )
)


# Everything above this line is the built-in vocabulary. Recorded before the user file is read
# so a class a person added can always be told from one that ships with the tool -- they are
# graded the same way but only one of them can be deleted.
BUILT_IN = frozenset(REGISTRY)

load_user_classes()
