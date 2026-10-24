"""Scheduled posting: idempotency, gate re-check, content limit backoff."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from instamagic.config import get_settings
from instamagic.models import AdultAttestation, PublishAttempt
from instamagic.services.instagram.client import FakeInstagramClient
from instamagic.services.posting.scheduler import content_limit_near_cap, run_scheduled_publish_tick
from instamagic.services.recommendations.gates import publishing_gate
from sqlalchemy import func, select
from tests.conftest import login

ATTESTATION = "I confirm that every person depicted in this library is a consenting adult."


def _setup_approved_item(client, app_env) -> tuple[str, str, str]:
    library: Path = app_env["library"]
    from PIL import Image, ImageDraw

    path = library / "session" / "clean.jpg"
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (400, 600), (30, 30, 30))
    ImageDraw.Draw(image).rectangle((20, 20, 80, 80), fill=(230, 230, 230))
    image.save(path, format="JPEG", quality=90)
    login(client)
    created = client.post(
        "/api/libraries",
        json={"name": "Archive", "root_path": str(library), "confirmed": True, "statement": ATTESTATION},
    )
    library_id = created.json()["id"]
    assert client.post(f"/api/libraries/{library_id}/scan").status_code == 202
    image_id = client.get("/api/images", params={"library_id": library_id}).json()["items"][0]["id"]
    client.post(f"/api/images/{image_id}/rating", json={"rating": "excellent"})
    queued = client.post("/api/queue", json={"image_id": image_id})
    item_id = queued.json()["id"]
    client.post(f"/api/queue/{item_id}/approve")
    captions = client.post(f"/api/images/{image_id}/captions", json={"confirmed": True})
    caption_id = captions.json()[0]["id"]
    return item_id, caption_id, image_id


def test_gate_revalidated_at_publish_time(client, app_env, monkeypatch) -> None:
    item_id, caption_id, image_id = _setup_approved_item(client, app_env)
    # Remove attestation after approval
    from instamagic.db import session_factory

    session = session_factory()()
    try:
        from sqlalchemy import delete

        session.execute(delete(AdultAttestation))
        session.commit()
    finally:
        session.close()
    resp = client.post(f"/api/queue/{item_id}/publish", json={"caption_id": caption_id})
    assert resp.status_code == 409
    assert "attestation" in resp.json()["detail"].lower()


def test_schedule_and_unschedule(client, app_env) -> None:
    item_id, caption_id, _ = _setup_approved_item(client, app_env)
    when = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    scheduled = client.post(
        f"/api/queue/{item_id}/schedule",
        json={"mode": "explicit", "scheduled_at": when, "caption_id": caption_id},
    )
    assert scheduled.status_code == 200, scheduled.text
    body = scheduled.json()
    assert body["schedule_mode"] == "explicit"
    assert body["scheduled_at"] is not None
    upcoming = client.get("/api/posting-schedule/upcoming").json()
    assert len(upcoming) == 1
    unscheduled = client.post(f"/api/queue/{item_id}/unschedule")
    assert unscheduled.status_code == 200
    assert unscheduled.json()["scheduled_at"] is None


def test_scheduler_idempotent_single_publish(client, app_env) -> None:
    item_id, caption_id, _ = _setup_approved_item(client, app_env)
    past = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
    client.post(
        f"/api/queue/{item_id}/schedule",
        json={"mode": "explicit", "scheduled_at": past, "caption_id": caption_id},
    )
    from instamagic.db import session_factory
    settings = get_settings()
    fake = FakeInstagramClient()
    session = session_factory()()
    try:
        count = run_scheduled_publish_tick(session, settings, fake)
        assert count == 1
        count2 = run_scheduled_publish_tick(session, settings, fake)
        assert count2 == 0
        attempts = session.scalar(select(func.count()).select_from(PublishAttempt))
        assert int(attempts or 0) == 1
    finally:
        session.close()


def test_content_limit_backoff() -> None:
    client = FakeInstagramClient()
    client.content_limit_usage = 49
    assert content_limit_near_cap(client) is True
    client.content_limit_usage = 10
    assert content_limit_near_cap(client) is False


def test_publishing_gate_blocks_do_not_post() -> None:
    gate = publishing_gate(
        attested=True,
        human_rating="safe",
        model_state="DO_NOT_POST",
        needs_crop=False,
        has_approved_crop=False,
    )
    assert gate.allowed is False
