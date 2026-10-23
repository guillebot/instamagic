"""Live per-region coverage report for the blur editor (same math as the unlock gate)."""

from __future__ import annotations

import random
from pathlib import Path
from unittest.mock import patch

import pytest
from instamagic.services.blurs.coverage_report import region_coverage_rows
from instamagic.services.blurs.generator import BlurWindow
from instamagic.services.blurs.masks import region_coverages_rotated
from instamagic.services.geometry import Box
from instamagic.services.safety.signals import Region, SafetySignals
from PIL import Image as PilImage
from PIL import ImageDraw
from tests.conftest import login

ATTESTATION = "I confirm that every person depicted in this library is a consenting adult."


def _window(x, y, w, h, *, shape="rectangle", feather=29, rotation=0.0, corner=0) -> BlurWindow:
    return BlurWindow(
        x=x,
        y=y,
        width=w,
        height=h,
        shape=shape,
        feather=feather,
        radius=32.0,
        strength="strong",
        reason="test",
        covers_unsafe=True,
        rotation_deg=rotation,
        corner_radius=corner,
    )


@pytest.mark.parametrize("seed", range(6))
def test_local_window_matches_full_image_gate_math(seed: int) -> None:
    rng = random.Random(seed)
    size = (667, 1000)
    windows = [
        _window(
            rng.randint(-150, 500),
            rng.randint(-50, 900),
            rng.randint(40, 900),
            rng.randint(40, 400),
            shape=rng.choice(["rectangle", "ellipse"]),
            feather=rng.randint(0, 40),
            rotation=rng.uniform(-30, 30),
            corner=rng.randint(0, 30),
        )
        for _ in range(rng.randint(1, 3))
    ]
    boxes = [Box(rng.randint(-10, 620), rng.randint(-10, 950), rng.randint(20, 200), rng.randint(20, 200)) for _ in range(3)]
    expected = [coverage for _, coverage in region_coverages_rotated(windows, boxes, size, padding=6)]
    got = [row["coverage"] for row in region_coverage_rows(windows, boxes, size, padding=6)]
    assert got == pytest.approx(expected, abs=1e-9)


def test_reported_case_band_too_short_and_feathered() -> None:
    # Geometry of the reported image: a rotated feathered band whose top edge sits below
    # the top of both flagged boxes. The gate (and the report) must call these uncovered.
    band = _window(-118, 327, 973, 142, feather=29, rotation=4.34)
    boxes = [Box(341, 304, 186, 162), Box(152, 309, 174, 152)]
    rows = region_coverage_rows([band], boxes, (667, 1000))
    gate = [coverage for _, coverage in region_coverages_rotated([band], boxes, (667, 1000), padding=6)]
    assert [row["coverage"] for row in rows] == pytest.approx(gate, abs=1e-9)
    assert all(row["coverage"] < 0.95 for row in rows)
    # The drawn outline covers far more than the feathered mask counts.
    assert all(row["inside_outline"] > row["coverage"] for row in rows)
    assert rows[0]["missed"]["top"] > 0


def test_fully_covered_region_reports_nothing_missed() -> None:
    cover = _window(0, 0, 400, 400, feather=10)
    rows = region_coverage_rows([cover], [Box(100, 100, 120, 120)], (400, 400))
    assert rows[0]["coverage"] >= 0.95
    assert rows[0]["missed"] == {"top": 0, "bottom": 0, "left": 0, "right": 0}


def _marked(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image = PilImage.new("RGB", (400, 600), (40, 40, 90))
    ImageDraw.Draw(image).rectangle((120, 80, 200, 280), fill=(230, 230, 230))
    image.save(path, format="JPEG", quality=90)


def _blocked_image(client, app_env) -> str:
    library = app_env["library"]
    _marked(library / "blocked-frame.jpg")
    login(client)
    created = client.post(
        "/api/libraries",
        json={"name": "Archive", "root_path": str(library), "confirmed": True, "statement": ATTESTATION},
    )
    library_id = created.json()["id"]
    assert client.post(f"/api/libraries/{library_id}/scan").status_code == 202
    item = client.get("/api/images", params={"library_id": library_id}).json()["items"][0]
    assert item["model_state"] == "DO_NOT_POST"
    return item["id"]


def test_coverage_endpoint_tracks_current_geometry_and_names_failed_check(client, app_env) -> None:
    image_id = _blocked_image(client, app_env)
    suggested = client.post(f"/api/images/{image_id}/blurs/suggest", json={"strength": "strong"})
    assert suggested.status_code == 200, suggested.text
    report = client.get(f"/api/images/{image_id}/blur-coverage").json()
    assert report["regions"], report
    assert report["min_coverage"] == 0.95
    assert report["coverage_check"] == "pass"
    assert all(row["covered"] for row in report["regions"])

    blur = suggested.json()[0]
    moved = client.patch(f"/api/images/{image_id}/blurs/{blur['id']}", json={"x": 0, "y": 500, "height": 60})
    assert moved.status_code == 200, moved.text
    report = client.get(f"/api/images/{image_id}/blur-coverage").json()
    assert report["coverage_check"] == "fail"
    assert not any(row["covered"] for row in report["regions"])
    assert report["verification"]["recheck"] == "not_run"

    assert client.post(f"/api/images/{image_id}/blurs/approve").status_code == 200
    detail = client.get(f"/api/images/{image_id}").json()
    assert detail["blur_unlock_status"] == "failed"
    assert "coverage check" in detail["blur_unlock_message"].lower()
    assert "%" in detail["blur_unlock_message"]
    report = client.get(f"/api/images/{image_id}/blur-coverage").json()
    assert report["verification"]["current_geometry"] is True
    assert report["verification"]["recheck"] == "pass"

    client.post(f"/api/images/{image_id}/rating", json={"rating": "safe"})
    ready = client.get(f"/api/images/{image_id}/publish-readiness").json()
    assert ready["publishing_allowed"] is False
    assert any("coverage check" in reason.lower() for reason in ready["publishing_reasons"])
    # The gate itself is unchanged: still not queueable.
    assert client.post("/api/queue", json={"image_id": image_id}).status_code == 409


def test_recheck_failure_is_named(client, app_env) -> None:
    image_id = _blocked_image(client, app_env)
    assert client.post(f"/api/images/{image_id}/blurs/suggest", json={"strength": "strong"}).status_code == 200
    unsafe = SafetySignals(
        regions=[Region("FEMALE_BREAST_EXPOSED", 0.96, Box(10, 10, 80, 80))],
        detector_available=True,
        uncertain=False,
        model_name="test",
    )
    with patch("instamagic.services.blurs.unlock._classify_render", return_value=unsafe):
        assert client.post(f"/api/images/{image_id}/blurs/approve").status_code == 200
    detail = client.get(f"/api/images/{image_id}").json()
    assert "re-check on the blurred render" in detail["blur_unlock_message"].lower()
    report = client.get(f"/api/images/{image_id}/blur-coverage").json()
    assert report["verification"]["recheck"] == "fail"
    assert report["coverage_check"] == "pass"
