from __future__ import annotations

import hashlib
import hmac
import time

from instamagic.config import Settings

COOKIE_NAME = "instamagic_session"


def check_password(settings: Settings, password: str) -> bool:
    return hmac.compare_digest(settings.app_password.encode(), password.encode())


def sign_session(settings: Settings) -> str:
    payload = str(int(time.time()) + settings.session_ttl_seconds)
    signature = hmac.new(settings.app_secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{signature}"


def session_is_valid(settings: Settings, token: str | None) -> bool:
    if not token or "." not in token:
        return False
    payload, signature = token.rsplit(".", 1)
    expected = hmac.new(settings.app_secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        return False
    try:
        expires = int(payload)
    except ValueError:
        return False
    return expires >= int(time.time())
