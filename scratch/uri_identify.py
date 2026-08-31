"""Why did a URI door not come back as `door_swing`?

If it had, the arc detector would have run. It did not, so the generic template path ran
instead -- which is the path `doors.py` exists BECAUSE templates cannot do doors.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from takeoff import candidates as cand
from takeoff import classes, detect, doors, raster
from server.app import _class_library, _class_segmentation

SRC = Path("documents/uri_2511plans.pdf")
INDEX = 4
DRAG = (1218, 4655, 172, 110)


def main() -> None:
    library = _class_library()
    print("library:", sorted(library))

    for symbol in classes.all_classes():
        gap, cut = _class_segmentation(symbol)
        r = raster.render(SRC, INDEX, dpi=300)
        pool = cand.find_candidates(
            r, cand.ink_layers(r, ink_threshold=cut, repair_gap_px=gap))
        sel = cand.snap(pool, DRAG, dpi=r.dpi)
        if sel.is_empty:
            print(f"\nsegmentation gap={gap} cut={cut} ({symbol.id}): drag snaps to NOTHING")
            continue
        guess, why = detect.identify(sel, r, pool, references=library)
        print(f"\nsegmentation gap={gap} cut={cut} ({symbol.id})")
        print(f"   selection {sel.bbox_px} ink {sel.area_px}")
        print(f"   identify -> {guess.id!r}: {why}")

    # And the question underneath: is the arc even findable at this scale?
    r = raster.render(SRC, INDEX, dpi=300)
    layers = cand.ink_layers(r, repair_gap_px=0)
    pool = cand.find_candidates(r, layers)
    sel = cand.snap(pool, DRAG, dpi=r.dpi)
    if not sel.is_empty:
        c = max(sel.members, key=lambda c: c.area_px)
        arc = doors.find_swing(c)
        print(f"\narc fit on the selected door: {arc}")
        if arc:
            print(f"   radius {arc.radius_px:.1f} px = "
                  f"{arc.radius_px / r.dpi:.3f} in on paper")
            print(f"   Skanksa doors are 107-121 px; 3/32\" vs 1/8\" predicts "
                  f"{107 * 0.75:.0f}-{121 * 0.75:.0f} px here")


if __name__ == "__main__":
    main()
