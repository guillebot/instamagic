"""GET /api/health/status — auth, shape, and no secret leakage."""

from __future__ import annotations

import json
import re

from instamagic.config import ATTESTATION_STATEMENT, get_settings
from instamagic.db import session_factory
from instamagic.services.health_status import build_health_status, reset_health_cache
from instamagic.services.instagram.client import InstagramAccountDetails
from instamagic.services.instagram.store import save_account_profile
from tests.conftest import login


def test_health_status_requires_session(client) -> None:
    assert client.get("/api/health/status").status_code == 401


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
                username="other.user",
                name="Other",
                profile_picture_url=None,
                followers_count=1,
                follows_count=1,
                media_count=1,
            ),
        )
        from instamagic.services.instagram.store import save_credentials

        save_credentials(session, get_settings(), access_token="short-token", business_account_id="123")
    finally:
        session.close()

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
    get_settings.cache_clear()
