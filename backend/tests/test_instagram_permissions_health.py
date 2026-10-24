"""Page vs user token scopes and live Instagram health probes."""

from __future__ import annotations

import json

import pytest
from instamagic.config import get_settings
from instamagic.db import session_factory
from instamagic.errors import NotConfigured
from instamagic.services.health_status import (
    build_health_status,
    probe_instagram_username_live,
    reset_health_cache,
)
from instamagic.services.instagram.client import GraphInstagramClient, InstagramAccountDetails
from instamagic.services.instagram.insights_sync import (
    refresh_token_scopes,
    sync_instagram_insights,
)
from instamagic.services.instagram.permissions import FetchedScopes
from instamagic.services.instagram.store import (
    save_account_profile,
    save_credentials,
    save_meta_app_credentials,
)


def test_page_token_scopes_via_debug_token(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, dict | None]] = []

    def fake_graph_get(url, *, params, timeout, action):
        calls.append((url, params))
        if url.endswith("/debug_token"):
            return {
                "data": {
                    "scopes": ["pages_show_list"],
                    "granular_scopes": [
                        {"scope": "instagram_manage_insights"},
                        {"scope": "instagram_basic"},
                    ],
                }
            }
        raise AssertionError(url)

    monkeypatch.setattr("instamagic.services.instagram.client.graph_get_page", fake_graph_get)
    client = GraphInstagramClient(
        access_token="page-user-token",
        business_account_id="17841403337960568",
        meta_app_id="app123",
        meta_app_secret="secret456",
    )
    fetched = client.fetch_granted_permissions_detailed()
    assert fetched.determined is True
    assert fetched.scopes == frozenset(
        {"pages_show_list", "instagram_manage_insights", "instagram_basic"}
    )
    assert any(url.endswith("/debug_token") for url, _ in calls)
    assert not any("/me/permissions" in url for url, _ in calls)


def test_user_token_scopes_via_me_permissions(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_graph_get(url, *, params, timeout, action):
        if url.endswith("/me/permissions"):
            return {
                "data": [
                    {"permission": "instagram_manage_comments", "status": "granted"},
                    {"permission": "email", "status": "declined"},
                ]
            }
        raise AssertionError(url)

    monkeypatch.setattr("instamagic.services.instagram.client.graph_get_page", fake_graph_get)
    client = GraphInstagramClient(access_token="user-token", business_account_id="178414000")
    fetched = client.fetch_granted_permissions_detailed()
    assert fetched.determined is True
    assert fetched.scopes == frozenset({"instagram_manage_comments"})


def test_page_token_without_app_creds_is_scope_undetermined(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_graph_get(url, *, params, timeout, action):
        if url.endswith("/me/permissions"):
            raise NotConfigured(
                "Instagram fetch user token permissions failed: "
                "Tried accessing nonexisting field (permissions) (#100)"
            )
        raise AssertionError(url)

    monkeypatch.setattr("instamagic.services.instagram.client.graph_get_page", fake_graph_get)
    client = GraphInstagramClient(access_token="page-token", business_account_id="178414000")
    fetched = client.fetch_granted_permissions_detailed()
    assert fetched.determined is False
    assert fetched.scopes == frozenset()


def test_refresh_persists_debug_token_scopes(app_env, monkeypatch: pytest.MonkeyPatch) -> None:
    del app_env
    settings = get_settings()
    session = session_factory()()
    save_credentials(session, settings, access_token="page-token", business_account_id="178414000")
    save_meta_app_credentials(session, settings, app_id="app1", app_secret="sec1")

    def fake_detailed(self):
        return FetchedScopes(
            scopes=frozenset({"instagram_manage_insights", "instagram_basic"}),
            determined=True,
        )

    monkeypatch.setattr(GraphInstagramClient, "fetch_granted_permissions_detailed", fake_detailed)
    client = GraphInstagramClient(
        access_token="page-token",
        business_account_id="178414000",
        meta_app_id="app1",
        meta_app_secret="sec1",
    )
    fetched = refresh_token_scopes(session, client)
    assert "instagram_manage_insights" in fetched.scopes
    from instamagic.services.instagram.store import get_connection

    row = get_connection(session)
    assert row is not None
    assert json.loads(row.token_scopes_json or "[]") == [
        "instagram_basic",
        "instagram_manage_insights",
    ]
    session.close()


def test_sync_proceeds_when_scopes_undetermined(monkeypatch: pytest.MonkeyPatch, app_env) -> None:
    del app_env
    settings = get_settings()
    session = session_factory()()

    def fake_detailed(self):
        return FetchedScopes(scopes=frozenset(), determined=False)

    def fake_account_insights(self):
        return {
            "follower_count": 10,
            "reach": 1,
            "profile_views": 1,
            "impressions": None,
            "accounts_engaged": 0,
        }

    def fake_media(self, *, limit: int = 50):
        del limit
        return []

    monkeypatch.setattr(GraphInstagramClient, "fetch_granted_permissions_detailed", fake_detailed)
    monkeypatch.setattr(
        GraphInstagramClient, "fetch_account_insight_snapshot", fake_account_insights
    )
    monkeypatch.setattr(GraphInstagramClient, "list_recent_media", fake_media)

    client = GraphInstagramClient(access_token="page-token", business_account_id="178414000")
    result = sync_instagram_insights(session, settings, client, force=True)
    assert result["ok"] is True
    session.close()


def test_health_live_auth_failure_is_down(monkeypatch: pytest.MonkeyPatch, app_env) -> None:
    del app_env
    settings = get_settings()
    reset_health_cache()
    monkeypatch.setenv("INSTAGRAM_PROVIDER", "graph")
    monkeypatch.setenv("INSTAGRAM_EXPECTED_USERNAME", "foto.sch")
    get_settings.cache_clear()

    session = session_factory()()
    try:
        save_credentials(
            session, settings, access_token="bad-token", business_account_id="178414000"
        )
        save_account_profile(
            session,
            InstagramAccountDetails(id="178414000", username="foto.sch", name="Cached"),
        )
    finally:
        session.close()

    def fake_live(self):
        raise NotConfigured(
            "Instagram probe account username failed: Error validating access token (#190)"
        )

    monkeypatch.setattr(GraphInstagramClient, "fetch_live_username", fake_live)

    username, auth = probe_instagram_username_live(get_settings())
    assert username is None
    assert auth is not None
    assert "expired" in auth.lower() or "invalid" in auth.lower()

    payload = build_health_status(get_settings(), bypass_cache=True)
    ig = next(item for item in payload["checks"] if item["id"] == "instagram")
    assert ig["status"] == "down"
    assert "token" in ig["detail"].lower()
    get_settings.cache_clear()
    reset_health_cache()
