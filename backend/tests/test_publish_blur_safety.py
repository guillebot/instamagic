"""Publish must never upload unblurred pixels when approved blur covers exist."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

import numpy as np
from instamagic.services.images.presentation import publish_variant, publish_variant_label
from PIL import Image as PilImage
from PIL import ImageDraw
from tests.conftest import login, write_jpeg

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


def _marked(path: Path, color: tuple[int, int, int], box: tuple[int, int, int, int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image = PilImage.new("RGB", (400, 600), color)
    ImageDraw.Draw(image).rectangle(box, fill=(230, 230, 230))
    image.save(path, format="JPEG", quality=90)


def _checkerboard(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image = PilImage.new("RGB", (200, 200))
    pixels = image.load()
    for y in range(200):
        for x in range(200):
            pixels[x, y] = (255, 0, 0) if (x // 10 + y // 10) % 2 else (0, 0, 255)
    image.save(path, format="JPEG", quality=95)


def test_unapproved_blur_blocks_queue(client, app_env) -> None:
    library = app_env["library"]
    write_jpeg(library / "frame.jpg", size=(200, 200))
    login(client)
    library_id = _attested_library(client, library)
    image_id = client.get("/api/images", params={"library_id": library_id}).json()["items"][0]["id"]
    client.post(f"/api/images/{image_id}/rating", json={"rating": "safe"})
    drawn = client.post(
        f"/api/images/{image_id}/blurs",
        json={"x": 40, "y": 40, "width": 80, "height": 80, "strength": "strong"},
    )
    assert drawn.status_code == 201, drawn.text
    queued = client.post("/api/queue", json={"image_id": image_id})
    assert queued.status_code == 409
    assert "Blur drawn but not approved" in queued.text


def test_api_returns_blurred_thumbnail_url(client, app_env) -> None:
    library = app_env["library"]
    write_jpeg(library / "unsafe.jpg", color=(90, 40, 40))
    login(client)
    library_id = _attested_library(client, library)
    items = client.get("/api/images", params={"library_id": library_id}).json()["items"]
    unsafe = next(item for item in items if item["model_state"] == "SAFE_AFTER_CROP")
    image_id = unsafe["id"]
    client.post(f"/api/images/{unsafe['id']}/rating", json={"rating": "safe"})
    assert client.post(f"/api/images/{image_id}/blurs/suggest", json={"strength": "standard"}).status_code == 200
    assert client.post(f"/api/images/{image_id}/blurs/approve").status_code == 200
    detail = client.get(f"/api/images/{image_id}").json()
    assert detail["thumbnail_url"] == f"/api/derivatives/{image_id}/blurred-thumbnail"
    assert detail["preview_url"] == f"/api/derivatives/{image_id}/blurred-preview"
    assert detail["publish_variant"] == "BLURRED"
    assert detail["publish_variant_label"] == "SAFE · BLURRED"
    thumb = client.get(detail["thumbnail_url"])
    assert thumb.status_code == 200


def test_publish_renders_blur_when_derivative_missing(client, app_env) -> None:
    library = app_env["library"]
    _marked(library / "unsafe.jpg", (90, 40, 40), (40, 300, 180, 420))
    login(client)
    library_id = _attested_library(client, library)
    items = client.get("/api/images", params={"library_id": library_id}).json()["items"]
    unsafe = next(item for item in items if item["model_state"] == "SAFE_AFTER_CROP")
    image_id = unsafe["id"]
    client.post(f"/api/images/{image_id}/rating", json={"rating": "excellent"})
    assert client.post(f"/api/images/{image_id}/blurs/suggest", json={"strength": "standard"}).status_code == 200
    assert client.post(f"/api/images/{image_id}/blurs/approve").status_code == 200
    derivatives = app_env["derivatives"]
    cover = derivatives / f"{image_id}/blurs/cover.jpg"
    if cover.is_file():
        cover.unlink()
    preview = client.get(f"/api/images/{image_id}/publish-preview")
    assert preview.status_code == 200, preview.text
    assert cover.is_file()


def test_crop_plus_blur_publish_uses_blurred_pixels(client, app_env) -> None:
    """Regression: approved crop must be taken from the blurred canvas, not the original."""
    from instamagic.config import get_settings
    from instamagic.db import session_factory
    from instamagic.models import BlurCover, CropSuggestion, Image
    from instamagic.services.instagram.publish_derivative import ensure_publish_derivative
    from sqlalchemy.orm import selectinload

    library = app_env["library"]
    source = library / "combo.jpg"
    _checkerboard(source)
    login(client)
    library_id = _attested_library(client, library)
    image_id = client.get("/api/images", params={"library_id": library_id}).json()["items"][0]["id"]
    client.post(f"/api/images/{image_id}/rating", json={"rating": "safe"})

    session = session_factory()()
    settings = get_settings()
    try:
        image = session.get(
            Image,
            UUID(image_id),
            options=(selectinload(Image.blurs), selectinload(Image.crops)),
        )
        assert image is not None
        blur = BlurCover(
            image_id=image.id,
            x=50,
            y=50,
            width=100,
            height=100,
            shape="ellipse",
            feather=12,
            radius=8.0,
            strength="strong",
            reason="test",
            covers_unsafe=False,
            approved=True,
        )
        crop = CropSuggestion(
            image_id=image.id,
            aspect_ratio="1:1",
            x=0,
            y=0,
            width=200,
            height=200,
            reason="test",
            excludes_unsafe=True,
            approved=True,
        )
        session.add_all([blur, crop])
        session.commit()
        session.refresh(image)

        relative = ensure_publish_derivative(session, settings, image, crop)
        session.commit()
        publish_path = settings.derivatives_root / relative
        assert publish_path.is_file()
        published = np.asarray(PilImage.open(publish_path).convert("RGB"), dtype=np.float32)
        original = np.asarray(PilImage.open(source).convert("RGB"), dtype=np.float32)
        region = published[60:140, 60:140]
        reference = original[60:140, 60:140]
        mean_diff = float(np.mean(np.abs(region - reference)))
        assert mean_diff > 4.0
    finally:
        session.close()


def test_publish_variant_label_helper() -> None:
    assert publish_variant("safe", has_approved_blur=True) == "BLURRED"
    assert publish_variant_label("BLURRED_CROPPED") == "SAFE · BLURRED + CROPPED"
