"""AUTH_DISABLED (LAN no-password) gates, spoofed headers, and 30-day sliding sessions."""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient
from instamagic.auth import lan_auth_bypass, session_should_renew, sign_session
from instamagic.config import Settings, get_settings
from instamagic.main import create_app

LAN_HOST = "instamagic.schimmel.homes"
PUBLIC_HOST = "instamagic.schimmel.com.ar"
NGINX_PEER = ("172.22.0.8", 40000)
LAN_IP = {"X-Real-IP": "10.1.1.150", "X-Forwarded-For": "10.1.1.150"}


def _env(monkeypatch, *, disabled: str = "true") -> None:
    monkeypatch.setenv("FRONTEND_ORIGIN", f"https://{LAN_HOST}")
    monkeypatch.setenv("PUBLIC_BASE_URL", f"https://{PUBLIC_HOST}")
    monkeypatch.setenv("AUTH_DISABLED", disabled)
    get_settings.cache_clear()


@pytest.fixture
def nginx_client(app_env, monkeypatch):
    _env(monkeypatch)
    with TestClient(create_app(), client=NGINX_PEER) as c:
        yield c
    get_settings.cache_clear()


def _get(c, *, host=LAN_HOST, headers=None, path="/api/auth/session"):
    merged = {"host": host}
    merged.update(headers or {})
    return c.get(path, headers=merged)


def test_lan_request_needs_no_password(nginx_client) -> None:
    assert _get(nginx_client, headers=LAN_IP).status_code == 200
    assert _get(nginx_client, headers=LAN_IP, path="/api/queue").status_code == 200
    assert _get(nginx_client, headers={**LAN_IP, "X-Forwarded-Host": LAN_HOST}).status_code == 200


@pytest.mark.parametrize(
    "host",
    [
        PUBLIC_HOST,
        f"{PUBLIC_HOST}:443",
        "186.182.15.22",
        "",
        "evil.example",
        f"{LAN_HOST}.evil.example",
    ],
)
def test_non_lan_host_still_requires_login(nginx_client, host) -> None:
    assert _get(nginx_client, host=host, headers=LAN_IP).status_code == 401


@pytest.mark.parametrize(
    "headers",
    [
        {"X-Real-IP": "8.8.8.8", "X-Forwarded-For": "8.8.8.8"},  # internet client via Traefik
        {"X-Real-IP": "192.168.112.5", "X-Forwarded-For": "192.168.112.5"},  # docker bridge
        {"X-Real-IP": "10.1.1.150", "X-Forwarded-For": "10.1.1.150, 8.8.8.8"},  # chain
        {"X-Real-IP": "10.1.1.150", "X-Forwarded-For": "8.8.8.8"},  # mismatch
        {"X-Forwarded-For": "10.1.1.150"},  # spoofed XFF only
        {"X-Real-IP": "10.1.1.150, 10.1.1.150"},
        {"X-Real-IP": "garbage"},
        {**LAN_IP, "X-Forwarded-Host": PUBLIC_HOST},
        {},
    ],
)
def test_spoofed_or_non_lan_client_requires_login(nginx_client, headers) -> None:
    assert _get(nginx_client, headers=headers).status_code == 401


def test_public_peer_never_bypasses(app_env, monkeypatch) -> None:
    _env(monkeypatch)
    with TestClient(create_app(), client=("9.9.9.9", 5555)) as c:
        assert _get(c, headers=LAN_IP).status_code == 401
    get_settings.cache_clear()


def test_off_by_default(app_env, monkeypatch) -> None:
    _env(monkeypatch, disabled="false")
    with TestClient(create_app(), client=NGINX_PEER) as c:
        assert _get(c, headers=LAN_IP).status_code == 401
    get_settings.cache_clear()


def test_public_host_excluded_even_if_listed(app_env, monkeypatch) -> None:
    _env(monkeypatch)
    monkeypatch.setenv("LAN_UI_HOSTS", f"{LAN_HOST},{PUBLIC_HOST}")
    get_settings.cache_clear()
    assert PUBLIC_HOST not in get_settings().lan_ui_host_set()
    get_settings.cache_clear()


def test_public_paths_never_bypass(app_env, monkeypatch) -> None:
    _env(monkeypatch)

    class _Url:
        path = "/api/public/publish-assets/abc"

    class _Client:
        host = "172.22.0.8"

    class _Req:
        url = _Url()
        client = _Client()
        headers = {"host": LAN_HOST, **{k.lower(): v for k, v in LAN_IP.items()}}

    assert lan_auth_bypass(_Req(), get_settings()) is False  # type: ignore[arg-type]
    get_settings.cache_clear()


def test_public_networks_rejected(app_env, monkeypatch) -> None:
    for bad in ("0.0.0.0/0", "8.8.8.0/24", "10.0.0.0/8"):
        monkeypatch.setenv("AUTH_DISABLED_NETWORKS", bad)
        with pytest.raises(ValueError):
            Settings()


def test_session_lasts_30_days_and_slides(app_env) -> None:
    settings = get_settings()
    assert settings.session_ttl_seconds == 30 * 24 * 3600
    token = sign_session(settings)
    assert not session_should_renew(settings, token)
    assert session_should_renew(
        settings, token, now=time.time() + settings.session_renew_after_seconds + 5
    )
    assert not session_should_renew(settings, "garbage.sig")


def test_login_cookie_max_age_and_sliding_renewal(client, monkeypatch) -> None:
    r = client.post("/api/auth/login", json={"password": "test-password-123"})
    assert f"max-age={30 * 24 * 3600}" in r.headers["set-cookie"].lower()
    assert "set-cookie" not in client.get("/api/auth/session").headers
    import instamagic.auth as auth_mod

    real_time = auth_mod.time.time
    monkeypatch.setattr(auth_mod.time, "time", lambda: real_time() + 7 * 3600)
    renewed = client.get("/api/auth/session")
    assert renewed.status_code == 200
    assert "instamagic_session=" in renewed.headers.get("set-cookie", "")
