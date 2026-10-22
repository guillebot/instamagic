from __future__ import annotations

import hashlib
import hmac
import ipaddress
import time
from collections.abc import Mapping

from starlette.requests import Request

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


def sign_session(settings: Settings) -> str:
    payload = str(int(time.time()) + settings.session_ttl_seconds)
    signature = hmac_sha256_hex(_session_key(settings), payload)
    return f"{payload}.{signature}"


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
    """Prefer X-Forwarded-For / X-Real-IP only when the peer is a private proxy (Docker/Traefik)."""
    peer = request.client.host if request.client else "local"
    if not _is_private_or_loopback(peer):
        return peer
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
