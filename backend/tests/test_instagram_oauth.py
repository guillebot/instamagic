"""Meta OAuth: authorize URL, CSRF state, redirect URI, callback seal. Never assert raw secrets in logs."""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient
from instamagic.auth import sign_payload
from instamagic.config import Settings, get_settings
from instamagic.db import get_engine, reset_engine
from instamagic.main import create_app
from instamagic.models import Base
from instamagic.services.instagram.oauth import (
    OAUTH_SCOPES,
    OAuthTokens,
    build_authorize_url,
    exchange_code_for_credentials,
    oauth_redirect_uri,
    verify_oauth_state,
)
from tests.conftest import login


def _graph_env(tmp_path, monkeypatch: pytest.MonkeyPatch, *, meta: bool = False) -> None:
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
    # Keep http so TestClient session cookies are not Secure-only.
    monkeypatch.setenv("FRONTEND_ORIGIN", "http://testserver")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://tunnel.example.invalid")
    if meta:
        monkeypatch.setenv("META_APP_ID", "app-id-123")
        monkeypatch.setenv("META_APP_SECRET", "app-secret-456")
    else:
        monkeypatch.setenv("META_APP_ID", "")
        monkeypatch.setenv("META_APP_SECRET", "")


@pytest.fixture
def graph_client(tmp_path, monkeypatch: pytest.MonkeyPatch):
    _graph_env(tmp_path, monkeypatch, meta=False)
    get_settings.cache_clear()
    reset_engine()
    Base.metadata.create_all(get_engine())
    with TestClient(create_app()) as client:
        yield client
    reset_engine()
    get_settings.cache_clear()


@pytest.fixture
def meta_client(tmp_path, monkeypatch: pytest.MonkeyPatch):
    _graph_env(tmp_path, monkeypatch, meta=True)
    get_settings.cache_clear()
    reset_engine()
    Base.metadata.create_all(get_engine())
    with TestClient(create_app()) as client:
        yield client
    reset_engine()
    get_settings.cache_clear()


def _settings(**overrides: str) -> Settings:
    get_settings.cache_clear()
    base = {
        "app_password": "test-password-123",
        "app_secret": "test-secret-value-123456",
        "frontend_origin": "https://instamagic.schimmel.homes",
        "public_base_url": "https://tunnel.example.invalid",
        "meta_app_id": "app-id-123",
        "meta_app_secret": "app-secret-456",
        "instagram_graph_version": "v21.0",
    }
    base.update(overrides)
    return Settings(**base)


def test_oauth_redirect_uses_frontend_origin_not_public_base() -> None:
    settings = _settings()
    assert oauth_redirect_uri(settings) == (
        "https://instamagic.schimmel.homes/api/instagram/oauth/callback"
    )


def test_oauth_redirect_explicit_override() -> None:
    settings = _settings(meta_oauth_redirect_uri="https://custom.example/api/instagram/oauth/callback")
    assert oauth_redirect_uri(settings) == "https://custom.example/api/instagram/oauth/callback"


def test_build_authorize_url_includes_signed_state_and_scopes() -> None:
    settings = _settings()
    url, state = build_authorize_url(settings)
    parsed = urlparse(url)
    assert parsed.netloc == "www.facebook.com"
    qs = parse_qs(parsed.query)
    assert qs["client_id"] == ["app-id-123"]
    assert qs["redirect_uri"] == [
        "https://instamagic.schimmel.homes/api/instagram/oauth/callback"
    ]
    assert qs["scope"] == [OAUTH_SCOPES]
    assert qs["response_type"] == ["code"]
    assert qs["state"] == [state]
    verify_oauth_state(state, settings)
    assert "app-secret" not in url.lower()


def test_oauth_start_returns_authorize_url(meta_client) -> None:
    login(meta_client)
    response = meta_client.get("/api/instagram/oauth/start")
    assert response.status_code == 200, response.text
    body = response.json()
    assert "facebook.com" in body["authorize_url"]
    assert body["redirect_uri"].endswith("/api/instagram/oauth/callback")
    assert "app-secret" not in str(body).lower()


