"""Instagram DB credentials: seal, save, refresh, clear. Never expose raw tokens."""

from __future__ import annotations

import pytest
from instamagic.config import get_settings
from instamagic.db import get_engine, reset_engine
from instamagic.main import create_app
from instamagic.models import Base
from instamagic.services.secret_box import open_sealed, seal
from fastapi.testclient import TestClient
from tests.conftest import login


def test_secret_box_roundtrip() -> None:
    secret = "test-secret-value-123456"
    sealed = seal("EAAB_super_secret_token_value", app_secret=secret)
    assert "EAAB" not in sealed
    assert open_sealed(sealed, app_secret=secret) == "EAAB_super_secret_token_value"


@pytest.fixture
def graph_client(tmp_path, monkeypatch: pytest.MonkeyPatch):
    database = tmp_path / "test.db"
    derivatives = tmp_path / "derivatives"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+pysqlite:///{database}")
    monkeypatch.setenv("DERIVATIVES_ROOT", str(derivatives))
    monkeypatch.setenv("ALLOWED_LIBRARY_ROOTS", str(tmp_path))
    monkeypatch.setenv("APP_PASSWORD", "test-password-123")
    monkeypatch.setenv("APP_SECRET", "test-secret-value-123456")
    monkeypatch.setenv("SAFETY_CLASSIFIER", "fake")
    monkeypatch.setenv("GEMINI_PROVIDER", "fake")
    monkeypatch.setenv("INSTAGRAM_PROVIDER", "graph")
    monkeypatch.setenv("INSTAGRAM_ACCESS_TOKEN", "")
    monkeypatch.setenv("INSTAGRAM_BUSINESS_ACCOUNT_ID", "")
    monkeypatch.setenv("PIPELINE_MODE", "inline")
    monkeypatch.setenv("GEMINI_IMAGE_POLICY", "text_only")
    monkeypatch.setenv("AUTO_PUBLISH", "false")
    monkeypatch.setenv("PUBLIC_BASE_URL", "http://testserver")
    get_settings.cache_clear()
    reset_engine()
    Base.metadata.create_all(get_engine())
    with TestClient(create_app()) as client:
        yield client
    reset_engine()
    get_settings.cache_clear()


def test_status_when_not_configured_shows_clear_flags(graph_client) -> None:
    login(graph_client)
    status = graph_client.get("/api/instagram/status").json()
    assert status["configured"] is False
    assert status["credential_source"] == "none"
    assert status["token_configured"] is False
    assert status["can_edit_database_credentials"] is True
    assert "access_token" not in status


def test_account_refresh_not_configured_returns_ok_false(graph_client) -> None:
    login(graph_client)
    response = graph_client.post("/api/instagram/account")
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert "not configured" in body["error"].lower()
    assert body["account"] is None


def test_save_credentials_and_refresh_with_fake_graph(graph_client, monkeypatch: pytest.MonkeyPatch) -> None:
    from instamagic.services.instagram import client as ig_client

    class StubGraph:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def configured(self) -> bool:
            return True

        def fetch_account_details(self):
            return ig_client.InstagramAccountDetails(
                id="17841400000000000",
                username="stage_photographer",
                name="Stage Photographer",
                followers_count=9,
                media_count=3,
                account_type="BUSINESS",
                linked_facebook_page_id="page-1",
            )

        def publish_image(self, **kwargs):
            raise AssertionError("publish must not run")

    monkeypatch.setattr(
        "instamagic.services.instagram.store.GraphInstagramClient",
        StubGraph,
    )
    login(graph_client)
    token = "EAAB_this_is_a_long_lived_page_token_xyz9"
    save = graph_client.post(
        "/api/instagram/credentials",
        json={"access_token": token, "business_account_id": "17841400000000000"},
    )
    assert save.status_code == 200, save.text
    body = save.json()
    assert body["ok"] is True
    assert body["configured"] is True
    assert body["credential_source"] == "database"
    assert body["token_masked"] == "••••…xyz9"
    assert body["account"]["username"] == "stage_photographer"
    assert token not in str(body)
    assert "EAAB" not in str(body)

    status = graph_client.get("/api/instagram/status").json()
    assert status["account"]["username"] == "stage_photographer"
    assert status["account_fetched_at"]

    refresh = graph_client.post("/api/instagram/account")
    assert refresh.status_code == 200
    assert refresh.json()["ok"] is True

    cleared = graph_client.delete("/api/instagram/credentials")
    assert cleared.status_code == 200
    after = cleared.json()
    assert after["configured"] is False
    assert after["credential_source"] == "none"


def test_oauth_start_requires_meta_app(graph_client) -> None:
    login(graph_client)
    response = graph_client.get("/api/instagram/oauth/start")
    assert response.status_code == 503
    assert "META_APP_ID" in response.json()["detail"]
