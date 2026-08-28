"""API surface of the viewer server.

Page 5 (T5) is assumed already built by the fixture; these tests are about the contract the
viewer depends on, not about tiling quality.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

import numpy as np
import pytest
from fastapi.testclient import TestClient

from server.app import BUNDLED_SOURCE, app
from takeoff import raster

PAGE = 5  # T5, the sheet the gated build works on first


@pytest.fixture(scope="module")
def client() -> TestClient:
    raster.build_dzi(BUNDLED_SOURCE, PAGE - 1)  # no-op once cached
    return TestClient(app)


def test_index_serves_the_viewer(client: TestClient) -> None:
    body = client.get("/").text
    assert "openseadragon.min.js" in body
    assert "viewer.js" in body


def test_pages_reports_the_sheet_count(client: TestClient) -> None:
    meta = client.get("/api/pages").json()
    assert meta["count"] == 28
    assert meta["pdf"] == "Skanksa.pdf"


def test_status_ready_carries_what_the_viewer_needs(client: TestClient) -> None:
    info = client.get(f"/api/pages/{PAGE}/status").json()
    assert info["state"] == "ready"
    assert (info["width"], info["height"]) == (10800, 7200)
    assert info["dpi"] == raster.VIEWER_DPI
    assert info["dzi"] == f"/api/pages/{PAGE}/sheet.dzi"


def test_descriptor_is_valid_dzi(client: TestClient) -> None:
    response = client.get(f"/api/pages/{PAGE}/sheet.dzi")
    assert response.status_code == 200
    root = ET.fromstring(response.text)
    assert root.attrib["TileSize"] == str(raster.DZI_TILE)
    assert root.attrib["Overlap"] == str(raster.DZI_OVERLAP)
    size = root[0]
    assert size.attrib["Width"] == "10800"


def test_tiles_are_served_at_the_path_openseadragon_derives(client: TestClient) -> None:
    """OSD turns `.../sheet.dzi` into `.../sheet_files/<level>/<col>_<row>.png` itself."""
    response = client.get(f"/api/pages/{PAGE}/sheet_files/14/0_0.png")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_page_out_of_range_is_404(client: TestClient) -> None:
    assert client.get("/api/pages/29/status").status_code == 404
    assert client.get("/api/pages/0/status").status_code == 404


def test_tile_path_traversal_is_rejected(client: TestClient) -> None:
    assert client.get(f"/api/pages/{PAGE}/sheet_files/14/..%2f..%2fsheet.dzi").status_code != 200
    assert client.get(f"/api/pages/{PAGE}/sheet_files/14/COMPLETE").status_code == 400


def test_unbuilt_page_reports_idle_not_error(client: TestClient) -> None:
    """The viewer distinguishes 'not built yet' from 'broken', and only builds on POST."""
    unbuilt = next(
        (n for n in range(1, 29) if not raster.dzi_is_built(BUNDLED_SOURCE, n - 1)), None
    )
    if unbuilt is None:
        pytest.skip("every page is already cached")
    info = client.get(f"/api/pages/{unbuilt}/status").json()
    assert info["state"] == "idle"
    assert client.get(f"/api/pages/{unbuilt}/sheet.dzi").status_code == 404


# ------------------------------------------------------------------ candidates and select


@pytest.fixture(scope="module")
def boxes(client: TestClient) -> list[list[float]]:
    return client.get(f"/api/pages/{PAGE}/candidates").json()["boxes"]


def test_candidates_endpoint_shape(client: TestClient) -> None:
    data = client.get(f"/api/pages/{PAGE}/candidates").json()
    assert 4_350 <= data["count"] <= 4_700
    assert len(data["boxes"]) == data["count"]
    assert data["detection_dpi"] == raster.DETECTION_DPI
    assert data["viewer_dpi"] == raster.VIEWER_DPI
    assert all(len(b) == 4 and b[2] > 0 and b[3] > 0 for b in data["boxes"])


def _ink_coverage(gray: np.ndarray, boxes: list[list[float]]) -> float:
    values = []
    for x, y, w, h in boxes:
        a, b, c, d = (int(round(v)) for v in (x, y, x + w, y + h))
        if c <= a or d <= b or a < 0 or b < 0 or c > gray.shape[1] or d > gray.shape[0]:
            continue
        values.append(float((gray[b:d, a:c] < 200).mean()))
    return float(np.mean(values)) if values else 0.0


def test_candidate_boxes_land_on_ink_in_viewer_space(client: TestClient, boxes) -> None:
    """The overlay contract: boxes are in tile-pyramid image px, and they sit on the ink.

    Detection px and image px share a DPI today, so a conversion that simply passed the
    numbers through would be accidentally correct. Scaling and shifting the same boxes shows
    the check can actually fail rather than passing for any plausible transform.
    """
    gray = raster.render(BUNDLED_SOURCE, PAGE - 1, dpi=raster.VIEWER_DPI).gray

    correct = _ink_coverage(gray, boxes)
    halved = _ink_coverage(gray, [[x / 2, y / 2, w / 2, h / 2] for x, y, w, h in boxes])
    shifted = _ink_coverage(gray, [[x + 40, y + 40, w, h] for x, y, w, h in boxes])

    assert correct > 0.45, f"candidate boxes are not on ink: {correct:.3f}"
    assert correct > 1.7 * halved, f"a scale error would not be caught: {correct:.3f} vs {halved:.3f}"
    assert correct > 1.7 * shifted, f"an offset error would not be caught: {correct:.3f} vs {shifted:.3f}"


def test_select_snaps_a_sloppy_drag(client: TestClient, boxes) -> None:
    x, y, w, h = boxes[len(boxes) // 2]
    margin = 0.4 * max(w, h)
    result = client.post(
        f"/api/pages/{PAGE}/select",
        json={"bbox_image_px": [x - margin, y - margin, w + 2 * margin, h + 2 * margin]},
    ).json()

    assert result["found"] is True
    assert result["component_count"] >= 1
    assert result["ink_px"] > 0
    assert result["preview_png"].startswith("data:image/png;base64,")

    # The snapped box hugs the glyph, not the drag: it must be no larger than the drag and
    # must overlap the component that seeded it.
    sx, sy, sw, sh = result["bbox_image_px"]
    assert sw <= w + 2 * margin and sh <= h + 2 * margin
    assert sx < x + w and sx + sw > x and sy < y + h and sy + sh > y

    width_in, height_in = result["size_in"]
    assert width_in == pytest.approx(result["size_px"][0] / raster.DETECTION_DPI, abs=1e-3)
    assert height_in == pytest.approx(result["size_px"][1] / raster.DETECTION_DPI, abs=1e-3)


def test_select_on_blank_paper_reports_not_found(client: TestClient) -> None:
    """The sheet margin carries a border rule, which line suppression removes as structure."""
    result = client.post(f"/api/pages/{PAGE}/select", json={"bbox_image_px": [5, 5, 60, 60]}).json()
    assert result["found"] is False
    assert "reason" in result


def test_select_rejects_a_malformed_body(client: TestClient) -> None:
    assert client.post(f"/api/pages/{PAGE}/select", json={"nope": 1}).status_code == 422


# ----------------------------------------- identifying a symbol whatever segmentation it needs


def test_dragging_a_door_is_recognised_as_the_registered_door_class(client: TestClient) -> None:
    """A class that turns line-suppression repair off cannot be identified on the default
    segmentation, because repair is what destroys the ink it is recognised by.

    A door's swing is a thin curve; closing gaps merges it into the jamb, so on the default
    pool the arc is not a candidate at all and the profiler sees only the keynote bubble and
    its letters. Every door dragged in the viewer used to come back unnamed -- with the
    generic thresholds instead of the class's calibrated ones -- while `-m eval.suites`
    counted the same sheet correctly, because the harness builds each class on its own ink.
    """
    from server.app import _candidates_for, _to_image_px
    from takeoff import classes

    r, _ = _candidates_for(PAGE, classes.SWING_DOOR.repair_gap_px)
    drag = _to_image_px(classes.SWING_DOOR.anchor.drag_bbox_px, r)

    body = client.post(f"/api/pages/{PAGE}/count", json={"bbox_image_px": drag}).json()
    assert body["found"] is True
    assert body["class_id"] == "door_swing", body["identified_as"]
    assert body["registered"] is True
    assert body["template"]["detector"] == "arc"
    assert body["counts"]["by_band"]["counted"] == 31


def test_counting_from_the_anchor_uses_the_classs_own_segmentation(client: TestClient) -> None:
    """Counting a class by name, with no drag, must reach the same detector as dragging it.

    The anchor path used to build its entry on the DEFAULT segmentation whatever the class
    asked for. At a 10 px repair gap a swing arc merges with its jamb, stops being a thin
    component, and `profile_selection` cannot read a curve in it -- so `door_swing` fell
    through to template matching and returned 15 detections, 9 after region gating, against
    the arc path's 31. The count was wrong and nothing said so.
    """
    body = client.post(f"/api/pages/{PAGE}/count", json={"class_id": "door_swing"}).json()
    assert body["found"] is True
    assert body["template"]["source"] == "anchor"
    assert body["template"]["detector"] == "arc"
    assert body["counts"]["by_band"]["counted"] == 31


def test_the_marker_is_still_identified_on_the_default_segmentation(client: TestClient) -> None:
    """The other half: trying more segmentations must not move a class that was already
    recognised on the first one."""
    from server.app import _candidates_for, _to_image_px
    from takeoff import classes

    r, _ = _candidates_for(PAGE)
    drag = _to_image_px(classes.ELEVATION_MARKER.anchor.drag_bbox_px, r)

    body = client.post(f"/api/pages/{PAGE}/count", json={"bbox_image_px": drag}).json()
    assert body["class_id"] == "elev_marker", body["identified_as"]
    assert body["template"]["detector"] == "template"
    assert body["counts"]["by_band"]["counted"] == 10


def test_candidates_report_the_sheets_regions(client: TestClient) -> None:
    """The Candidates overlay shows every blob; the region list says which of them a count
    would actually consider, so `no amber box` and `not somewhere an instance can be` stay
    distinguishable from the outside."""
    body = client.get(f"/api/pages/{PAGE}/candidates").json()
    assert body["count"] > 4000
    assert 0 < body["countable"] <= body["count"]
    kinds = {g["kind"] for g in body["regions"]}
    assert "drawing" in kinds and "text" in kinds
    for g in body["regions"]:
        assert len(g["bbox_image_px"]) == 4


def test_truth_round_trips_every_class_on_the_page(client: TestClient, monkeypatch, tmp_path) -> None:
    """A page holds more than one class, and the save replaces the whole page.

    The endpoint must be blind to which class a reviewer happens to be working on: a pass
    over doors that came back with only doors would silently erase the markers recorded
    beside them, and the file on disk is the only copy.
    """
    from takeoff import schema

    monkeypatch.setattr(schema, "GT_ROOT", tmp_path)
    sent = [
        {"class_id": "door_swing", "bbox_image_px": [6395, 2915, 108, 112], "label": "EX"},
        {"class_id": "elev_marker", "bbox_image_px": [6470, 2870, 62, 148], "label": "C/T9"},
    ]
    posted = client.post(f"/api/pages/{PAGE}/truth", json={"instances": sent})
    assert posted.json()["instances"] == 2

    read = client.get(f"/api/pages/{PAGE}/truth").json()
    assert read["annotated"] is True
    assert [i["class_id"] for i in read["instances"]] == ["door_swing", "elev_marker"]
    assert [i["label"] for i in read["instances"]] == ["EX", "C/T9"]


def test_a_page_can_record_that_a_class_is_absent(client: TestClient, monkeypatch, tmp_path) -> None:
    """"No markers on this sheet" and "nobody looked for markers" are different claims.

    T4 is the case: it carries doors and genuinely no elevation markers, so the marker
    detector's hits there are false positives. Without the class list they would be
    ungradeable, because an empty instance list cannot say which of the two it means.
    """
    from takeoff import schema

    monkeypatch.setattr(schema, "GT_ROOT", tmp_path)
    body = {
        "instances": [
            {"class_id": "door_swing", "bbox_image_px": [6395, 2915, 108, 112], "label": "EX"}
        ],
        "reviewed_classes": ["elev_marker"],
    }
    client.post(f"/api/pages/{PAGE}/truth", json=body)

    read = client.get(f"/api/pages/{PAGE}/truth").json()
    # Both: the class asserted empty, and the class that has instances and needs no assertion.
    assert read["reviewed_classes"] == ["door_swing", "elev_marker"]

    stored = schema.load_truth(read["document"], PAGE, tmp_path)
    assert stored.is_reviewed("elev_marker") and not stored.for_class("elev_marker")
    assert stored.is_reviewed("door_swing")
    assert not stored.is_reviewed("receptacle")


def test_grading_says_how_to_grade_when_nobody_has(client: TestClient, monkeypatch, tmp_path) -> None:
    """An ungraded page is the normal case, not an error -- and the answer is a command."""
    from eval import suites

    monkeypatch.setattr(suites, "REPORT_ROOT", tmp_path)
    body = client.get(f"/api/pages/{PAGE}/grade").json()
    assert body["graded"] is False
    assert "eval.suites" in body["how"]


def test_grading_hands_the_viewer_boxes_in_its_own_pixels(client: TestClient, monkeypatch, tmp_path) -> None:
    """The report is stored in detection pixels and the viewer works in image pixels.

    One conversion, in one place, on the way out -- the same one `/truth` makes. A box that
    arrived in the wrong space would land on the wrong ink, which is the one thing a grading
    overlay must never do.
    """
    import json

    from eval import suites
    from takeoff import raster

    monkeypatch.setattr(suites, "REPORT_ROOT", tmp_path)
    document = raster.source_hash(BUNDLED_SOURCE)
    path = tmp_path / document / f"page{PAGE:03d}.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({
        "version": 1, "document": document, "page": PAGE, "source": "Skanksa.pdf",
        "run_at": "2026-08-28T00:00:00+00:00", "not_graded": ["receptacle: not annotated"],
        "classes": {"elev_marker": {
            "true_positives": 1, "false_positives": 0, "false_negatives": 1,
            "precision": 1.0, "recall": 0.5, "f1": 0.667, "review_volume": 0,
            "occluded_recall": 0.0,
            "matched": [{"truth_px": [6470, 2870, 62, 148], "detection_px": [6470, 2870, 62, 148],
                         "distance_px": 0.0, "match": 0.99, "occluded": False, "label": "C/T9"}],
            "missed": [{"truth_px": [9185, 2299, 51, 147], "occluded": True, "label": None}],
            "spurious": [], "in_review": [],
        }},
    }), encoding="utf-8")

    body = client.get(f"/api/pages/{PAGE}/grade").json()
    assert body["graded"] is True and body["run_at"] == "2026-08-28T00:00:00+00:00"
    assert body["not_graded"] == ["receptacle: not annotated"]

    boxes = body["classes"]["elev_marker"]["boxes"]
    assert {b["kind"] for b in boxes} == {"matched", "missed"}
    missed = next(b for b in boxes if b["kind"] == "missed")
    assert missed["occluded"] is True
    # Image pixels, not detection pixels: the DZI is rendered at the viewer's own DPI.
    scale = raster.VIEWER_DPI / raster.DETECTION_DPI
    assert missed["bbox_image_px"][0] == pytest.approx(9185 * scale, abs=1.0)
