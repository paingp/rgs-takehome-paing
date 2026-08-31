"""Template extraction, rotation/mirror bank, legend harvesting.

One Template per class, expanded to a bank of TemplateVariant (rotation x mirror).

A template is the ink a person enclosed, nothing more: `Template.from_selection` takes the
`Selection` that `candidates.snap` produced and keeps its composite mask. The tool is the
annotation tool, so a template never requires typing coordinates -- the anchors in
`classes.py` exist only so tests can rebuild the same template without a browser.

Rotations that are multiples of 90 degrees go through `np.rot90`, which is exact. Anything
else resamples, and a resampled binary mask of a 43 x 128 glyph loses hatching lines to
interpolation -- so the bank records which variants are exact and a class that only ever
appears axis-aligned asks for the four exact ones.

Raster-only module: must never import pymupdf, directly or transitively.
Enforced by tests/test_raster_only.py.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from takeoff.candidates import BBox, Selection

# Distance transforms saturate: past this many pixels from the ink, "how far" stops carrying
# information and the value is clipped so the arrays stay small and comparable across sizes.
DIST_CLIP_PX = 24.0

# How close a piece must sit to the glyph to be part of it, as a fraction of the glyph's
# larger dimension. Shared raw ink alone is not enough: the long structure lines that
# suppression removes are themselves ink, so a glyph and something 26 px away can share a
# raw component purely because one line ran through both. Proximity settles that; raw
# connectivity settles the label, which sits close but was never joined.
JOIN_GAP_FACTOR = 0.12


@dataclass(frozen=True)
class Template:
    """One class's reference ink, tight to its bounding box.

    THE DRAG IS THE BOUNDARY. The mask is every piece of ink the selection enclosed, because
    a symbol is not always one connected thing and a drawing decides that, not us: a supply
    diffuser is a square drawn as four separate corner brackets around an X, and a door marked
    for demolition is a DASHED arc -- nine pieces, none touching, spanning 37x in size from a
    594 px leaf to 16 px dashes.

    This used to keep only the largest piece plus whatever the drawing had joined to it before
    line suppression, which discarded 64% of the diffuser's ink and 63% of the demo door's,
    and then counted one instance: itself. Three rules for telling a symbol's parts from an
    annotation beside it were measured and all three failed -- joined ink misses both cases,
    size comparability breaks on the demo door's 37x spread, and text-run grouping splits the
    diffuser into two "runs" and keeps none of it. There is no reliable way to guess, so the
    box a person drew decides, and the viewer shows what was taken.

    `parts` is what the selection held, in template coordinates. Matching needs it: a symbol
    whose pieces sit 30 px apart cannot be assembled by a grouping reach derived from a global
    constant, but it can be assembled by the spacing this template actually has.
    """

    class_id: str
    mask: np.ndarray          # bool, (H, W) -- every piece the selection enclosed
    dpi: int
    source_page_index: int
    source_bbox_px: BBox      # where `mask` sits, not where the selection sat
    parts: tuple[BBox, ...] = ()   # the pieces, relative to source_bbox_px
    context_blobs: int = 0    # kept at 0: nothing inside the drag is context any more
    context_ink_px: int = 0

    @property
    def trimmed(self) -> bool:
        """Whether anything inside the drag was left out of the template. Now always False."""
        return self.context_blobs > 0

    @property
    def part_gap_px(self) -> float:
        """How far apart this symbol's pieces sit -- the reach needed to assemble one.

        The largest nearest-neighbour distance over the parts, so a template made of pieces
        30 px apart says so, and grouping does not have to guess it from a global factor that
        was measured on symbols made of one blob.
        """
        if len(self.parts) < 2:
            return 0.0
        worst = 0.0
        for i, a in enumerate(self.parts):
            nearest = min(
                _box_gap(a, b) for j, b in enumerate(self.parts) if j != i
            )
            worst = max(worst, nearest)
        return worst

    @property
    def size_px(self) -> tuple[int, int]:
        return (self.mask.shape[1], self.mask.shape[0])

    @property
    def size_in(self) -> tuple[float, float]:
        return (self.mask.shape[1] / self.dpi, self.mask.shape[0] / self.dpi)

    @property
    def ink_px(self) -> int:
        return int(self.mask.sum())

    @property
    def fill(self) -> float:
        """Ink over bounding-box area. A hatched triangle sits near 0.23, an arc near 0.03."""
        h, w = self.mask.shape
        return self.ink_px / max(h * w, 1)

    @classmethod
    def from_selection(cls, class_id: str, selection: Selection, page_index: int) -> "Template":
        """Every piece the selection enclosed, as one template.

        `snap` has already resolved what the drag means -- everything substantially inside it,
        less any text run that continues past its edge -- and built the union mask. So this is
        that mask, and the only work here is recording where the pieces sit.

        The A/T10 marker on T5 still works, and for a better reason than before: its two halves
        (a centre line was drawn through its apex and removed as structure) are both inside the
        drag, so both are in the template. That case used to need a rule about joined ink; now
        it needs nothing.
        """
        if selection.is_empty:
            raise ValueError(f"cannot build a template for {class_id!r} from an empty selection")

        x0, y0, w, h = selection.bbox_px
        parts = tuple(
            (c.bbox_px[0] - x0, c.bbox_px[1] - y0, c.bbox_px[2], c.bbox_px[3])
            for c in sorted(selection.members, key=lambda c: -c.area_px)
        )
        return cls(
            class_id=class_id,
            mask=selection.mask,
            dpi=selection.dpi,
            source_page_index=page_index,
            source_bbox_px=selection.bbox_px,
            parts=parts,
        )


def _box_gap(a: BBox, b: BBox) -> float:
    """Edge-to-edge distance between two boxes; 0 when they touch or overlap."""
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    dx = max(bx - (ax + aw), ax - (bx + bw), 0)
    dy = max(by - (ay + ah), ay - (by + bh), 0)
    return float((dx * dx + dy * dy) ** 0.5)


@dataclass(frozen=True)
class TemplateVariant:
    """One orientation of a template, with its distance transform precomputed.

    `dist` is the distance in pixels from each cell to the nearest ink pixel, clipped at
    DIST_CLIP_PX. Precomputed because the scorer reads it once per candidate and there are
    thousands of candidates per sheet.
    """

    class_id: str
    mask: np.ndarray          # bool,    (H, W)
    dist: np.ndarray          # float32, (H, W) distance to nearest ink
    rotation_deg: float
    mirrored: bool
    exact: bool               # False once resampling was involved
    scale: float = 1.0

    @property
    def size_px(self) -> tuple[int, int]:
        return (self.mask.shape[1], self.mask.shape[0])

    @property
    def ink_px(self) -> int:
        return int(self.mask.sum())

    @property
    def label(self) -> str:
        suffix = "" if self.scale == 1.0 else f"x{self.scale:g}"
        return f"{self.class_id}@{self.rotation_deg:g}{'m' if self.mirrored else ''}{suffix}"


def distance_to_ink(mask: np.ndarray) -> np.ndarray:
    """Pixel distance to the nearest True cell, clipped.

    cv2 measures distance to the nearest ZERO, so the mask is inverted going in: ink becomes
    0 and the transform reports how far each cell is from ink, which is what the scorer asks.
    """
    if not mask.any():
        return np.full(mask.shape, DIST_CLIP_PX, np.float32)
    dist = cv2.distanceTransform((~mask).astype(np.uint8), cv2.DIST_L2, 3)
    return np.minimum(dist, DIST_CLIP_PX).astype(np.float32)


def _rotate(mask: np.ndarray, degrees: float) -> tuple[np.ndarray, bool]:
    """Rotate a boolean mask. Returns the mask and whether the rotation was exact."""
    turns = degrees / 90.0
    if abs(turns - round(turns)) < 1e-9:
        return np.rot90(mask, int(round(turns)) % 4), True

    h, w = mask.shape
    m = cv2.getRotationMatrix2D((w / 2.0, h / 2.0), degrees, 1.0)
    cos, sin = abs(m[0, 0]), abs(m[0, 1])
    nw, nh = int(h * sin + w * cos), int(h * cos + w * sin)
    m[0, 2] += nw / 2.0 - w / 2.0
    m[1, 2] += nh / 2.0 - h / 2.0
    spun = cv2.warpAffine(
        mask.astype(np.uint8), m, (nw, nh), flags=cv2.INTER_NEAREST, borderValue=0
    )
    return spun.astype(bool), False


def _trim(mask: np.ndarray) -> np.ndarray:
    """Crop back to the ink. Rotation leaves blank margins that would skew every size gate."""
    rows, cols = np.any(mask, 1), np.any(mask, 0)
    if not rows.any():
        return mask
    y0, y1 = np.flatnonzero(rows)[[0, -1]]
    x0, x1 = np.flatnonzero(cols)[[0, -1]]
    return mask[y0 : y1 + 1, x0 : x1 + 1]


def _scaled(mask: np.ndarray, factor: float) -> np.ndarray:
    """Resize a boolean mask. Nearest-neighbour, so hatching stays hatching."""
    if factor == 1.0:
        return mask
    height = max(1, int(round(mask.shape[0] * factor)))
    width = max(1, int(round(mask.shape[1] * factor)))
    return cv2.resize(
        mask.astype(np.uint8), (width, height), interpolation=cv2.INTER_NEAREST
    ).astype(bool)


def variants(
    template: Template,
    rotations: tuple[float, ...] = (0.0, 90.0, 180.0, 270.0),
    mirrors: tuple[bool, ...] = (False, True),
    scales: tuple[float, ...] = (1.0,),
) -> list[TemplateVariant]:
    """The rotation x mirror x scale bank for one template, deduplicated.

    A symmetric glyph produces identical masks under several transforms -- a plain triangle
    mirrored is a triangle rotated -- and scoring the same array eight times would inflate
    the cost and make the variant reported for a hit arbitrary. Identical masks collapse to
    the first orientation that produced them.

    Scale belongs in the bank, not in the size gate. Widening the gate to admit a bigger
    instance also widens the bound that stops a group growing into its neighbours: measured
    on T5, taking the size tolerance from 30% to 60% dropped the count from 8 to 4, because
    groups that had been clean markers swallowed the ink beside them. A second scale in the
    bank admits a bigger instance while the bound around each one stays tight.
    """
    bank: list[TemplateVariant] = []
    seen: set[bytes] = set()

    for factor in scales:
        sized = _scaled(template.mask, factor)
        for mirrored in mirrors:
            base = np.fliplr(sized) if mirrored else sized
            for degrees in rotations:
                mask, exact = _rotate(base, degrees)
                mask = _trim(mask)
                key = mask.shape[0].to_bytes(4, "little") + mask.tobytes()
                if key in seen:
                    continue
                seen.add(key)
                bank.append(
                    TemplateVariant(
                        class_id=template.class_id,
                        mask=mask,
                        dist=distance_to_ink(mask),
                        rotation_deg=degrees,
                        mirrored=mirrored,
                        exact=exact and factor == 1.0,
                        scale=factor,
                    )
                )
    return bank
