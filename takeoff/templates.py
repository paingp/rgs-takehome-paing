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

    The mask is the glyph the person pointed at -- the largest piece of ink in the selection
    together with every piece that was joined to it on the drawing before line suppression
    ran -- and not everything the selection happened to enclose. A template that spans ink
    the drawing never joined has nothing that could match it: measured on T5, the best score
    available to any of 4,770 candidates against a six-blob template was 0.780, below even
    the review floor. A template like that reports nothing and cannot say why.

    What the selection held besides the glyph is kept as `context_*` rather than thrown away.
    On the T5 elevation marker that is the `C\\T9` sheet reference sitting 14 px off the
    triangle -- genuinely part of the annotation, and genuinely different on every instance,
    so it identifies a marker but can never help match one.
    """

    class_id: str
    mask: np.ndarray          # bool, (H, W) -- the matchable glyph, one connected component
    dpi: int
    source_page_index: int
    source_bbox_px: BBox      # where `mask` sits, not where the selection sat
    context_blobs: int = 0    # disconnected pieces the selection held and matching ignores
    context_ink_px: int = 0

    @property
    def trimmed(self) -> bool:
        """True when the selection held more than the glyph that will actually be matched."""
        return self.context_blobs > 0

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
        """Take the glyph the person pointed at; count anything else as context.

        The glyph is the largest piece of ink in the selection PLUS every other piece that
        was joined to it on the drawing, before line suppression ran. `Candidate.raw_id`
        carries that: two pieces sharing one mean suppression pulled a single blob apart by
        removing a line drawn across it.

        The A/T10 marker on T5 is exactly this. A centre line runs through its apex, gets
        removed as structure, and takes the apex junction with it, leaving two halves 6 px
        apart. Keeping only the larger half made a template of half a triangle -- 0.143 x
        0.220 in instead of 0.143 x 0.427 -- which then counted each half of every marker
        separately. The label beside a marker is a different case and still drops out: it was
        never joined to the glyph, so its raw_id differs and no distance threshold is needed
        to tell the two apart.

        Largest by ink, not by bounding box: a sheet reference set in small type can outrun a
        thin glyph's box while carrying a fraction of its ink.
        """
        if selection.is_empty:
            raise ValueError(f"cannot build a template for {class_id!r} from an empty selection")

        primary = max(selection.members, key=lambda c: c.area_px)
        gap = max(3.0, JOIN_GAP_FACTOR * max(primary.bbox_px[2], primary.bbox_px[3]))

        glyph = [primary]
        x0, y0 = primary.bbox_px[0], primary.bbox_px[1]
        x1, y1 = x0 + primary.bbox_px[2], y0 + primary.bbox_px[3]

        # Grow over pieces the drawing joined to this one and that sit within a stroke of it.
        changed = True
        while changed:
            changed = False
            for c in selection.members:
                if c in glyph or c.raw_id != primary.raw_id:
                    continue
                cx, cy, cw, ch = c.bbox_px
                if cx > x1 + gap or cx + cw < x0 - gap or cy > y1 + gap or cy + ch < y0 - gap:
                    continue
                glyph.append(c)
                x0, y0 = min(x0, cx), min(y0, cy)
                x1, y1 = max(x1, cx + cw), max(y1, cy + ch)
                changed = True

        context = [c for c in selection.members if c not in glyph]

        mask = np.zeros((y1 - y0, x1 - x0), bool)
        for c in glyph:
            cx, cy, cw, ch = c.bbox_px
            mask[cy - y0 : cy - y0 + ch, cx - x0 : cx - x0 + cw] |= c.mask

        return cls(
            class_id=class_id,
            mask=mask,
            dpi=selection.dpi,
            source_page_index=page_index,
            source_bbox_px=(x0, y0, x1 - x0, y1 - y0),
            context_blobs=len(context),
            context_ink_px=int(sum(c.area_px for c in context)),
        )


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
