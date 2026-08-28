"""Accuracy / template-sensitivity / robustness / regression suites.

Run one page against its reviewed annotations:

    .venv/Scripts/python.exe -m eval.suites --page 5
    .venv/Scripts/python.exe -m eval.suites --page 1 --source documents/<hash>.png

What this replaces is a person -- me, all of last session -- rendering a contact sheet after
every change and deciding by eye whether the count got better. That does not scale past two
classes, is not reproducible, and gives no warning when a fix for one class quietly breaks
another. Three changes last session each moved counts invisibly until someone looked.

Every number here is against `gt/<document>/pageNNN.json`. A page nobody has annotated is
reported as unannotated rather than scored, because an empty truth file and an unseen page
are different claims.

May import pymupdf -- it renders pages to run the detector over them.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import json
from datetime import datetime, timezone

from eval import harness, report as report_mod
from takeoff import candidates as cand
from takeoff import classes, detect, doors, raster, regions, schema

DEFAULT_SOURCE = Path("Skanksa.pdf")

# Where a graded run lands. Keyed by document hash exactly like `gt/` and `cache/`, so a
# report, the annotations it was graded against, and the tiles it was detected on all sit
# under the same id for the same drawing.
REPORT_ROOT = Path("eval/reports")


def _page_bits(source: Path, index: int, symbol: classes.SymbolClass, cache: dict):
    """Raster and candidates for one page, on the segmentation THIS class uses.

    Cached per (page, repair gap) because grading a sheet now renders two pages -- the sheet
    itself and the sheet each class is anchored on -- and the two classes registered today
    share neither gap nor page.
    """
    key = (index, symbol.repair_gap_px)
    if key not in cache:
        r = raster.render(source, index, dpi=raster.DETECTION_DPI)
        found = cand.find_candidates(r, cand.ink_layers(r, repair_gap_px=symbol.repair_gap_px))
        cache[key] = (r, found)
    return cache[key]


def _entry_for(symbol: classes.SymbolClass, cache: dict) -> detect.ClassEntry | None:
    """One class's entry, rebuilt from its anchor the way the server does.

    The anchor is a drag box on a particular sheet of the bundled drawing, so the entry is
    built THERE and then run against whatever page is being graded. It used to be built on
    the page under test, which meant a class could only ever be graded on the one sheet it
    was registered from: `--page 4` skipped both classes in silence and reported 27 misses
    and a precision of 1.000 against a detector that had not been asked to look.

    `ClassEntry` carries no page of its own, so moving it is legitimate -- it is a template
    bank or a radius band, both measured in inches on the sheet. What does NOT travel is
    plan scale: a radius band from a 1/8" viewport finds nothing in a 1/4" one, which is why
    T4's three viewports still need scale handling rather than a bigger tolerance.
    """
    try:
        r, found = _page_bits(DEFAULT_SOURCE, symbol.anchor.page_index, symbol, cache)
    except (RuntimeError, ValueError, IndexError):
        return None  # the anchor's sheet is not in this drawing
    return detect.build_entry(symbol, r, found)


def report_path(document: str, page: int, root: Path | None = None) -> Path:
    return Path(REPORT_ROOT if root is None else root) / document / f"page{page:03d}.json"


def run_page(
    source: Path, page: int, root: Path | None = None, reports: Path | None = None
) -> int:
    """Score every registered class on one page. Returns a process exit code."""
    document = raster.source_hash(source)
    truth = schema.load_truth(document, page, root)
    if truth is None:
        print(f"page {page} of {source.name} has not been annotated yet")
        print(f"  expected {schema.truth_path(document, page, root)}")
        print("  annotate it in the viewer: count, review, mark what was missed, Save truth")
        return 2

    truth = truth.reviewed
    index = page - 1
    cache: dict = {}

    detections: list[detect.Detection] = []
    skipped: list[str] = []
    for symbol in classes.all_classes():
        # A class nobody has looked for on this page is not scored. Its detections are
        # neither right nor wrong here -- calling them false positives would invent an
        # answer key, and dropping them silently would hide the detector's own claims.
        if not truth.is_reviewed(symbol.id):
            skipped.append(f"{symbol.id}: not annotated on this page")
            continue
        entry = _entry_for(symbol, cache)
        if entry is None:
            skipped.append(f"{symbol.id}: anchored on a sheet this drawing does not have")
            continue
        # Each class is detected on the segmentation IT uses, exactly as the server does --
        # grading a class on ink it never sees would measure the wrong thing.
        r, found = _page_bits(source, index, symbol, cache)
        # The server counts inside the sheet's drawing blocks, so the harness has to as
        # well. Grading a wider pool than the tool actually uses would report false
        # positives nobody can see and would drift from the thing being measured.
        detections.extend(
            detect.detect(r, found, [entry], regions=regions.segment(r, found))
        )

    graded = {s.id for s in classes.all_classes()} - {row.split(":")[0] for row in skipped}
    scores = {c: s for c, s in harness.score_page(detections, truth).items() if c in graded}
    print(f"{source.name}  page {page}  ({len(truth.instances)} reviewed instances)")
    print(harness.format_table(scores))
    for row in skipped:
        # Named, never scored. A blank row would read as a pass.
        print(f"  {row}")

    # The run, kept. Without this the only record of which box was the false positive is a
    # terminal scrollback, and the viewer has nothing to draw.
    written = report_mod.payload(scores, skipped)
    written["document"] = document
    written["page"] = page
    written["source"] = source.name
    written["run_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    path = report_path(document, page, reports)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(written, indent=2) + "\n", encoding="utf-8")
    print(f"  report: {path}")

    # Non-zero when anything is wrong, so this is usable as a check in CI. A class that
    # could not be graded counts as wrong: a green run that quietly measured nothing is the
    # exact failure this suite exists to stop.
    perfect = scores and not skipped and all(
        s.false_positives == 0 and s.false_negatives == 0 for s in scores.values()
    )
    return 0 if perfect else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--page", type=int, required=True, help="1-based sheet number")
    parser.add_argument(
        "--source", "--pdf", dest="source", default=str(DEFAULT_SOURCE),
        help="drawing to grade: a PDF or an image",
    )
    parser.add_argument("--gt", default=str(schema.GT_ROOT), help="annotations root")
    parser.add_argument("--reports", default=str(REPORT_ROOT), help="where to write the run")
    args = parser.parse_args(argv)
    return run_page(Path(args.source), args.page, Path(args.gt), Path(args.reports))


if __name__ == "__main__":
    sys.exit(main())
