"""Tunable blur coverage threshold and live detector probe."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from instamagic.services.geometry import Box
from instamagic.services.safety.signals import Region, SafetySignals
from PIL import Image as PilImage
from PIL import ImageDraw
from tests.conftest import login

ATTESTATION = "I confirm that every person depicted in this library is a consenting adult."


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
        json={
            "name": "Archive",
            "root_path": str(library),
            "confirmed": True,
            "statement": ATTESTATION,
        },
    )
    library_id = created.json()["id"]
    assert client.post(f"/api/libraries/{library_id}/scan").status_code == 202
    return client.get("/api/images", params={"library_id": library_id}).json()["items"][0]["id"]


def test_config_rejects_out_of_range_threshold(client, app_env) -> None:
    login(client)
    bad = client.put("/api/posting-schedule/config", json={"blur_coverage_threshold": 55})
    assert bad.status_code == 422
    bad_pad = client.put("/api/posting-schedule/config", json={"blur_region_padding_px": 25})
    assert bad_pad.status_code == 422


def test_coverage_endpoint_reflects_threshold(client, app_env) -> None:
    login(client)
    assert (
        client.put("/api/posting-schedule/config", json={"blur_coverage_threshold": 80}).status_code
        == 200
    )
    image_id = _blocked_image(client, app_env)
    assert (
        client.post(
            f"/api/images/{image_id}/blurs/suggest", json={"strength": "strong"}
        ).status_code
        == 200
    )
    report = client.get(f"/api/images/{image_id}/blur-coverage").json()
    assert report["min_coverage"] == 0.8
    assert report["coverage_threshold_percent"] == 80


def test_classifier_still_required_at_low_threshold(client, app_env) -> None:
    login(client)
    assert (
        client.put("/api/posting-schedule/config", json={"blur_coverage_threshold": 60}).status_code
        == 200
    )
    image_id = _blocked_image(client, app_env)
    assert (
        client.post(
            f"/api/images/{image_id}/blurs/suggest", json={"strength": "strong"}
        ).status_code
        == 200
    )
    unsafe = SafetySignals(
        regions=[Region("FEMALE_BREAST_EXPOSED", 0.96, Box(10, 10, 80, 80))],
        detector_available=True,
        uncertain=False,
        model_name="test",
    )
    with patch("instamagic.services.blurs.unlock._classify_render", return_value=unsafe):
        assert client.post(f"/api/images/{image_id}/blurs/approve").status_code == 200
    detail = client.get(f"/api/images/{image_id}").json()
    assert detail["blur_unlock_status"] == "failed"
    assert "re-check" in detail["blur_unlock_message"].lower()


def test_raising_threshold_re_gates_verified_unlock(client, app_env) -> None:
    login(client)
    assert (
        client.put("/api/posting-schedule/config", json={"blur_coverage_threshold": 60}).status_code
        == 200
    )
    image_id = _blocked_image(client, app_env)
    assert (
        client.post(
            f"/api/images/{image_id}/blurs/suggest", json={"strength": "strong"}
        ).status_code
        == 200
    )
    assert client.post(f"/api/images/{image_id}/blurs/approve").status_code == 200
    detail = client.get(f"/api/images/{image_id}").json()
    assert detail["blur_unlock_status"] == "verified"

    assert (
        client.put("/api/posting-schedule/config", json={"blur_coverage_threshold": 95}).status_code
        == 200
    )
    detail = client.get(f"/api/images/{image_id}").json()
    assert detail["blur_unlock_status"] == "verified"
    assert "SAFE" not in (detail["blur_unlock_message"] or "")
    client.post(f"/api/images/{image_id}/rating", json={"rating": "safe"})
    assert client.post("/api/queue", json={"image_id": image_id}).status_code == 409


def test_blur_detector_probe_endpoint(client, app_env) -> None:
    image_id = _blocked_image(client, app_env)
    assert (
        client.post(
            f"/api/images/{image_id}/blurs/suggest", json={"strength": "strong"}
        ).status_code
        == 200
    )
    unsafe = SafetySignals(
        regions=[Region("FEMALE_BREAST_EXPOSED", 0.88, Box(5, 5, 90, 90))],
        detector_available=True,
        uncertain=False,
        model_name="test",
    )
    with patch("instamagic.services.blurs.unlock._classify_render", return_value=unsafe):
        probe = client.post(f"/api/images/{image_id}/blur-detector-probe")
    assert probe.status_code == 200, probe.text
    body = probe.json()
    assert body["passed"] is False
    assert body["detections"]
    assert body["top_score"] >= 0.88

    safe = SafetySignals(regions=[], detector_available=True, uncertain=False, model_name="test")
    with patch("instamagic.services.blurs.unlock._classify_render", return_value=safe):
        probe_ok = client.post(f"/api/images/{image_id}/blur-detector-probe")
    assert probe_ok.json()["passed"] is True
