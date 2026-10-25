"""Standalone Stories and Reels: creation, gates, render, approval, fake publish, lanes."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from instamagic.services.instagram.assets import (
    is_allowed_publish_relative_path,
    publish_asset_media_type,
)
from instamagic.services.instagram.reel_video import plan_durations
from PIL import Image, ImageDraw
from tests.conftest import login

ATTESTATION = "I confirm that every person depicted in this library is a consenting adult."
needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


def _marked(path: Path, color: tuple[int, int, int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (800, 1000), color)
    ImageDraw.Draw(image).rectangle((100, 200, 700, 900), fill=(200, 200, 200))
    image.save(path, format="JPEG", quality=90)


def _approved_images(client, library: Path, count: int) -> list[str]:
    for idx in range(count):
        _marked(library / "session" / f"p{idx}.jpg", (40 + idx * 30, 60, 90))
    login(client)
    created = client.post(
        "/api/libraries",
        json={"name": "Archive", "root_path": str(library), "confirmed": True, "statement": ATTESTATION},
    )
    library_id = created.json()["id"]
    assert client.post(f"/api/libraries/{library_id}/scan").status_code == 202
    items = client.get("/api/images", params={"library_id": library_id}).json()["items"]
    ids = []
    for row in items[:count]:
        image_id = row["id"]
        client.post(f"/api/images/{image_id}/rating", json={"rating": "excellent"})
        crop = client.get(f"/api/images/{image_id}/crops").json()[0]
        client.post(f"/api/images/{image_id}/crops/{crop['id']}/approve", json={})
        ids.append(image_id)
    return ids


def test_reel_asset_paths_and_types() -> None:
    uid = "12345678-1234-1234-1234-123456789abc"
    assert is_allowed_publish_relative_path(f"{uid}/publish/reel-{'a' * 32}.mp4")
    for bad in (
        f"{uid}/publish/reel-x.mp4",
        f"{uid}/originals/reel-{'a' * 32}.mp4",
        f"{uid}/publish/feed.mov",
        f"{uid}/publish/../reel-{'a' * 32}.mp4",
    ):
        assert not is_allowed_publish_relative_path(bad)
    assert publish_asset_media_type(f"abc/publish/reel-{'a' * 32}.mp4") == "video/mp4"
    assert publish_asset_media_type("abc/publish/feed.jpg") == "image/jpeg"


def test_reel_durations_within_instagram_window() -> None:
    for n in range(1, 11):
        _per, total = plan_durations(n)
        assert 7.0 <= total <= 15.0 + 1e-6, (n, total)


def test_story_item_publishes_without_caption(client, app_env) -> None:
    [image_id] = _approved_images(client, app_env["library"], 1)
    res = client.post("/api/queue/story", json={"image_ids": [image_id]})
    assert res.status_code == 201, res.text
    story = res.json()[0]
    assert story["media_type"] == "STORY"
    preview = client.get(story["story_preview_url"])
    assert preview.status_code == 200
    with Image.open(__import__("io").BytesIO(preview.content)) as img:
        assert img.size == (1080, 1920)
    # Feed queue for the same photo is a separate item.
    feed = client.post("/api/queue", json={"image_id": image_id}).json()
    assert feed["id"] != story["id"]
    assert client.post(f"/api/queue/{story['id']}/approve").status_code == 200
    pub = client.post(f"/api/queue/{story['id']}/publish", json={})
    assert pub.status_code == 200, pub.text
    fake = client.app.state.instagram
    assert len(fake.story_calls) == 1
    assert "/api/public/publish-assets/" in fake.story_calls[0]["image_url"]
    assert not getattr(fake, "calls", [])  # nothing published to the feed


def test_reel_requires_render_before_approval(client, app_env) -> None:
    ids = _approved_images(client, app_env["library"], 2)
    reel = client.post("/api/queue/reel", json={"image_ids": ids})
    assert reel.status_code == 201, reel.text
    body = reel.json()
    assert body["media_type"] == "REELS"
    assert len(body["slides"]) == 2
    assert body["reel_status"] == "none"
    assert client.post(f"/api/queue/{body['id']}/approve").status_code == 409


def test_post_type_switch(client, app_env) -> None:
    [image_id] = _approved_images(client, app_env["library"], 1)
    feed = client.post("/api/queue", json={"image_id": image_id}).json()
    res = client.post(f"/api/queue/{feed['id']}/post-type", json={"media_type": "STORY"})
    assert res.status_code == 200, res.text
    assert res.json()["media_type"] == "STORY"
    res = client.post(f"/api/queue/{feed['id']}/post-type", json={"media_type": "REELS"})
    assert res.json()["media_type"] == "REELS"
    assert client.post(f"/api/queue/{feed['id']}/post-type", json={"media_type": "BOGUS"}).status_code in {400, 409, 422}


@needs_ffmpeg
def test_reel_render_approve_publish_fake(client, app_env) -> None:
    [image_id] = _approved_images(client, app_env["library"], 1)
    reel = client.post("/api/queue/reel", json={"image_ids": [image_id]}).json()
    started = client.post(f"/api/queue/{reel['id']}/reel-render")
    assert started.status_code == 202, started.text
    row = next(item for item in client.get("/api/queue").json() if item["id"] == reel["id"])
    assert row["reel_status"] == "ready", row.get("reel_error")
    assert 7.0 <= row["reel_duration_s"] <= 15.5
    video = client.get(row["reel_video_url"])
    assert video.status_code == 200
    assert video.headers["content-type"] == "video/mp4"
    captions = client.post(f"/api/images/{image_id}/captions", json={"confirmed": True}).json()
    assert client.post(f"/api/queue/{reel['id']}/approve").status_code == 200
    pub = client.post(f"/api/queue/{reel['id']}/publish", json={"caption_id": captions[0]["id"]})
    assert pub.status_code == 200, pub.text
    fake = client.app.state.instagram
    assert len(fake.reel_calls) == 1
    assert "/api/public/publish-assets/" in fake.reel_calls[0]["video_url"]
    assert pub.json()["permalink"].startswith("https://www.instagram.com/reel/")
    from instamagic.services.posting.hashtags import is_blocked, tags_in_text

    sent = fake.reel_calls[0]["caption"]
    assert sent == pub.json()["published_caption"]
    assert not any(is_blocked(tag) for tag in tags_in_text(sent))


@needs_ffmpeg
def test_reel_served_through_signed_public_asset(client, app_env) -> None:
    from instamagic.config import get_settings
    from instamagic.services.instagram.assets import mint_publish_token

    [image_id] = _approved_images(client, app_env["library"], 1)
    reel = client.post("/api/queue/reel", json={"image_ids": [image_id]}).json()
    client.post(f"/api/queue/{reel['id']}/reel-render")
    relative = f"{image_id}/publish/reel-{reel['id'].replace('-', '')}.mp4"
    token = mint_publish_token(relative_path=relative, secret=get_settings().app_secret, ttl_seconds=60)
    client.cookies.clear()
    res = client.get(f"/api/public/publish-assets/{token}")
    assert res.status_code == 200
    assert res.headers["content-type"] == "video/mp4"
    assert res.content[4:8] == b"ftyp"
    assert client.get(f"/api/queue/{reel['id']}/reel-video").status_code in {401, 403}
