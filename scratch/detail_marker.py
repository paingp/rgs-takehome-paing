"""What the detail marker's anchor actually yields, before it is written into the registry.

Snaps the anchor into a template, then runs it over the sheets that carry the symbol so the
two thresholds go into `classes.py` measured rather than guessed.

    .venv/Scripts/python.exe scratch/detail_marker.py            # the anchor, drawn
    .venv/Scripts/python.exe scratch/detail_marker.py 8 4 9      # score sweep on those pages
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from takeoff import candidates as cand
from takeoff import detect, raster
from takeoff.classes import SymbolClass, TemplateAnchor

SOURCE = Path("Skanksa.pdf")
PAGE_INDEX = 8
DRAG = (4656, 4583, 146, 177)

CANDIDATE = SymbolClass(
    id="detail_marker_probe",
    name="Detail marker",
    anchor=TemplateAnchor(page_index=PAGE_INDEX, drag_bbox_px=DRAG, dpi=300),
    counted_at=0.90,
    review_floor=0.80,
)


def page_bits(index: int):
    r = raster.render(SOURCE, index, dpi=300)
    layers = cand.ink_layers(r)
    return r, cand.find_candidates(r, layers)


def draw() -> None:
    r, pool = page_bits(PAGE_INDEX)
    sel = cand.snap(pool, DRAG, dpi=r.dpi)
    print(f"page index {PAGE_INDEX}  raster {r.gray.shape}  candidates {len(pool)}")
    print(f"selection: members={len(sel.members)} set_aside={len(sel.set_aside)} "
          f"bbox={sel.bbox_px} ink={sel.area_px}")
    for c in sel.members:
        print(f"   member {c.bbox_px}  ink {c.area_px}")
    for c in sel.set_aside:
        print(f"   aside  {c.bbox_px}  ink {c.area_px}")

    x, y, w, h = sel.bbox_px
    crop = r.gray[y:y + h, x:x + w]
    step = max(1, max(w, h) // 48)
    for row in range(0, h, step):
        print("".join("#" if crop[row, col] < 200 else "." for col in range(0, w, step)))


def sweep(indices: list[int]) -> None:
    ref, ref_pool = page_bits(PAGE_INDEX)
    entry = detect.build_entry(CANDIDATE, ref, ref_pool)
    print(f"template {entry.symbol.id}: {entry.template.size_px} "
          f"ink {int(entry.template.mask.sum())}\n")

    for index in indices:
        r, pool = page_bits(index)
        found = detect.detect(r, pool, [entry], keep_rejected=True)
        rows = sorted(found, key=lambda d: -d.match)
        print(f"--- page index {index} ({len(rows)} detections) ---")
        for d in rows[:40]:
            print(f"  {d.match:.3f}  margin {d.margin if d.margin is not None else float('nan'):+.3f}  {d.status.name:8s} {d.bbox_px}")
        print()


if __name__ == "__main__":
    args = [int(a) for a in sys.argv[1:]]
    if args:
        sweep(args)
    else:
        draw()
