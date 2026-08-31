"""Is the warm worth parallelising? The passes are independent; the GIL may not care."""
from __future__ import annotations

import sys, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
PAGE = 5

from server import app as srv

jobs = [("library", srv._class_library)]
for gap, cut in srv._segmentations():
    jobs.append((f"gap={gap} cut={cut}",
                 lambda g=gap, c=cut: srv._candidates_for(PAGE, g, ink_threshold=c)))

mode = sys.argv[1] if len(sys.argv) > 1 else "serial"
t0 = time.perf_counter()
if mode == "parallel":
    with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        list(pool.map(lambda j: j[1](), jobs))
else:
    for _, fn in jobs:
        fn()
print(f"{mode}: {time.perf_counter() - t0:.2f}s over {len(jobs)} jobs")
