"""Does the tool work on a drawing it has never seen?

URI Barlow Hall, Tecton Architects, sheet BA-A2.101 -- a residence-hall door and lock
replacement bid set from a different firm, downloaded from Rhode Island's public purchasing
portal. Three things differ from Skanksa.pdf and each could break something:

    page rotation   0        vs Skanksa's 90/270   -- spaces.py
    plan scale      3/32"    vs Skanksa's 1/8"     -- doors are 25% smaller
    draughtsman     Tecton   vs whoever drew Skanksa

Driven through the SERVER's own endpoints, not the library, so this is what a person gets.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from server.app import app, documents

PAGE = 5           # sheet BA-A2.101, 1st and 2nd floor plans
DOC = None         # filled in below


def run(client, doc, label, drag):
    print(f"\n=== {label} ===")
    print(f"drag {drag}")
    t0 = time.perf_counter()
    sel = client.post(f"/api/pages/{PAGE}/select?doc={doc}",
                      json={"bbox_image_px": list(drag)}).json()
    if not sel.get("found"):
        print("  select: NOTHING FOUND --", sel.get("reason"))
        return
    print(f"  select: {sel['size_px']} px, {sel['size_in'][0]:.3f}x{sel['size_in'][1]:.3f} in, "
          f"ink {sel['ink_px']}, {sel['component_count']} parts, "
          f"read_as={sel['read_as']}  ({time.perf_counter() - t0:.1f}s)")

    t1 = time.perf_counter()
    out = client.post(f"/api/pages/{PAGE}/count?doc={doc}",
                      json={"bbox_image_px": sel["bbox_image_px"]}).json()
    if not out.get("found"):
        print("  count: NOTHING --", out.get("reason"))
        return
    counts = out["counts"]
    print(f"  count: class={out['class_id']} registered={out['registered']} "
          f"detector={out.get('detector')}  ({time.perf_counter() - t1:.1f}s)")
    print(f"         counted={counts.get('counted')} review={counts.get('review')} "
          f"total={counts.get('total')}")
    scores = sorted((d["match"] for d in out["detections"]), reverse=True)
    print(f"         best {['%.3f' % s for s in scores[:8]]}")
    print(f"         worst {['%.3f' % s for s in scores[-5:]]}")
    if out.get("diagnostics", {}).get("note"):
        print("         note:", out["diagnostics"]["note"])


def main() -> None:
    client = TestClient(app)
    docs = documents()
    doc = next(k for k, v in docs.items() if v.name == "uri_2511plans.pdf")
    print(f"document {doc} -> {docs[doc].name}, page {PAGE}")

    # A single swing door in room 113, measured off the raster.
    run(client, doc, "single swing door", (1218, 4655, 172, 110))
    # The door tag: a stadium-shaped bubble with the door number inside. Nothing like it is
    # registered, and nothing like it exists on Skanksa.pdf.
    run(client, doc, "door tag bubble", (1265, 4742, 115, 62))


if __name__ == "__main__":
    main()
