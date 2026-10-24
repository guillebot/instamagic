"""GET /api/health/status — auth, shape, and no secret leakage."""

from __future__ import annotations

import json
import re

from instamagic.config import ATTESTATION_STATEMENT, get_settings
from instamagic.db import session_factory
from instamagic.services.health_status import (
    _queue_worker_alive,
    build_health_status,
    classify_publish_probe_status,
    publish_assets_probe_path,
    reset_health_cache,
)
from instamagic.services.instagram.client import InstagramAccountDetails
from instamagic.services.instagram.store import save_account_profile
from tests.conftest import login


def test_health_status_requires_session(client) -> None:
    assert client.get("/api/health/status").status_code == 401


def test_publish_probe_path_uses_valid_bogus_token() -> None:
    path = publish_assets_probe_path()
    assert path.startswith("/api/public/publish-assets/")
    token = path.rsplit("/", 1)[-1]
    assert len(token) == 60
    assert all(ch.isalnum() or ch in "-_" for ch in token)


def test_classify_publish_probe_status() -> None:
    assert classify_publish_probe_status(409) == (
        "ok",
        "publish-assets route reachable (token rejected)",
    )
    assert classify_publish_probe_status(404) == ("warning", "public route not reaching the app")
    assert classify_publish_probe_status(503)[0] == "down"
    assert classify_publish_probe_status(502)[0] == "down"


def test_queue_worker_ignores_stale_rq_without_heartbeat(monkeypatch) -> None:
    class FakeConn:
        pass

    monkeypatch.setattr("instamagic.services.health_status.heartbeat_fresh", lambda _c, _n: False)
    monkeypatch.setattr(
        "instamagic.services.health_status.Worker",
        type("W", (), {"all": staticmethod(lambda **_kwargs: [object(), object()])}),
    )
    alive, note = _queue_worker_alive(FakeConn(), "io")
    assert alive is False
    assert "stale" in note


def test_queue_worker_ok_when_heartbeat_fresh(monkeypatch) -> None:
    monkeypatch.setattr("instamagic.services.health_status.heartbeat_fresh", lambda _c, _n: True)
    alive, note = _queue_worker_alive(object(), "ml")
    assert alive is True
    assert "heartbeat" in note


def test_health_status_shape_and_no_secrets(client, app_env, monkeypatch) -> None:
    login(client)
    reset_health_cache()

    library = app_env["library"]
    created = client.post(
        "/api/libraries",
        json={
            "name": "Test",
            "root_path": str(library),
            "confirmed": True,
            "statement": ATTESTATION_STATEMENT,
        },
    )
    assert created.status_code == 201

    monkeypatch.setattr(
        "instamagic.services.health_status._check_queue",
        lambda _settings: type(
            "HC",
            (),
            {
                "id": "queue",
                "label": "Queue",
                "status": "ok",
                "detail": "stub",
            },
        )(),
    )

    response = client.get("/api/health/status")
    assert response.status_code == 200
    body = response.json()
    assert body["overall"] in {"ok", "warning", "down", "unknown"}
    assert isinstance(body["checks"], list) and len(body["checks"]) >= 5
    ids = {item["id"] for item in body["checks"]}
    assert "library" in ids
    assert "instagram" in ids
    assert "database" in ids

    raw = json.dumps(body)
    settings = get_settings()
    assert settings.app_secret not in raw
    assert settings.gemini_api_key not in raw or not settings.gemini_api_key
    assert "access_token" not in raw.lower()
    assert "ciphertext" not in raw.lower()
    assert not re.search(r"EAA[A-Za-z0-9]{20,}", raw)


def test_instagram_username_mismatch_is_down(client, monkeypatch) -> None:
    login(client)
    reset_health_cache()
    monkeypatch.setenv("INSTAGRAM_PROVIDER", "graph")
    monkeypatch.setenv("INSTAGRAM_EXPECTED_USERNAME", "foto.sch")
    get_settings.cache_clear()

    session = session_factory()()
    try:
        save_account_profile(
            session,
            InstagramAccountDetails(
                id="123",
                username="foto.sch",
                name="Cached OK",
                profile_picture_url=None,
                followers_count=1,
                follows_count=1,
                media_count=1,
            ),
        )
        from instamagic.services.instagram.store import save_credentials

        save_credentials(
            session, get_settings(), access_token="short-token", business_account_id="123"
        )
    finally:
        session.close()

    def fake_live_probe(_settings):
        return "other.user", None

    monkeypatch.setattr(
        "instamagic.services.health_status.probe_instagram_username_live", fake_live_probe
    )
    monkeypatch.setattr(
        "instamagic.services.health_status._check_queue",
        lambda _settings: type(
            "HC",
            (),
            {"id": "queue", "label": "Queue", "status": "ok", "detail": "stub"},
        )(),
    )

    payload = build_health_status(get_settings(), bypass_cache=True)
    ig = next(item for item in payload["checks"] if item["id"] == "instagram")
    assert ig["status"] == "down"
    assert "foto.sch" in ig["detail"]
    assert "other.user" in ig["detail"]
    get_settings.cache_clear()
    reset_health_cache()


def test_instagram_check_gets_longer_timeout_than_default(monkeypatch) -> None:
    import time as _time

    from instamagic.services import health_status as hs

    def slow_instagram(_settings):
        _time.sleep(hs._CHECK_TIMEOUT + 0.3)
        return hs.HealthCheck(
            id="instagram", label="Instagram", status="ok", detail="@foto.sch (live)"
        )

    slow_instagram.__name__ = "_check_instagram"
    result = hs._run_check(slow_instagram, None)
    assert result.status == "ok"
    assert result.detail == "@foto.sch (live)"
