"""What the ARC detector finds on the URI sheet, if it were allowed to run.

`profile_selection` refuses to read a URI door as an arc, so `identify` never offers
`door_swing` and the generic template path runs instead. This asks the question underneath:
is the arc detector itself capable on this drawing, or is identification hiding a second
failure behind the first?
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from takeoff import candidates as cand
from takeoff import classes, doors, raster

SRC = Path("documents/uri_2511plans.pdf")
INDEX = 4


def main() -> None:
    r = raster.render(SRC, INDEX, dpi=300)
    # The door class turns repair off: an arc is a thin curve and closing gaps merges it
    # into the jamb beside it.
    pool = cand.find_candidates(r, cand.ink_layers(r, repair_gap_px=0))
    page_ink = doors.page_ink_from(r.gray)
    print(f"candidates {len(pool)}")

    # Skanksa's doors are 3'-0" at 1/8in = 1ft-0in -> 0.36-0.40 in of paper. This sheet is
    # 3/32in = 1ft-0in, so the same door is 0.75x as wide on paper: 0.27-0.30 in.
    for label, band in (
        ("registered band (1/8\" sheets)", doors.RADIUS_BAND_IN),
        ("scaled for 3/32\"", (0.18, 0.39)),
        ("wide open", (0.15, 0.60)),
    ):
        t0 = time.perf_counter()
        found = doors.swings_in(pool, r.dpi, band, page_ink)
        radii = sorted(a.radius_px for _, a in found)
        print(f"\n{label} {band}: {len(found)} swings in {time.perf_counter() - t0:.1f}s")
        if radii:
            print(f"   radius px: min {radii[0]:.0f} median {radii[len(radii)//2]:.0f} "
                  f"max {radii[-1]:.0f}")
            q = sorted(a.quality for _, a in found)
            print(f"   quality:   min {q[0]:.2f} median {q[len(q)//2]:.2f} max {q[-1]:.2f}")


if __name__ == "__main__":
    main()
