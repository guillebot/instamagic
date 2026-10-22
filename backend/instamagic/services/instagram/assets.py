"""Short-lived signed URLs so Meta can fetch a publish derivative without a session cookie.

Tokens are HMAC over relative path + expiry. Secrets stay in APP_SECRET; never log the token.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
from dataclasses import dataclass
from pathlib import Path

from instamagic.errors import NotAllowed, NotFound
from instamagic.services.images.paths import safe_join

DEFAULT_TTL_SECONDS = 15 * 60


@dataclass(frozen=True)
class PublishAsset:
    relative_path: str
    expires_at: int


def mint_publish_token(*, relative_path: str, secret: str, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> str:
    expires_at = int(time.time()) + max(60, ttl_seconds)
    payload = f"{expires_at}:{relative_path}".encode()
    digest = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).digest()
    raw = base64.urlsafe_b64encode(payload + b"|" + digest).decode("ascii").rstrip("=")
    return raw


def parse_publish_token(token: str, *, secret: str) -> PublishAsset:
    padded = token + "=" * (-len(token) % 4)
    try:
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))
        payload, digest = raw.rsplit(b"|", 1)
    except (ValueError, UnicodeError) as exc:
        raise NotAllowed("Publish asset token is invalid.") from exc
    expected = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).digest()
    if not hmac.compare_digest(digest, expected):
        raise NotAllowed("Publish asset token is invalid.")
    try:
        stamp, relative = payload.decode("utf-8").split(":", 1)
        expires_at = int(stamp)
    except ValueError as exc:
        raise NotAllowed("Publish asset token is invalid.") from exc
    if expires_at < int(time.time()):
        raise NotAllowed("Publish asset token has expired.")
    if ".." in relative or relative.startswith(("/", "\\")):
        raise NotAllowed("Publish asset path is not allowed.")
    return PublishAsset(relative_path=relative, expires_at=expires_at)


def resolve_publish_file(derivatives_root: Path, asset: PublishAsset) -> Path:
    path = safe_join(derivatives_root, asset.relative_path)
    if not path.is_file():
        raise NotFound("Publish asset was not found.")
    return path


def public_image_url(*, public_base_url: str, token: str) -> str:
    base = public_base_url.rstrip("/")
    return f"{base}/api/public/publish-assets/{token}"
