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
