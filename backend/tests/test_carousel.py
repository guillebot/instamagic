"""Carousel Graph publish flow (mock / fake provider)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from instamagic.config import get_settings
from instamagic.models import PublishAttempt
from instamagic.services.instagram.client import FakeInstagramClient
from instamagic.services.instagram.feed_image import (
    CAROUSEL_FEED_ASPECT,
    enforce_carousel_feed_geometry,
)
from instamagic.services.posting.scheduler import run_scheduled_publish_tick
from PIL import Image
from sqlalchemy import func, select
from tests.conftest import login

ATTESTATION = "I confirm that every person depicted in this library is a consenting adult."


def _image(path: Path, color: tuple[int, int, int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (800, 600), color).save(path, format="JPEG", quality=90)


def _library_with_two(client, library: Path) -> tuple[str, list[str]]:
    _image(library / "session" / "a.jpg", (40, 40, 90))
    _image(library / "session" / "b.jpg", (90, 40, 40))
    login(client)
    created = client.post(
        "/api/libraries",
        json={"name": "Archive", "root_path": str(library), "confirmed": True, "statement": ATTESTATION},
    )
    library_id = created.json()["id"]
    assert client.post(f"/api/libraries/{library_id}/scan").status_code == 202
    items = client.get("/api/images", params={"library_id": library_id}).json()["items"]
    ids = []
    for row in items:
        client.post(f"/api/images/{row['id']}/rating", json={"rating": "excellent"})
        ids.append(row["id"])
    return library_id, ids


def test_carousel_enqueue_and_publish(client, app_env) -> None:
    library: Path = app_env["library"]
    _, image_ids = _library_with_two(client, library)
    queued = client.post("/api/queue/carousel", json={"image_ids": image_ids})
    assert queued.status_code == 201, queued.text
    body = queued.json()
    assert body["media_type"] == "CAROUSEL"
    assert len(body["slides"]) == 2
    item_id = body["id"]
    client.post(f"/api/queue/{item_id}/approve")
    caption = client.post(f"/api/images/{image_ids[0]}/captions", json={"confirmed": True}).json()[0]
    published = client.post(f"/api/queue/{item_id}/publish", json={"caption_id": caption["id"]})
    assert published.status_code == 200, published.text
    assert published.json()["state"] == "PUBLISHED"


def test_fake_carousel_client_container_flow() -> None:
    client = FakeInstagramClient()
    urls = [f"https://example.test/{i}.jpg" for i in range(3)]
    result = client.publish_carousel(image_urls=urls, caption="Three frames.")
    assert result.media_id.startswith("fake-media-")
    assert len(client.carousel_calls) == 1
    assert len(client.carousel_calls[0]["image_urls"]) == 3


def test_carousel_enforces_4_5_aspect() -> None:
    wide = Image.new("RGB", (1000, 500), (10, 10, 10))
    cropped = enforce_carousel_feed_geometry(wide)
    aspect = cropped.size[0] / cropped.size[1]
    assert abs(aspect - CAROUSEL_FEED_ASPECT) < 0.02


def test_scheduled_carousel_publish(client, app_env) -> None:
    library: Path = app_env["library"]
    _, image_ids = _library_with_two(client, library)
    queued = client.post("/api/queue/carousel", json={"image_ids": image_ids})
    item_id = queued.json()["id"]
    client.post(f"/api/queue/{item_id}/approve")
    caption_id = client.post(f"/api/images/{image_ids[0]}/captions", json={"confirmed": True}).json()[0]["id"]
    past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    client.post(
        f"/api/queue/{item_id}/schedule",
        json={"mode": "explicit", "scheduled_at": past, "caption_id": caption_id},
    )
    from instamagic.db import session_factory

    settings = get_settings()
    fake = FakeInstagramClient()
    session = session_factory()()
    try:
        assert run_scheduled_publish_tick(session, settings, fake) == 1
        assert len(fake.carousel_calls) == 1
        attempts = session.scalar(select(func.count()).select_from(PublishAttempt))
        assert int(attempts or 0) >= 1
    finally:
        session.close()
