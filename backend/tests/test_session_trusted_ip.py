"""Long-lived sliding sessions and trusted-IP (LAN) auto-login, incl. spoofed headers."""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient
from instamagic.auth import session_should_renew, sign_session, trusted_login_ip
from instamagic.config import Settings, get_settings
from instamagic.main import create_app

LAN_HOST = "instamagic.schimmel.homes"
PUBLIC_HOST = "instamagic.schimmel.com.ar"
NGINX_PEER = ("172.22.0.8", 40000)


@pytest.fixture
def trusted_env(app_env, monkeypatch):
    monkeypatch.setenv("FRONTEND_ORIGIN", f"https://{LAN_HOST}")
    monkeypatch.setenv("TRUSTED_LOGIN_IPS", "10.1.1.150")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def nginx_client(trusted_env):
    """TestClient whose TCP peer looks like the in-stack nginx container."""
    with TestClient(create_app(), client=NGINX_PEER) as c:
        yield c


def _get(c: TestClient, *, host: str = LAN_HOST, headers: dict[str, str] | None = None, path="/api/auth/session"):
    merged = {"host": host}
    merged.update(headers or {})
    return c.get(path, headers=merged)


def test_trusted_ip_from_nginx_is_logged_in(nginx_client) -> None:
    r = _get(nginx_client, headers={"X-Real-IP": "10.1.1.150", "X-Forwarded-For": "10.1.1.150"})
    assert r.status_code == 200
    assert _get(nginx_client, headers={"X-Real-IP": "10.1.1.150"}, path="/api/queue").status_code == 200
    # No cookie is minted for trusted requests.
    assert "set-cookie" not in r.headers


def test_untrusted_lan_ip_still_needs_password(nginx_client) -> None:
    assert _get(nginx_client, headers={"X-Real-IP": "10.1.1.152", "X-Forwarded-For": "10.1.1.152"}).status_code == 401


def test_public_host_never_auto_logs_in(nginx_client) -> None:
    r = _get(nginx_client, host=PUBLIC_HOST, headers={"X-Real-IP": "10.1.1.150", "X-Forwarded-For": "10.1.1.150"})
    assert r.status_code == 401
    r = _get(nginx_client, host=f"{PUBLIC_HOST}:443", headers={"X-Real-IP": "10.1.1.150"})
    assert r.status_code == 401


@pytest.mark.parametrize(
    "headers",
    [
        # Spoofed chain: nginx overwrites XFF with a single IP; any chain is refused.
        {"X-Real-IP": "10.1.1.150", "X-Forwarded-For": "10.1.1.150, 203.0.113.9"},
        {"X-Real-IP": "10.1.1.150", "X-Forwarded-For": "203.0.113.9"},
        # Only a spoofed XFF and no X-Real-IP from nginx.
        {"X-Forwarded-For": "10.1.1.150"},
        # Multiple values / garbage in X-Real-IP.
        {"X-Real-IP": "10.1.1.150, 10.1.1.150"},
        {"X-Real-IP": "not-an-ip"},
        {"X-Real-IP": "10.1.1.1500"},
        {},
    ],
)
def test_spoofed_or_malformed_headers_are_refused(nginx_client, headers) -> None:
    assert _get(nginx_client, headers=headers).status_code == 401


def test_public_peer_cannot_spoof_trusted_ip(trusted_env) -> None:
    # A request reaching the backend directly from a public address must never be trusted,
    # no matter which headers it sends.
    with TestClient(create_app(), client=("9.9.9.9", 5555)) as c:
        r = _get(c, headers={"X-Real-IP": "10.1.1.150", "X-Forwarded-For": "10.1.1.150"})
        assert r.status_code == 401


def test_feature_off_when_unset(app_env, monkeypatch) -> None:
    monkeypatch.setenv("FRONTEND_ORIGIN", f"https://{LAN_HOST}")
    monkeypatch.delenv("TRUSTED_LOGIN_IPS", raising=False)
    get_settings.cache_clear()
    with TestClient(create_app(), client=NGINX_PEER) as c:
        assert _get(c, headers={"X-Real-IP": "10.1.1.150"}).status_code == 401
    get_settings.cache_clear()


def test_public_paths_never_trusted(trusted_env) -> None:
    class _Url:
        path = "/api/public/publish-assets/abc"

    class _Client:
        host = "172.22.0.8"

    class _Req:
        url = _Url()
        client = _Client()
        headers = {"host": LAN_HOST, "x-real-ip": "10.1.1.150"}

    assert trusted_login_ip(_Req(), get_settings()) is None  # type: ignore[arg-type]


def test_public_cidrs_rejected(app_env, monkeypatch) -> None:
    monkeypatch.setenv("TRUSTED_LOGIN_IPS", "0.0.0.0/0")
    with pytest.raises(ValueError):
        Settings()
    monkeypatch.setenv("TRUSTED_LOGIN_IPS", "8.8.8.8")
    with pytest.raises(ValueError):
        Settings()
    monkeypatch.setenv("TRUSTED_LOGIN_IPS", "10.0.0.0/8")
    with pytest.raises(ValueError):
        Settings()


def test_session_lasts_30_days_and_slides(app_env) -> None:
    settings = get_settings()
    assert settings.session_ttl_seconds == 30 * 24 * 3600
    token = sign_session(settings)
    assert not session_should_renew(settings, token)
    later = time.time() + settings.session_renew_after_seconds + 5
    assert session_should_renew(settings, token, now=later)
    assert not session_should_renew(settings, "garbage.sig")


def test_login_cookie_max_age_and_sliding_renewal(client, monkeypatch) -> None:
    r = client.post("/api/auth/login", json={"password": "test-password-123"})
    assert r.status_code == 200
    assert f"max-age={30 * 24 * 3600}" in r.headers["set-cookie"].lower()
    # Fresh cookie: no renewal on normal requests.
    assert "set-cookie" not in client.get("/api/auth/session").headers
    # Pretend the cookie is old: the next authenticated request re-issues it.
    import instamagic.auth as auth_mod

    real_time = auth_mod.time.time
    monkeypatch.setattr(auth_mod.time, "time", lambda: real_time() + 7 * 3600)
    renewed = client.get("/api/auth/session")
    assert renewed.status_code == 200
    assert "instamagic_session=" in renewed.headers.get("set-cookie", "")
    # Logout must not be undone by renewal.
    out = client.post("/api/auth/logout")
    assert 'instamagic_session=""' in out.headers.get("set-cookie", "") or "max-age=0" in out.headers.get("set-cookie", "").lower()
