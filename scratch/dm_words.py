"""What the text layer holds around each detail marker: is there a caption worth reporting?"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from takeoff import layout, raster

BOXES = {
    8: [(4656, 4583, 146, 178), (7330, 4583, 145, 177), (6002, 1602, 146, 176),
        (4407, 1702, 146, 178), (8678, 2047, 146, 177)],
    9: [(999, 1450, 145, 178), (4682, 1494, 146, 175), (1758, 4450, 145, 178),
        (3614, 1571, 146, 176), (7675, 2492, 146, 177)],
}

for index, boxes in BOXES.items():
    r = raster.render(Path("Skanksa.pdf"), index, dpi=300)
    words = layout.words_px(Path("Skanksa.pdf"), index, r.dpi, r.origin_sheet_pt)
    print(f"--- page index {index} ---")
    for b in boxes:
        near = layout.words_near(words, b, limit=6)
        print(f"  {b}: " + " | ".join(f"{w.text!r}" for w in near))
