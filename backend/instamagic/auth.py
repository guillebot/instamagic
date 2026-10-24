from __future__ import annotations

import hashlib
import hmac
import ipaddress
import time
from collections.abc import Mapping

from starlette.requests import Request
from starlette.responses import Response

from instamagic.config import Settings
from instamagic.security.signing import (
    PURPOSE_SESSION,
    PURPOSE_SIGNED_PAYLOAD,
    derive_signing_key,
    hmac_sha256_hex,
    verify_hmac_sha256_hex,
)

COOKIE_NAME = "instamagic_session"
OAUTH_NONCE_COOKIE = "instamagic_ig_oauth_nonce"
LOGIN_WINDOW_SECONDS = 15 * 60
LOGIN_MAX_FAILURES = 10


def check_password(settings: Settings, password: str) -> bool:
    """Constant-time compare via SHA-256 digests (equal length even when inputs differ)."""
    expected = hashlib.sha256(settings.app_password.encode("utf-8")).digest()
    provided = hashlib.sha256(password.encode("utf-8")).digest()
    return hmac.compare_digest(expected, provided)


def _session_key(settings: Settings) -> bytes:
    return derive_signing_key(settings.app_secret, PURPOSE_SESSION)


def _legacy_session_key(settings: Settings) -> bytes:
    return settings.app_secret.encode("utf-8")


def session_expiry(settings: Settings, token: str | None) -> int | None:
    """Expiry (unix seconds) of a valid session token, else None."""
    if not session_is_valid(settings, token):
        return None
    assert token is not None
    return int(token.rsplit(".", 1)[0])


def session_should_renew(settings: Settings, token: str | None, *, now: float | None = None) -> bool:
    """Sliding renewal: re-issue once the cookie is older than SESSION_RENEW_AFTER_SECONDS."""
    expires = session_expiry(settings, token)
    if expires is None:
        return False
    stamp = int(time.time() if now is None else now)
    issued = expires - settings.session_ttl_seconds
    return stamp - issued >= settings.session_renew_after_seconds


def sign_session(settings: Settings) -> str:
    payload = str(int(time.time()) + settings.session_ttl_seconds)
    signature = hmac_sha256_hex(_session_key(settings), payload)
    return f"{payload}.{signature}"


def set_session_cookie(response: Response, settings: Settings) -> None:
    response.set_cookie(
        COOKIE_NAME,
        sign_session(settings),
        max_age=settings.session_ttl_seconds,
        httponly=True,
        samesite="lax",
        secure=settings.session_cookie_secure(),
        path="/",
    )


def session_is_valid(settings: Settings, token: str | None) -> bool:
    if not token or "." not in token:
        return False
    payload, signature = token.rsplit(".", 1)
    if not (
        verify_hmac_sha256_hex(_session_key(settings), payload, signature)
        or verify_hmac_sha256_hex(_legacy_session_key(settings), payload, signature)
    ):
        return False
    try:
        expires = int(payload)
    except ValueError:
        return False
    return expires >= int(time.time())


def _signed_payload_key(settings: Settings) -> bytes:
    return derive_signing_key(settings.app_secret, PURPOSE_SIGNED_PAYLOAD)


def _legacy_signed_payload_key(settings: Settings) -> bytes:
    return settings.app_secret.encode("utf-8")


def sign_payload(payload: str, settings: Settings, *, ttl_seconds: int = 600) -> str:
    """HMAC-sign an opaque payload with an absolute expiry (OAuth state, etc.)."""
    expires = str(int(time.time()) + max(60, ttl_seconds))
    body = f"{expires}.{payload}"
    signature = hmac_sha256_hex(_signed_payload_key(settings), body)
    return f"{body}.{signature}"


def verify_signed_payload(token: str | None, settings: Settings) -> str | None:
    if not token or token.count(".") < 2:
        return None
    expires_s, payload, signature = token.split(".", 2)
    body = f"{expires_s}.{payload}"
    if not (
        verify_hmac_sha256_hex(_signed_payload_key(settings), body, signature)
        or verify_hmac_sha256_hex(_legacy_signed_payload_key(settings), body, signature)
    ):
        return None
    try:
        expires = int(expires_s)
    except ValueError:
        return None
    if expires < int(time.time()):
        return None
    return payload


