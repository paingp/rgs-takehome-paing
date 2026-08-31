"""Does the detail marker steal the elevation marker's instances, or vice versa?

Both carry the same hatched wedge. Runs every class together -- which is what turns the
margin gate on -- over the sheets that hold either symbol.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from takeoff import candidates as cand
from takeoff import classes, detect, raster

SOURCE = Path("Skanksa.pdf")
PAGES = [int(a) for a in sys.argv[1:]] or [4, 8, 9]


def bits(index: int, symbol=None):
    gap = None if symbol is None else symbol.repair_gap_px
    cut = None if symbol is None else symbol.ink_threshold
    kw = {}
    if gap is not None:
        kw["repair_gap_px"] = gap
    if cut is not None:
        kw["ink_threshold"] = cut
    r = raster.render(SOURCE, index, dpi=300)
    return r, cand.find_candidates(r, cand.ink_layers(r, **kw))


entries = []
for symbol in classes.all_classes():
    ref, pool = bits(symbol.anchor.page_index, symbol)
    entries.append(detect.build_entry(symbol, ref, pool))
    print(f"entry {symbol.id}: anchor page {symbol.anchor.page_index}")
print()

for index in PAGES:
    r, pool = bits(index)
    found = detect.detect(r, pool, entries)
    tally: dict[tuple[str, str], int] = {}
    for d in found:
        tally[(d.class_id, d.status.name)] = tally.get((d.class_id, d.status.name), 0) + 1
    print(f"--- page index {index} ---")
    for (cid, status), n in sorted(tally.items()):
        print(f"  {cid:20s} {status:8s} {n}")
    for d in sorted(found, key=lambda d: -d.match):
        if d.class_id in ("detail_marker", "elev_marker"):
            m = "None " if d.margin is None else f"{d.margin:+.3f}"
            print(f"    {d.class_id:15s} {d.match:.3f} margin {m} {d.status.name:8s} "
                  f"runner-up {d.runner_up} {d.bbox_px}")
    print()
