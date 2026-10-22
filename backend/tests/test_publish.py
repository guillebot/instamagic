"""Human-gated Instagram publish gates and fake Graph provider."""

from __future__ import annotations

from pathlib import Path

from instamagic.config import AUTO_PUBLISH_ACK_PHRASE, Settings
from instamagic.services.instagram.assets import mint_publish_token, parse_publish_token
from instamagic.services.instagram.client import FakeInstagramClient
from PIL import Image, ImageDraw
from pydantic import ValidationError
from tests.conftest import login

ATTESTATION = "I confirm that every person depicted in this library is a consenting adult."


def _marked(path: Path, color: tuple[int, int, int], box: tuple[int, int, int, int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (400, 600), color)
    ImageDraw.Draw(image).rectangle(box, fill=(230, 230, 230))
    image.save(path, format="JPEG", quality=90)


def _attested_library(client, library: Path) -> str:
    created = client.post(
        "/api/libraries",
        json={"name": "Archive", "root_path": str(library), "confirmed": True, "statement": ATTESTATION},
    )
    assert created.status_code == 201, created.text
    library_id = created.json()["id"]
    assert client.post(f"/api/libraries/{library_id}/scan").status_code == 202
    return library_id


def test_auto_publish_requires_ack_phrase() -> None:
    try:
        Settings(
            app_password="test-password-123",
            app_secret="test-secret-value-123456",
            safety_classifier="fake",
            gemini_provider="fake",
            instagram_provider="fake",
            auto_publish=True,
            auto_publish_ack="",
            environment="local",
            bind_host="127.0.0.1",
        )
        raise AssertionError("expected ValidationError")
    except ValidationError as exc:
        assert "AUTO_PUBLISH" in str(exc)
    ok = Settings(
        app_password="test-password-123",
        app_secret="test-secret-value-123456",
        safety_classifier="fake",
        gemini_provider="fake",
        instagram_provider="fake",
        auto_publish=True,
        auto_publish_ack=AUTO_PUBLISH_ACK_PHRASE,
        environment="local",
        bind_host="127.0.0.1",
    )
    assert ok.auto_publish is True


def test_publish_token_roundtrip() -> None:
    token = mint_publish_token(relative_path="abc/preview.jpg", secret="test-secret-value-123456", ttl_seconds=120)
    asset = parse_publish_token(token, secret="test-secret-value-123456")
    assert asset.relative_path == "abc/preview.jpg"


def test_do_not_post_cannot_publish(client, app_env) -> None:
    library: Path = app_env["library"]
    _marked(library / "session" / "blocked-frame.jpg", (40, 40, 90), (40, 300, 180, 420))
    login(client)
    library_id = _attested_library(client, library)
    items = client.get("/api/images", params={"library_id": library_id}).json()["items"]
    blocked = next(item for item in items if item["model_state"] == "DO_NOT_POST")
    client.post(f"/api/images/{blocked['id']}/rating", json={"rating": "safe"})
    assert client.post("/api/queue", json={"image_id": blocked["id"]}).status_code == 409


def test_unattested_cannot_publish_via_queue(client, app_env) -> None:
    library: Path = app_env["library"]
    _marked(library / "session" / "clean.jpg", (30, 30, 30), (20, 20, 80, 80))
    login(client)
    created = client.post(
        "/api/libraries",
        json={"name": "NoAttest", "root_path": str(library), "confirmed": False, "statement": ""},
    )
    library_id = created.json()["id"]
    assert client.post(f"/api/libraries/{library_id}/scan").status_code == 202
    image_id = client.get("/api/images", params={"library_id": library_id}).json()["items"][0]["id"]
    client.post(f"/api/images/{image_id}/rating", json={"rating": "excellent"})
    assert client.post("/api/queue", json={"image_id": image_id}).status_code == 409


def test_approved_queue_publishes_with_fake_instagram(client, app_env) -> None:
    library: Path = app_env["library"]
    _marked(library / "session" / "clean.jpg", (30, 30, 30), (20, 20, 80, 80))
    login(client)
    library_id = _attested_library(client, library)
    image_id = client.get("/api/images", params={"library_id": library_id}).json()["items"][0]["id"]
    client.post(f"/api/images/{image_id}/rating", json={"rating": "excellent"})
    queued = client.post("/api/queue", json={"image_id": image_id})
    assert queued.status_code == 201, queued.text
    item_id = queued.json()["id"]
    assert client.post(f"/api/queue/{item_id}/approve").status_code == 200
    captions = client.post(f"/api/images/{image_id}/captions", json={"confirmed": True})
    assert captions.status_code == 201, captions.text
    caption_id = captions.json()[0]["id"]
    published = client.post(f"/api/queue/{item_id}/publish", json={"caption_id": caption_id})
    assert published.status_code == 200, published.text
    body = published.json()
    assert body["state"] == "PUBLISHED"
    assert body["ig_media_id"]
    assert body["permalink"]
    status = client.get("/api/instagram/status").json()
    assert status["configured"] is True
    assert status["auto_publish"] is False


def test_fake_instagram_client_records_calls() -> None:
    client = FakeInstagramClient()
    result = client.publish_image(image_url="https://example.test/a.jpg", caption="Quiet light.")
    assert result.media_id.startswith("fake-media-")
    assert client.calls[0]["caption"] == "Quiet light."


def test_related_accounts_crud_and_comment_drafts(client, app_env) -> None:
    login(client)
    created = client.post(
        "/api/related-accounts",
        json={
            "handle": "@LightStudy",
            "why_relevant": "Soft window light",
            "notes": "Pointer: study captions that name the light, not the body.",
        },
    )
    assert created.status_code == 201, created.text
    account_id = created.json()["id"]
    assert created.json()["handle"] == "lightstudy"
    listed = client.get("/api/related-accounts").json()
    assert len(listed) == 1
    drafts = client.post(
        "/api/comment-drafts",
        json={"related_account_id": account_id, "target_context": "Their latest window portrait", "confirmed": True},
    )
    assert drafts.status_code == 201, drafts.text
    assert len(drafts.json()) >= 1
    assert client.delete(f"/api/related-accounts/{account_id}").status_code == 204
