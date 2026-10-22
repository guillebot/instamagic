"""Auth, session cookie, and login abuse controls (no real photos / Gemini)."""

from __future__ import annotations

from instamagic.auth import (
    check_password,
    clear_login_failures,
    client_ip,
    login_is_blocked,
    record_login_failure,
    session_is_valid,
    sign_session,
)
from instamagic.config import Settings, get_settings
from tests.conftest import login


def test_protected_routes_require_session(client) -> None:
    for path in (
        "/api/libraries",
        "/api/images",
        "/api/queue",
        "/api/candidates",
        "/api/config",
        "/api/jobs",
        "/api/facets",
        "/api/tags",
        "/api/instagram/status",
    ):
        assert client.get(path).status_code == 401, path


def test_login_sets_httponly_session_cookie(client) -> None:
    response = client.post("/api/auth/login", json={"password": "test-password-123"})
    assert response.status_code == 200
    cookie = response.cookies.get("instamagic_session")
    assert cookie
    assert session_is_valid(get_settings(), cookie)
    # Starlette TestClient exposes Set-Cookie; HttpOnly must be present on the raw header.
    set_cookie = response.headers.get("set-cookie", "")
    assert "httponly" in set_cookie.lower()
    assert "samesite=lax" in set_cookie.lower()


def test_https_frontend_origin_enables_secure_cookie(app_env, monkeypatch) -> None:
    monkeypatch.setenv("FRONTEND_ORIGIN", "https://instamagic.example")
    get_settings.cache_clear()
    settings = get_settings()
    assert settings.session_cookie_secure() is True
    get_settings.cache_clear()


def test_wrong_password_and_rate_limit(client) -> None:
    for _ in range(10):
        response = client.post("/api/auth/login", json={"password": "wrong-password!!"})
        assert response.status_code == 401
    blocked = client.post("/api/auth/login", json={"password": "wrong-password!!"})
    assert blocked.status_code == 429
    # Correct password is also blocked until the window clears.
    still = client.post("/api/auth/login", json={"password": "test-password-123"})
    assert still.status_code == 429


def test_check_password_rejects_different_lengths() -> None:
    settings = Settings(
        app_password="test-password-123",
        app_secret="test-secret-value-123456",
        safety_classifier="fake",
        gemini_provider="fake",
        auto_publish=False,
    )
    assert check_password(settings, "test-password-123") is True
    assert check_password(settings, "short") is False
    assert check_password(settings, "test-password-123" + "x") is False


def test_tampered_session_is_rejected() -> None:
    settings = get_settings()
    token = sign_session(settings)
    bad = token[:-4] + ("dead" if not token.endswith("dead") else "beef")
    assert session_is_valid(settings, bad) is False
    assert session_is_valid(settings, None) is False
    assert session_is_valid(settings, "no-dot") is False


def test_client_ip_trusts_forwarded_only_from_private_peer() -> None:
    class _Client:
        def __init__(self, host: str) -> None:
            self.host = host

    class _Req:
        def __init__(self, host: str, forwarded: str | None = None) -> None:
            self.client = _Client(host)
            self.headers = {"x-forwarded-for": forwarded} if forwarded else {}

    assert client_ip(_Req("10.0.0.5", "203.0.113.9")) == "203.0.113.9"  # type: ignore[arg-type]
    # Public peers must not honor X-Forwarded-For (spoof protection).
    assert client_ip(_Req("8.8.8.8", "198.51.100.2")) == "8.8.8.8"  # type: ignore[arg-type]


def test_login_failure_window_decays() -> None:
    failures: dict[str, list[float]] = {}
    now = 1_700_000_000.0
    for index in range(10):
        record_login_failure(failures, "1.2.3.4", now=now + index)
    assert login_is_blocked(failures, "1.2.3.4", now=now + 9)
    assert not login_is_blocked(failures, "1.2.3.4", now=now + 15 * 60 + 20)
    clear_login_failures(failures, "1.2.3.4")
    assert not login_is_blocked(failures, "1.2.3.4", now=now + 9)


def test_logout_clears_session(client) -> None:
    login(client)
    assert client.get("/api/auth/session").status_code == 200
    assert client.post("/api/auth/logout").status_code == 200
    assert client.get("/api/auth/session").status_code == 401
