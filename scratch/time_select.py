"""Where the wall clock goes on a viewer selection.

`/select` felt slow. This times the pieces on a cold process and then on a warm one, so the
fix goes where the seconds actually are rather than where they look like they should be.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

PAGE = 5                      # T5, the sheet most of the measurements in PROGRESS.md use
DRAG = [6470, 2870, 62, 148]  # the elevation marker anchor, as a viewer drag


class Clock:
    def __init__(self) -> None:
        self.rows: list[tuple[str, float]] = []

    def __call__(self, label: str, fn):
        t0 = time.perf_counter()
        out = fn()
        self.rows.append((label, time.perf_counter() - t0))
        return out

    def report(self, title: str) -> None:
        total = sum(d for _, d in self.rows)
        print(f"\n=== {title} ===")
        for label, d in self.rows:
            print(f"  {d:7.2f}s  {label}")
        print(f"  {total:7.2f}s  TOTAL")
        self.rows = []


def main() -> None:
    from server import app as srv
    from takeoff import candidates as cand

    clock = Clock()
    clock("_class_library (all anchors)", srv._class_library)
    clock("_candidates_for default", lambda: srv._candidates_for(PAGE))
    for symbol in __import__("takeoff.classes", fromlist=["x"]).all_classes():
        gap, cut = srv._class_segmentation(symbol)
        if (gap, cut) == (cand.REPAIR_GAP_PX, cand.INK_THRESHOLD):
            continue
        clock(f"_candidates_for gap={gap} cut={cut} ({symbol.id})",
              lambda g=gap, c=cut: srv._candidates_for(PAGE, g, ink_threshold=c))
    clock.report("cold: what the first selection pays for")

    body = srv.DragBox(bbox_image_px=DRAG)
    out = clock("POST /select (everything warm)", lambda: srv.select(PAGE, body))
    clock.report("warm: what every later selection pays for")
    print(f"read_as={out.get('read_as')} parts={len(out.get('parts', []))}")

    # And the same drag again, to separate cache misses from real work.
    clock("POST /select, repeat", lambda: srv.select(PAGE, body))
    clock.report("repeat")


if __name__ == "__main__":
    main()
