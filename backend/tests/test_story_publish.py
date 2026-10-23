"""Instagram Story publishing (optional, blur-safe, mocked Graph)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from instamagic.models.enums import StoryRenderMode
from instamagic.services.instagram.client import FakeInstagramClient
from instamagic.services.instagram.story_image import (
    STORY_HEIGHT,
    STORY_WIDTH,
    render_story_from_feed_file,
    write_story_jpeg,
)
from instamagic.services.instagram.story_publish import (
    schedule_story_after_feed,
)
from PIL import Image, ImageDraw
from tests.conftest import login

ATTESTATION = "I confirm that every person depicted in this library is a consenting adult."


def _marked(path: Path, color: tuple[int, int, int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (800, 1000), color)
    ImageDraw.Draw(image).rectangle((100, 200, 700, 900), fill=(200, 200, 200))
    image.save(path, format="JPEG", quality=90)


def _library(client, library: Path) -> str:
    created = client.post(
        "/api/libraries",
        json={"name": "Archive", "root_path": str(library), "confirmed": True, "statement": ATTESTATION},
    )
    assert created.status_code == 201
    library_id = created.json()["id"]
    assert client.post(f"/api/libraries/{library_id}/scan").status_code == 202
    return library_id


def test_story_render_modes_dimensions(app_env, tmp_path: Path) -> None:
    feed = tmp_path / "feed.jpg"
    _marked(feed, (30, 80, 120))
    for mode in StoryRenderMode:
        story = render_story_from_feed_file(feed, mode=mode.value, border_color="black")
        assert story.size == (STORY_WIDTH, STORY_HEIGHT)
        out = tmp_path / f"story-{mode.value}.jpg"
        write_story_jpeg(story, out)
        assert out.stat().st_size < 8 * 1024 * 1024


def test_story_publishes_after_feed_with_fake_client(client, app_env, monkeypatch) -> None:
    library: Path = app_env["library"]
    _marked(library / "session" / "ok.jpg", (90, 40, 40))
    login(client)
    library_id = _library(client, library)
    items = client.get("/api/images", params={"library_id": library_id}).json()["items"]
    image_id = items[0]["id"]
    client.post(f"/api/images/{image_id}/rating", json={"rating": "excellent"})
    crop = client.get(f"/api/images/{image_id}/crops").json()[0]
    client.post(f"/api/images/{image_id}/crops/{crop['id']}/approve", json={})
    captions = client.post(f"/api/images/{image_id}/captions", json={"confirmed": True}).json()
    queue = client.post(
        "/api/queue",
        json={"image_id": image_id, "story_share_enabled": True, "story_timing_mode": "with_post"},
    ).json()
    client.post(f"/api/queue/{queue['id']}/approve")
    res = client.post(f"/api/queue/{queue['id']}/publish", json={"caption_id": captions[0]["id"]})
    assert res.status_code == 200, res.text
    row = next(item for item in client.get("/api/queue").json() if item["id"] == queue["id"])
    assert row["state"] == "PUBLISHED"
    assert row.get("story_published_at") is not None
    assert row.get("story_status") == "published"


def test_story_quota_exhausted_feed_ok(client, app_env) -> None:
    fake = client.app.state.instagram
    assert isinstance(fake, FakeInstagramClient)
    fake.content_limit_usage = 50
    fake.content_limit_total = 50

    library: Path = app_env["library"]
    _marked(library / "session" / "quota.jpg", (10, 10, 10))
    login(client)
    library_id = _library(client, library)
    items = client.get("/api/images", params={"library_id": library_id}).json()["items"]
    image_id = items[0]["id"]
    client.post(f"/api/images/{image_id}/rating", json={"rating": "excellent"})
    crop = client.get(f"/api/images/{image_id}/crops").json()[0]
    client.post(f"/api/images/{image_id}/crops/{crop['id']}/approve", json={})
    captions = client.post(f"/api/images/{image_id}/captions", json={"confirmed": True}).json()
    queue = client.post(
        "/api/queue",
        json={"image_id": image_id, "story_share_enabled": True},
    ).json()
    client.post(f"/api/queue/{queue['id']}/approve")
    res = client.post(f"/api/queue/{queue['id']}/publish", json={"caption_id": captions[0]["id"]})
    assert res.status_code == 200
    row = next(item for item in client.get("/api/queue").json() if item["id"] == queue["id"])
    assert row["state"] == "PUBLISHED"
    assert row.get("story_published_at") is None
    assert "limit" in (row.get("story_last_error") or "").lower()


def test_delayed_story_scheduling_unit() -> None:
    from instamagic.models import PublishingQueueItem

    item = PublishingQueueItem(
        image_id=uuid4(),
        story_share_enabled=True,
        story_timing_mode="delay_hours",
        story_delay_hours=3,
    )
    published_at = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    schedule_story_after_feed(item, published_at=published_at)
    assert item.story_scheduled_at == published_at + timedelta(hours=3)


def test_story_preview_requires_auth(client, app_env) -> None:
    library: Path = app_env["library"]
    _marked(library / "session" / "preview.jpg", (20, 20, 20))
    login(client)
    library_id = _library(client, library)
    image_id = client.get("/api/images", params={"library_id": library_id}).json()["items"][0]["id"]
    client.cookies.clear()
    unauth = client.get(f"/api/images/{image_id}/story-preview")
    assert unauth.status_code == 401

    login(client)
    preview = client.get(
        f"/api/images/{image_id}/story-preview",
        headers={"X-Forwarded-For": "127.0.0.1"},
    )
    assert preview.status_code == 200
