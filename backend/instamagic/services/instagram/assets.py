"""Short-lived signed URLs so Meta can fetch a publish derivative without a session cookie.

Tokens are AEAD-encrypted (path not visible in the URL) with a purpose-derived key.
Secrets stay in APP_SECRET; never log the token.
"""

from __future__ import annotations

import base64
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from instamagic.errors import NotAllowed, NotFound
from instamagic.security.signing import PURPOSE_PUBLISH_ASSET, derive_signing_key
from instamagic.services.images.paths import safe_join

DEFAULT_TTL_SECONDS = 15 * 60

_PUBLISH_PATH_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
    r"/publish/(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.jpg|reel-[0-9a-f]{32}\.mp4)$"
)

# Reels: Meta fetches + transcodes asynchronously, so the video link lives a bit longer.
REEL_TTL_SECONDS = 30 * 60

_GCM_NONCE_LEN = 12


@dataclass(frozen=True)
class PublishAsset:
    relative_path: str
    expires_at: int


def is_allowed_publish_relative_path(relative_path: str) -> bool:
    if ".." in relative_path or relative_path.startswith(("/", "\\")):
        return False
    if not _PUBLISH_PATH_RE.fullmatch(relative_path):
        return False
    try:
        UUID(relative_path.split("/", 1)[0])
    except ValueError:
        return False
    return True


def _publish_asset_key(secret: str) -> bytes:
    return derive_signing_key(secret, PURPOSE_PUBLISH_ASSET, length=32)


def _encode_opaque_token(raw: bytes) -> str:
    """Traefik allows [A-Za-z0-9_-]{40,1000} on this path."""
    token = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    if not re.fullmatch(r"[A-Za-z0-9_-]{40,1000}", token):
        raise NotAllowed("Publish asset token could not be minted safely.")
    return token


def _decode_opaque_token(token: str) -> bytes:
    if not re.fullmatch(r"[A-Za-z0-9_-]{40,1000}", token):
        raise NotAllowed("Publish asset token is invalid.")
    padded = token + "=" * (-len(token) % 4)
    try:
        return base64.urlsafe_b64decode(padded.encode("ascii"))
    except (ValueError, UnicodeError) as exc:
        raise NotAllowed("Publish asset token is invalid.") from exc


def mint_publish_token(*, relative_path: str, secret: str, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> str:
    if not is_allowed_publish_relative_path(relative_path):
        raise NotAllowed("Publish asset path is not allowed.")
    expires_at = int(time.time()) + max(60, ttl_seconds)
    plaintext = f"{expires_at}:{relative_path}".encode()
    nonce = os.urandom(_GCM_NONCE_LEN)
    aad = PURPOSE_PUBLISH_ASSET
    ciphertext = AESGCM(_publish_asset_key(secret)).encrypt(nonce, plaintext, aad)
    return _encode_opaque_token(nonce + ciphertext)


def parse_publish_token(token: str, *, secret: str) -> PublishAsset:
    raw = _decode_opaque_token(token)
    if len(raw) <= _GCM_NONCE_LEN:
        raise NotAllowed("Publish asset token is invalid.")
    nonce, ciphertext = raw[:_GCM_NONCE_LEN], raw[_GCM_NONCE_LEN:]
    key = _publish_asset_key(secret)
    try:
        plaintext = AESGCM(key).decrypt(nonce, ciphertext, PURPOSE_PUBLISH_ASSET)
    except Exception as exc:
        raise NotAllowed("Publish asset token is invalid.") from exc
    try:
        stamp, relative = plaintext.decode("utf-8").split(":", 1)
        expires_at = int(stamp)
    except ValueError as exc:
        raise NotAllowed("Publish asset token is invalid.") from exc
    if expires_at < int(time.time()):
        raise NotAllowed("Publish asset token has expired.")
    if not is_allowed_publish_relative_path(relative):
        raise NotAllowed("Publish asset path is not allowed.")
    return PublishAsset(relative_path=relative, expires_at=expires_at)


def resolve_publish_file(derivatives_root: Path, asset: PublishAsset) -> Path:
    if not is_allowed_publish_relative_path(asset.relative_path):
        raise NotAllowed("Publish asset path is not allowed.")
    path = safe_join(derivatives_root, asset.relative_path)
    if not path.is_file():
        raise NotFound("Publish asset was not found.")
    return path


def publish_asset_media_type(relative_path: str) -> str:
    """Content-Type for an allowed publish asset (only verified JPEG renders and reel MP4s exist)."""
    return "video/mp4" if relative_path.endswith(".mp4") else "image/jpeg"


def public_image_url(*, public_base_url: str, token: str) -> str:
    base = public_base_url.rstrip("/")
    return f"{base}/api/public/publish-assets/{token}"
