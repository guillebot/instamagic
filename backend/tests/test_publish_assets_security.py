"""Public publish-asset tokens: domain separation, path allowlist, HEAD."""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
from pathlib import Path
from uuid import uuid4

import pytest
from instamagic.auth import sign_payload, sign_session, verify_signed_payload
from instamagic.config import Settings
from instamagic.errors import NotAllowed
from instamagic.services.instagram.assets import (
    PublishAsset,
    is_allowed_publish_relative_path,
    mint_publish_token,
    parse_publish_token,
    resolve_publish_file,
)

_SECRET = "test-secret-value-123456"


def _publish_path(label: str = "feed") -> str:
    return f"{uuid4()}/publish/{label}.jpg"


def test_publish_path_allowlist() -> None:
    ok = _publish_path("from-crop")
    assert is_allowed_publish_relative_path(ok)
    assert not is_allowed_publish_relative_path("abc/preview.jpg")
    assert not is_allowed_publish_relative_path(f"{uuid4()}/preview/feed.jpg")
    assert not is_allowed_publish_relative_path(f"{uuid4()}/publish/feed.png")
    assert not is_allowed_publish_relative_path(f"{uuid4()}/../publish/feed.jpg")


def test_publish_token_roundtrip_opaque() -> None:
    relative = _publish_path()
    token = mint_publish_token(relative_path=relative, secret=_SECRET, ttl_seconds=900)
    assert token.isascii()
    assert all(ch.isalnum() or ch in "-_" for ch in token)
    assert len(token) >= 40
    padded = token + "=" * (-len(token) % 4)
    decoded = base64.urlsafe_b64decode(padded.encode())
    assert relative.encode() not in decoded
    asset = parse_publish_token(token, secret=_SECRET)
    assert asset.relative_path == relative


def test_publish_token_rejects_disallowed_path_on_mint() -> None:
    with pytest.raises(NotAllowed):
        mint_publish_token(relative_path="not-a-uuid/publish/x.jpg", secret=_SECRET)


def test_publish_token_not_valid_as_signed_oauth_payload() -> None:
    settings = Settings(
        app_password="test-password-123",
        app_secret=_SECRET,
        safety_classifier="fake",
        gemini_provider="fake",
        auto_publish=False,
    )
    token = mint_publish_token(relative_path=_publish_path(), secret=_SECRET)
    assert verify_signed_payload(token, settings) is None


def test_signed_payload_not_valid_as_publish_token() -> None:
    settings = Settings(
        app_password="test-password-123",
        app_secret=_SECRET,
        safety_classifier="fake",
        gemini_provider="fake",
        auto_publish=False,
    )
    oauth = sign_payload("ig-oauth|nonce-abc", settings)
    with pytest.raises(NotAllowed):
        parse_publish_token(oauth, secret=_SECRET)


def test_legacy_session_cookie_still_valid() -> None:
    settings = Settings(
        app_password="test-password-123",
        app_secret=_SECRET,
        safety_classifier="fake",
        gemini_provider="fake",
        auto_publish=False,
        session_ttl_seconds=3600,
    )
    payload = str(int(time.time()) + 3600)
    legacy_sig = hmac.new(_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    legacy_token = f"{payload}.{legacy_sig}"
    from instamagic.auth import session_is_valid

    assert session_is_valid(settings, legacy_token) is True
    assert session_is_valid(settings, sign_session(settings)) is True


def test_resolve_publish_file_enforces_allowlist(tmp_path: Path) -> None:
    root = tmp_path / "derivatives"
    root.mkdir()
    allowed = _publish_path()
    file_path = root / allowed
    file_path.parent.mkdir(parents=True)
    file_path.write_bytes(b"jpeg")
    asset = parse_publish_token(
        mint_publish_token(relative_path=allowed, secret=_SECRET),
        secret=_SECRET,
    )
    assert resolve_publish_file(root, asset) == file_path
    bad = PublishAsset(relative_path=f"{uuid4()}/preview/x.jpg", expires_at=int(time.time()) + 60)
    with pytest.raises(NotAllowed):
        resolve_publish_file(root, bad)


def test_public_publish_asset_head_and_get(client, app_env) -> None:
    derivatives: Path = app_env["derivatives"]
    relative = _publish_path()
    dest = derivatives / relative
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(b"\xff\xd8\xff\xd9")

    token = mint_publish_token(relative_path=relative, secret=_SECRET)
    head = client.head(f"/api/public/publish-assets/{token}")
    assert head.status_code == 200
    assert head.headers.get("content-type", "").startswith("image/jpeg")
    assert head.headers.get("content-length") == str(dest.stat().st_size)
    assert head.content == b""

    get = client.get(f"/api/public/publish-assets/{token}")
    assert get.status_code == 200
    assert get.content == b"\xff\xd8\xff\xd9"


def test_public_publish_asset_requires_valid_token(client) -> None:
    bad = "A" * 40
    assert client.get(f"/api/public/publish-assets/{bad}").status_code == 404
    assert client.head(f"/api/public/publish-assets/{bad}").status_code == 404


def test_public_publish_asset_expired_or_traversal_is_404(client, monkeypatch) -> None:
    import instamagic.services.instagram.assets as assets_mod

    rel = "11111111-2222-3333-4444-555555555555/publish/feed.jpg"
    token = mint_publish_token(relative_path=rel, secret=_SECRET)
    real_time = assets_mod.time.time
    monkeypatch.setattr(assets_mod.time, "time", lambda: real_time() + 3600)
    assert client.get(f"/api/public/publish-assets/{token}").status_code == 404
    for path in ("..%2f..%2fetc%2fpasswd", "%2e%2e/%2e%2e/" + "A" * 40, "A" * 39, "B" * 60):
        assert client.get(f"/api/public/publish-assets/{path}").status_code == 404
