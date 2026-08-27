"""Any rasterized drawing, not just the PDF that came with the project.

The goal is to count symbols on a scan. These tests treat an image as a first-class source
and check the three places a PDF and an image genuinely differ -- pages, scale, resampling --
plus the two things that must NOT differ: what the detector sees, and what it finds.
"""

from __future__ import annotations

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from server.app import BUNDLED_SOURCE, app
from takeoff import banding, candidates as cand, classes, detect, doors, layout, raster

PDF = "Skanksa.pdf"
T5 = 4


@pytest.fixture(scope="module")
def scan(tmp_path_factory) -> str:
    """A real region of T5, written out as a PNG: a stand-in for a scanned drawing."""
    r = raster.render(PDF, T5, dpi=300)
    path = tmp_path_factory.mktemp("scans") / "sheet_scan.png"
    cv2.imwrite(str(path), r.gray[1400:3400, 4400:9000])
    return str(path)


# ------------------------------------------------------------------- source recognition


def test_images_are_recognised_and_have_no_text_layer(scan) -> None:
    assert raster.is_image(scan) and not raster.is_image(PDF)
    assert raster.page_count(scan) == 1
    assert raster.has_text_layer(PDF)
    assert not raster.has_text_layer(scan), "a scan has no captions to read"


def test_a_scan_is_not_resampled(scan) -> None:
    """Its pixels ARE the drawing.

    PyMuPDF would open the same file and map it at 96 DPI, so asking for 300 would upscale a
    4600 px scan to 14375 and invent nothing. This is the bug that shipped in the first cut
    of the tile pyramid.
    """
    source = cv2.imread(scan, cv2.IMREAD_GRAYSCALE)
    r = raster.render(scan, 0, dpi=300)
    assert r.size_px == (source.shape[1], source.shape[0])
    assert np.array_equal(r.gray, source)
    assert r.origin_sheet_pt == (0.0, 0.0)


def test_the_declared_dpi_is_what_px_to_inches_uses(scan) -> None:
    """A PDF states its physical size; a scan does not, so the DPI is declared and travels
    with the document. Same pixels, different stated scale."""
    coarse = raster.render(scan, 0, dpi=150)
    fine = raster.render(scan, 0, dpi=300)
    assert coarse.size_px == fine.size_px
    assert coarse.to_sheet(300, 0)[0] == pytest.approx(2 * fine.to_sheet(300, 0)[0])


def test_page_space_for_an_image_is_the_identity(scan) -> None:
    space = raster.page_space(scan, 0, dpi=300)
    assert space.rotation == 0
    assert space.page_size_pt == space.sheet_size_pt
    assert space.page_to_sheet(37.0, 11.0) == (37.0, 11.0)


def test_captions_degrade_rather_than_crash(scan) -> None:
    r = raster.render(scan, 0, dpi=300)
    assert layout.words_px(scan, 0, r.dpi, r.origin_sheet_pt) == []
    assert layout.label_for([], (10, 10, 20, 20)) is None


def test_the_tile_pyramid_is_built_at_native_resolution(scan, tmp_path) -> None:
    out = raster.build_dzi(scan, 0)
    width, height = (int(v) for v in (out / "COMPLETE").read_text().split())
    source = cv2.imread(scan, cv2.IMREAD_GRAYSCALE)
    assert (width, height) == (source.shape[1], source.shape[0])
    assert (out / "sheet.dzi").exists()
    assert list(out.glob("sheet_files/*/*.png")), "tiles must actually be written"


# --------------------------------------------------------- the detector cannot tell either


def test_a_symbol_is_counted_on_a_scan(scan) -> None:
    """The point of all of it. Same detector, same class, no PDF involved."""
    r = raster.render(scan, 0, dpi=300)
    found = cand.find_candidates(
        r, cand.ink_layers(r, repair_gap_px=classes.SWING_DOOR.repair_gap_px)
    )
    assert len(found) > 1000

    # The door beside the elevator, in the crop's coordinates.
    selection = cand.snap(found, (6360 - 4400, 2890 - 1400, 155, 165), dpi=r.dpi)
    assert not selection.is_empty

    entry = detect.entry_from_selection(
        "door_swing", selection, page_index=0, symbol=classes.SWING_DOOR,
        page_ink=doors.page_ink_from(r.gray),
    )
    assert entry.detector == "arc"
    counted = [
        d for d in detect.detect(r, found, [entry]) if d.status is banding.Status.COUNTED
    ]
    assert len(counted) >= 20, len(counted)


def test_a_registered_class_is_recognised_on_a_scan(scan) -> None:
    """Anchors name the document their reference lives in, so a registered symbol stays
    recognisable when the tool is pointed somewhere else. Without that, every symbol on an
    uploaded drawing came back unnamed and lost its calibrated thresholds."""
    assert classes.SWING_DOOR.anchor.source == "Skanksa.pdf"

    reference = raster.render(PDF, classes.SWING_DOOR.anchor.page_index, dpi=300)
    ref_found = cand.find_candidates(
        reference, cand.ink_layers(reference, repair_gap_px=classes.SWING_DOOR.repair_gap_px)
    )
    library = {"door_swing": detect.build_entry(classes.SWING_DOOR, reference, ref_found)}

    r = raster.render(scan, 0, dpi=300)
    found = cand.find_candidates(
        r, cand.ink_layers(r, repair_gap_px=classes.SWING_DOOR.repair_gap_px)
    )
    selection = cand.snap(found, (6360 - 4400, 2890 - 1400, 155, 165), dpi=r.dpi)
    symbol, reason = detect.identify(selection, r, found, references=library)
    assert symbol.id == "door_swing", reason


# ----------------------------------------------------------------------- the HTTP surface


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


def test_the_bundled_drawing_is_listed(client: TestClient) -> None:
    docs = client.get("/api/documents").json()["documents"]
    bundled = [d for d in docs if d["bundled"]]
    assert len(bundled) == 1
    assert bundled[0]["name"] == BUNDLED_SOURCE.name
    assert bundled[0]["kind"] == "pdf" and bundled[0]["has_text_layer"]


def test_uploading_an_image_makes_it_a_document(client: TestClient, scan) -> None:
    with open(scan, "rb") as handle:
        body = handle.read()
    posted = client.post("/api/documents?name=sheet_scan.png", content=body)
    assert posted.status_code == 200, posted.text

    entry = posted.json()
    assert entry["kind"] == "image" and entry["pages"] == 1
    assert not entry["has_text_layer"] and not entry["bundled"]

    # Content-addressed: the same drawing twice is one document.
    again = client.post("/api/documents?name=whatever-else.png", content=body).json()
    assert again["id"] == entry["id"]

    listed = {d["id"] for d in client.get("/api/documents").json()["documents"]}
    assert entry["id"] in listed

    pages = client.get(f"/api/pages?doc={entry['id']}").json()
    assert pages["count"] == 1
    assert pages["document"]["kind"] == "image"


def test_a_non_drawing_upload_is_refused(client: TestClient) -> None:
    refused = client.post("/api/documents?name=notes.txt", content=b"not a drawing")
    assert refused.status_code == 400
    assert "PDF" in refused.json()["detail"]


def test_an_unknown_document_is_a_404(client: TestClient) -> None:
    assert client.get("/api/pages?doc=deadbeefcafe").status_code == 404
