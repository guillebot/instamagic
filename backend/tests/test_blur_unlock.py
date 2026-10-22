"""DO_NOT_POST blur-to-unlock verification and gates."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from instamagic.services.blurs.generator import BlurWindow, region_coverages
from instamagic.services.geometry import Box
from instamagic.services.safety.signals import Region, SafetySignals
from PIL import Image as PilImage
from PIL import ImageDraw
from tests.conftest import login


def _marked(path: Path, color: tuple[int, int, int], box: tuple[int, int, int, int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image = PilImage.new("RGB", (400, 600), color)
    ImageDraw.Draw(image).rectangle(box, fill=(230, 230, 230))
    image.save(path, format="JPEG", quality=90)

ATTESTATION = "I confirm that every person depicted in this library is a consenting adult."


def _attested_library(client, library: Path) -> str:
    created = client.post(
        "/api/libraries",
        json={"name": "Archive", "root_path": str(library), "confirmed": True, "statement": ATTESTATION},
    )
    assert created.status_code == 201, created.text
    library_id = created.json()["id"]
    assert client.post(f"/api/libraries/{library_id}/scan").status_code == 202
    return library_id


def test_region_coverage_requires_most_of_box() -> None:
    cover = BlurWindow(
        x=30,
        y=30,
        width=120,
        height=120,
        shape="ellipse",
        feather=8,
        radius=20.0,
        strength="strong",
        reason="test",
        covers_unsafe=True,
    )
    target = Box(50, 50, 60, 60)
    rows = region_coverages([cover], [target], (200, 200), min_ratio=0.95, padding=0)
    assert rows[0].coverage >= 0.95


def test_blur_unlock_verified_allows_queue(client, app_env) -> None:
    library = app_env["library"]
    _marked(library / "blocked-frame.jpg", (40, 40, 90), (120, 80, 200, 280))
    login(client)
    library_id = _attested_library(client, library)
    items = {row["filename"]: row for row in client.get("/api/images", params={"library_id": library_id}).json()["items"]}
    image_id = items["blocked-frame.jpg"]["id"]
    assert items["blocked-frame.jpg"]["model_state"] == "DO_NOT_POST"

    suggested = client.post(f"/api/images/{image_id}/blurs/suggest", json={"strength": "strong"})
    assert suggested.status_code == 200, suggested.text
    approved = client.post(f"/api/images/{image_id}/blurs/approve")
    assert approved.status_code == 200, approved.text

    detail = client.get(f"/api/images/{image_id}").json()
    assert detail["blur_unlock_status"] == "verified"
    assert "verified" in (detail["blur_unlock_message"] or "").lower()

    client.post(f"/api/images/{image_id}/rating", json={"rating": "safe"})
    queued = client.post("/api/queue", json={"image_id": image_id})
    assert queued.status_code == 201, queued.text


def test_blur_unlock_fails_when_classifier_still_unsafe(client, app_env) -> None:
    library = app_env["library"]
    _marked(library / "blocked-frame.jpg", (40, 40, 90), (120, 80, 200, 280))
    login(client)
    library_id = _attested_library(client, library)
    image_id = client.get("/api/images", params={"library_id": library_id}).json()["items"][0]["id"]
    assert client.post(f"/api/images/{image_id}/blurs/suggest", json={"strength": "strong"}).status_code == 200

    unsafe_signals = SafetySignals(
        regions=[Region("FEMALE_BREAST_EXPOSED", 0.96, Box(10, 10, 80, 80))],
        detector_available=True,
        uncertain=False,
        model_name="test",
    )

    with patch(
        "instamagic.services.blurs.unlock._classify_render",
        return_value=unsafe_signals,
    ):
        approved = client.post(f"/api/images/{image_id}/blurs/approve")
    assert approved.status_code == 200, approved.text
    detail = client.get(f"/api/images/{image_id}").json()
    assert detail["blur_unlock_status"] == "failed"
    assert "classifier" in (detail["blur_unlock_message"] or "").lower()


def test_blur_edit_after_verify_resets_and_blocks_queue(client, app_env) -> None:
    library = app_env["library"]
    _marked(library / "blocked-frame.jpg", (40, 40, 90), (120, 80, 200, 280))
    login(client)
    library_id = _attested_library(client, library)
    image_id = client.get("/api/images", params={"library_id": library_id}).json()["items"][0]["id"]
    assert client.post(f"/api/images/{image_id}/blurs/suggest", json={"strength": "strong"}).status_code == 200
    assert client.post(f"/api/images/{image_id}/blurs/approve").status_code == 200
    client.post(f"/api/images/{image_id}/rating", json={"rating": "safe"})

    assert client.post(f"/api/images/{image_id}/blurs/strength", json={"strength": "subtle"}).status_code == 200
    detail = client.get(f"/api/images/{image_id}").json()
    assert detail["blur_unlock_status"] == "pending"
    assert client.post("/api/queue", json={"image_id": image_id}).status_code == 409


def test_publish_preview_requires_unlock_on_do_not_post(client, app_env) -> None:
    library = app_env["library"]
    _marked(library / "blocked-frame.jpg", (40, 40, 90), (120, 80, 200, 280))
    login(client)
    library_id = _attested_library(client, library)
    image_id = client.get("/api/images", params={"library_id": library_id}).json()["items"][0]["id"]
    assert client.post(f"/api/images/{image_id}/blurs/suggest", json={"strength": "strong"}).status_code == 200
    assert client.post(f"/api/images/{image_id}/blurs/approve").status_code == 200
    preview = client.get(f"/api/images/{image_id}/publish-preview")
    assert preview.status_code == 200, preview.text