def test_status_exposes_oauth_setup_fields_without_secrets(graph_client) -> None:
    login(graph_client)
    response = graph_client.get("/api/instagram/status")
    assert response.status_code == 200, response.text
    status = response.json()
    assert status["oauth_available"] is False
    assert status["oauth_redirect_uri"].endswith("/api/instagram/oauth/callback")
    assert "instagram_content_publish" in status["oauth_scopes"]
    assert status["meta_docs_url"].startswith("https://developers.facebook.com/")
    assert "META_APP_SECRET" not in str(status)
    assert "access_token" not in status


def test_oauth_callback_seals_credentials(meta_client, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = get_settings()
    state = sign_payload("ig-oauth|nonce", settings)

    def fake_exchange(settings_arg, *, code: str) -> OAuthTokens:
        assert code == "meta-code"
        return OAuthTokens(
            access_token="EAAB_page_token_from_oauth_xyz9",
            business_account_id="17841400000000000",
            page_id="page-99",
        )

    class StubGraph:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def configured(self) -> bool:
            return True

        def fetch_account_details(self):
            from instamagic.services.instagram import client as ig_client

            return ig_client.InstagramAccountDetails(
                id="17841400000000000",
                username="oauth_photographer",
                name="OAuth Photographer",
                followers_count=2,
                media_count=1,
                account_type="BUSINESS",
                linked_facebook_page_id="page-99",
            )

        def publish_image(self, **kwargs):
            raise AssertionError("publish must not run")

    monkeypatch.setattr(
        "instamagic.api.routes.exchange_code_for_credentials",
        fake_exchange,
    )
    monkeypatch.setattr(
        "instamagic.services.instagram.store.GraphInstagramClient",
        StubGraph,
    )

    response = meta_client.get(
        "/api/instagram/oauth/callback",
        params={"code": "meta-code", "state": state},
        follow_redirects=False,
    )
    assert response.status_code == 302
    loc = response.headers["location"]
    assert "oauth=ok" in loc
    assert "EAAB" not in loc

    login(meta_client)
    status_resp = meta_client.get("/api/instagram/status")
    assert status_resp.status_code == 200, status_resp.text
    status = status_resp.json()
    assert status["configured"] is True
    assert status["credential_source"] == "database"
    assert status["token_masked"] == "••••…xyz9"
    assert status["account"]["username"] == "oauth_photographer"
    assert "EAAB" not in str(status)


def test_oauth_callback_rejects_bad_state(meta_client, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*args, **kwargs):
        raise AssertionError("exchange must not run with bad state")

    monkeypatch.setattr("instamagic.api.routes.exchange_code_for_credentials", boom)
    response = meta_client.get(
        "/api/instagram/oauth/callback",
        params={"code": "meta-code", "state": "not-a-valid-state"},
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert "oauth=error" in response.headers["location"]


def test_exchange_discovers_ig_business_account(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings()
    calls: list[str] = []

    class FakeResponse:
        def __init__(self, payload: dict, status_code: int = 200):
            self._payload = payload
            self.status_code = status_code

        def json(self) -> dict:
            return self._payload

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, url: str, params: dict | None = None):
            calls.append(url)
            if url.endswith("/oauth/access_token") and params and params.get("code"):
                return FakeResponse({"access_token": "short-user-token"})
            if url.endswith("/oauth/access_token") and params and params.get("grant_type"):
                return FakeResponse({"access_token": "long-user-token"})
            if url.endswith("/me/accounts"):
                assert params and params.get("access_token") == "long-user-token"
                return FakeResponse(
                    {
                        "data": [
                            {
                                "id": "page-1",
                                "name": "Studio",
                                "access_token": "EAAB_page_token_abc",
                                "instagram_business_account": {"id": "178414999"},
                            }
                        ]
                    }
                )
            raise AssertionError(f"unexpected url {url}")

    monkeypatch.setattr("instamagic.services.instagram.oauth.httpx.Client", FakeClient)
    tokens = exchange_code_for_credentials(settings, code="auth-code")
    assert tokens.business_account_id == "178414999"
    assert tokens.access_token == "EAAB_page_token_abc"
    assert tokens.page_id == "page-1"
    assert any("oauth/access_token" in c for c in calls)