def _is_private_or_loopback(host: str) -> bool:
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return host in {"localhost", "local"}
    return bool(address.is_private or address.is_loopback or address.is_link_local)


def client_ip(request: Request) -> str:
    """Prefer X-Real-IP / X-Forwarded-For only when the peer is a private proxy (Docker/Traefik).

    The stack nginx overwrites both headers with the real TCP peer it resolved from
    Traefik (see docker/nginx.conf), so client-supplied values never reach here.
    """
    peer = request.client.host if request.client else "local"
    if not _is_private_or_loopback(peer):
        return peer
    real_first = request.headers.get("x-real-ip", "").strip()
    if real_first:
        try:
            ipaddress.ip_address(real_first)
            return real_first
        except ValueError:
            pass
    forwarded = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    if forwarded:
        try:
            ipaddress.ip_address(forwarded)
            return forwarded
        except ValueError:
            pass
    real = request.headers.get("x-real-ip", "").strip()
    if real:
        try:
            ipaddress.ip_address(real)
            return real
        except ValueError:
            pass
    return peer


def lan_auth_bypass(request: Request, settings: Settings) -> bool:
    """True when AUTH_DISABLED applies to this request. Fail-closed; every gate must pass.

    - AUTH_DISABLED is on and AUTH_DISABLED_NETWORKS is non-empty.
    - Exactly one Host header, equal to a LAN UI host (never the public assets host).
    - X-Forwarded-Host, if present, equals that same host.
    - Path is not a public endpoint (/api/public/*).
    - The TCP peer is the in-stack nginx (private/loopback address).
    - X-Real-IP (set by nginx from the Traefik hop, overwriting client values) is a
      single IP, X-Forwarded-For (also overwritten by nginx) is absent or identical,
      and that IP is inside AUTH_DISABLED_NETWORKS (LAN only; internet clients and
      Docker bridge addresses never match).
    """
    if not settings.auth_disabled:
        return False
    networks = settings.auth_disabled_network_list()
    hosts = settings.lan_ui_host_set()
    if not networks or not hosts:
        return False
    headers = request.headers
    getlist = getattr(headers, "getlist", None)

    def _values(name: str) -> list[str]:
        if getlist is not None:
            return list(getlist(name))
        value = headers.get(name)
        return [value] if value is not None else []

    host_values = _values("host")
    if len(host_values) != 1:
        return False
    host = host_values[0].strip().lower()
    if not host or host.startswith("["):
        return False
    host = host.split(":", 1)[0]
    if host not in hosts:
        return False
    xfh = _values("x-forwarded-host")
    if len(xfh) > 1 or (xfh and xfh[0].strip().lower().split(":", 1)[0] != host):
        return False
    if request.url.path.startswith("/api/public/"):
        return False
    peer = request.client.host if request.client else ""
    try:
        peer_ip = ipaddress.ip_address(peer)
    except ValueError:
        return False
    if not (peer_ip.is_private or peer_ip.is_loopback):
        return False
    real_values = _values("x-real-ip")
    if len(real_values) != 1:
        return False
    real = real_values[0].strip()
    if not real or "," in real:
        return False
    try:
        real_ip = ipaddress.ip_address(real)
    except ValueError:
        return False
    forwarded = _values("x-forwarded-for")
    if len(forwarded) > 1 or (forwarded and forwarded[0].strip() != real):
        return False
    return any(real_ip in network for network in networks)


def login_is_blocked(failures: Mapping[str, list[float]], ip: str, *, now: float | None = None) -> bool:
    stamp = time.time() if now is None else now
    recent = [item for item in failures.get(ip, []) if stamp - item < LOGIN_WINDOW_SECONDS]
    return len(recent) >= LOGIN_MAX_FAILURES


def record_login_failure(failures: dict[str, list[float]], ip: str, *, now: float | None = None) -> None:
    stamp = time.time() if now is None else now
    recent = [item for item in failures.get(ip, []) if stamp - item < LOGIN_WINDOW_SECONDS]
    recent.append(stamp)
    failures[ip] = recent


def clear_login_failures(failures: dict[str, list[float]], ip: str) -> None:
    failures.pop(ip, None)
