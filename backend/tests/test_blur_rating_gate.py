"""Blur first, verify, then rate: no safe/excellent (and no Gemini pixels) for unverified covers."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

from instamagic.config import get_settings
from instamagic.db import session_factory
from instamagic.models import BlurCover, Image, ImageRating, PublishingQueueItem
from instamagic.services.blurs.rating_gate import fresh_blur_gate_passes
from instamagic.services.recommendations.gemini_derivatives import curation_derivative_bytes
from PIL import Image as PilImage
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from tests.conftest import login

ATTESTATION = "I confirm that every person depicted in this library is a consenting adult."


def _textured(path: Path) -> None:
    # Texture so the blur integrity check can see the cover actually changed pixels.
    path.parent.mkdir(parents=True, exist_ok=True)
    image = PilImage.new("RGB", (400, 600))
    pixels = image.load()
    for y in range(600):
        for x in range(400):
            pixels[x, y] = (255, 0, 0) if (x // 10 + y // 10) % 2 else (0, 0, 255)
    image.save(path, format="JPEG", quality=95)


def _image_id(client, library: Path) -> str:
    _textured(library / "frame.jpg")
    login(client)
    created = client.post(
        "/api/libraries",
        json={"name": "Archive", "root_path": str(library), "confirmed": True, "statement": ATTESTATION},
    )
    library_id = created.json()["id"]
    assert client.post(f"/api/libraries/{library_id}/scan").status_code == 202
    return client.get("/api/images", params={"library_id": library_id}).json()["items"][0]["id"]


def _cover(client, image_id: str) -> dict:
    drawn = client.post(
        f"/api/images/{image_id}/blurs",
        json={"x": 40, "y": 40, "width": 120, "height": 120, "shape": "rectangle", "strength": "strong"},
    )
    assert drawn.status_code == 201, drawn.text
    return drawn.json()


def _load(session, image_id: str) -> Image:
    return session.scalars(
        select(Image)
        .where(Image.id == UUID(image_id))
        .options(selectinload(Image.blurs), selectinload(Image.crops))
    ).one()


def test_rating_safe_refused_until_blur_verified(client, app_env) -> None:
    image_id = _image_id(client, app_env["library"])
    _cover(client, image_id)
    detail = client.get(f"/api/images/{image_id}").json()
    assert detail["blur_verification_required"] is True
    assert detail["blur_verified"] is False
    for rating in ("safe", "excellent"):
        refused = client.post(f"/api/images/{image_id}/rating", json={"rating": rating})
        assert refused.status_code == 409
        assert "Blur and verify first" in refused.json()["detail"]
    # Review/reject stay available.
    assert client.post(f"/api/images/{image_id}/rating", json={"rating": "review"}).status_code == 200

    assert client.post(f"/api/images/{image_id}/blurs/approve").status_code == 200
    detail = client.get(f"/api/images/{image_id}").json()
    assert detail["blur_unlock_status"] == "verified"
    assert detail["blur_verified"] is True
    assert client.post(f"/api/images/{image_id}/rating", json={"rating": "safe"}).status_code == 200


def test_blur_edit_demotes_and_pulls_from_approved_keeping_history(client, app_env) -> None:
    image_id = _image_id(client, app_env["library"])
    _cover(client, image_id)
    assert client.post(f"/api/images/{image_id}/blurs/approve").status_code == 200
    assert client.post(f"/api/images/{image_id}/rating", json={"rating": "safe"}).status_code == 200
    queued = client.post("/api/queue", json={"image_id": image_id})
    assert queued.status_code == 201, queued.text
    queue_id = queued.json()["id"]
    assert client.post(f"/api/queue/{queue_id}/approve").status_code == 200

    assert client.post(f"/api/images/{image_id}/blurs/strength", json={"strength": "subtle"}).status_code == 200
    detail = client.get(f"/api/images/{image_id}").json()
    assert detail["human_rating"] == "review"
    assert detail["blur_unlock_status"] == "pending"
    session = session_factory()()
    try:
        item = session.get(PublishingQueueItem, UUID(queue_id))
        assert item.state == "CANDIDATE"
        assert item.approved_at is None and item.scheduled_at is None
        history = session.scalars(
            select(ImageRating.rating).where(ImageRating.image_id == UUID(image_id)).order_by(ImageRating.created_at)
        ).all()
        assert "safe" in history and history[-1] == "review"
    finally:
        session.close()
    assert client.post(f"/api/queue/{queue_id}/approve").status_code == 409


def test_gemini_bytes_only_from_verified_blurred_render(client, app_env) -> None:
    image_id = _image_id(client, app_env["library"])
    _cover(client, image_id)
    settings = get_settings()
    session = session_factory()()
    try:
        assert curation_derivative_bytes(session, settings, _load(session, image_id)) is None
    finally:
        session.close()

    assert client.post(f"/api/images/{image_id}/blurs/approve").status_code == 200
    session = session_factory()()
    try:
        data = curation_derivative_bytes(session, settings, _load(session, image_id))
        assert data
        rendered = sorted((app_env["derivatives"] / image_id / "publish").glob("from-blur*.jpg"))
        assert rendered and data == rendered[0].read_bytes()
    finally:
        session.close()


def test_fresh_gate_sees_edits_made_by_another_session(client, app_env) -> None:
    image_id = _image_id(client, app_env["library"])
    _cover(client, image_id)
    assert client.post(f"/api/images/{image_id}/blurs/approve").status_code == 200
    worker = session_factory()()
    try:
        image = _load(worker, image_id)
        assert fresh_blur_gate_passes(worker, image) is True
        # Another session moves the cover after the worker loaded the image.
        other = session_factory()()
        try:
            cover = other.scalars(select(BlurCover).where(BlurCover.image_id == UUID(image_id))).one()
            cover.x = cover.x + 50
            other.commit()
        finally:
            other.close()
        assert fresh_blur_gate_passes(worker, image) is False
    finally:
        worker.close()
