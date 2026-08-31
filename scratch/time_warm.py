"""Does warming the sheet on open actually take the wait off the first drag?

Simulates what the viewer now does: POST /warm the moment the sheet is on screen, then drag.
The number that matters is how long `select` blocks when a person drags DURING the warm --
if the per-key build locks work, it waits for the pass already running rather than starting
a second one.
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

PAGE = 5
DRAG = [6470, 2870, 62, 148]


def main() -> None:
    from server import app as srv

    body = srv.DragBox(bbox_image_px=DRAG)

    t0 = time.perf_counter()
    srv.warm(PAGE)                                  # what the viewer fires on open
    print(f"POST /warm returned in {time.perf_counter() - t0:.3f}s (non-blocking)")

    # A person drags almost immediately. This is the case the build locks exist for.
    time.sleep(1.0)
    t1 = time.perf_counter()
    out = srv.select(PAGE, body)
    early = time.perf_counter() - t1
    print(f"drag 1.0s after open: {early:6.2f}s   read_as={out.get('read_as')}")

    while srv._warm_state(PAGE)["state"] == "reading":
        time.sleep(0.2)
    print(f"warm finished: {srv._warm_state(PAGE)}")

    t2 = time.perf_counter()
    srv.select(PAGE, body)
    print(f"drag after warm:      {time.perf_counter() - t2:6.2f}s")


if __name__ == "__main__":
    main()
