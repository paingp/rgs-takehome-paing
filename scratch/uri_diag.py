"""Why did a drag around a URI door find nothing? Look at the ink, not at a guess."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
from PIL import Image

from takeoff import candidates as cand
from takeoff import raster

SRC = Path("documents/uri_2511plans.pdf")
INDEX = 4
DRAG = (1260, 4645, 118, 122)


def main() -> None:
    r = raster.render(SRC, INDEX, dpi=300)
    layers = cand.ink_layers(r)
    pool = cand.find_candidates(r, layers)
    print(f"raster {r.gray.shape}  origin {r.origin_sheet_pt}  candidates {len(pool)}")
    print(f"ink coverage {layers.binary.mean():.4f}  suppression removed "
          f"{layers.removed_fraction:.3f}")

    x, y, w, h = DRAG
    near = [c for c in pool
            if c.bbox_px[0] < x + w and c.bbox_px[0] + c.bbox_px[2] > x
            and c.bbox_px[1] < y + h and c.bbox_px[1] + c.bbox_px[3] > y]
    print(f"\ncandidates overlapping the drag: {len(near)}")
    for c in sorted(near, key=lambda c: -c.area_px)[:12]:
        bx, by, bw, bh = c.bbox_px
        inside = max(0, min(x + w, bx + bw) - max(x, bx)) * max(0, min(y + h, by + bh) - max(y, by))
        print(f"   {c.bbox_px}  ink {c.area_px:6d}  box-overlap {inside / (bw * bh):.2f}")

    sel = cand.snap(pool, DRAG, dpi=r.dpi)
    print(f"\nsnap: empty={sel.is_empty} members={len(sel.members)} aside={len(sel.set_aside)}")

    # The whole ink in that box, candidate or not: is the arc even a component?
    band_lo, band_hi = (int(v * r.dpi) for v in cand.SYMBOL_BAND_IN)
    print(f"size band at 300 dpi: {band_lo}..{band_hi} px")
    hosts = cand.host_blobs(r, layers)
    over = [c for c in hosts
            if c.bbox_px[0] < x + w and c.bbox_px[0] + c.bbox_px[2] > x
            and c.bbox_px[1] < y + h and c.bbox_px[1] + c.bbox_px[3] > y]
    print(f"host blobs (too big to be a symbol) touching the drag: {len(over)}")
    for c in sorted(over, key=lambda c: -c.area_px)[:5]:
        print(f"   {c.bbox_px}  ink {c.area_px}")

    pad = 40
    for name, arr in (("gray", r.gray), ("symbols", layers.symbols.astype(np.uint8) * 255)):
        crop = arr[y - pad:y + h + pad, x - pad:x + w + pad]
        if name == "symbols":
            crop = 255 - crop
        Image.fromarray(crop.astype(np.uint8)).resize(
            (crop.shape[1] * 3, crop.shape[0] * 3), Image.NEAREST
        ).save(f"scratch/uri_door_{name}.png")
    print("\nwrote scratch/uri_door_gray.png and scratch/uri_door_symbols.png")


if __name__ == "__main__":
    main()
