"""Instagram connection helper: status masking + fake Graph account fetch."""

from __future__ import annotations

from instamagic.services.instagram.client import (
    FakeInstagramClient,
    NotConfiguredInstagramClient,
    mask_access_token,
)
from tests.conftest import login


def test_mask_access_token_never_returns_full_secret() -> None:
    assert mask_access_token("") is None
    assert mask_access_token("short") == "••••••••"
    masked = mask_access_token("EAAB_this_is_a_long_token_value_xyz9")
    assert masked == "••••…xyz9"
    assert "EAAB" not in masked
    assert "token" not in masked


def test_fake_instagram_fetch_account_details() -> None:
    client = FakeInstagramClient()
    details = client.fetch_account_details()
    assert details.username == "instamagic_fixture"
    assert details.account_type == "BUSINESS"
    assert details.linked_facebook_page_id == "fake-page-001"
    assert client.account_fetches == 1


def test_not_configured_fetch_raises() -> None:
    from instamagic.errors import InstagramNotConfigured

    client = NotConfiguredInstagramClient()
    try:
        client.fetch_account_details()
        raise AssertionError("expected InstagramNotConfigured")
    except InstagramNotConfigured as exc:
        assert "INSTAGRAM_ACCESS_TOKEN" in exc.detail


def test_instagram_status_masks_and_lists_ids(client) -> None:
    login(client)
    status = client.get("/api/instagram/status").json()
    assert status["configured"] is True
    assert status["provider"] == "fake"
    assert status["credential_source"] == "fake"
    assert status["token_configured"] is True
    assert status["token_masked"] == "fake-provider"
    assert status["auto_publish"] is False
    assert status["can_edit_database_credentials"] is False
    assert "access_token" not in status
    raw = str(status)
    assert "EAAB" not in raw


def test_instagram_account_refresh_with_fake_provider(client) -> None:
    login(client)
    response = client.post("/api/instagram/account")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True
    assert body["error"] is None
    assert body["account"]["username"] == "instamagic_fixture"
    assert body["account"]["followers_count"] == 128
    assert body["account"]["id"] == "fake-ig-user-001"
    assert body["token_masked"] == "fake-provider"
    # Response must never include a raw Graph token field.
    assert "access_token" not in body
    blob = str(body)
    assert "EAAB" not in blob
    # Cached profile should round-trip on status.
    status = client.get("/api/instagram/status").json()
    assert status["account"]["username"] == "instamagic_fixture"


def test_instagram_account_requires_auth(client) -> None:
    assert client.get("/api/instagram/status").status_code == 401
    assert client.post("/api/instagram/account").status_code == 401
    assert client.post("/api/instagram/credentials", json={}).status_code == 401
