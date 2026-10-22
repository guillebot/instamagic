"""Purpose-separated keys derived from APP_SECRET (HKDF-SHA256)."""

from __future__ import annotations

import hashlib
import hmac

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

PURPOSE_SESSION = b"instamagic.session.v1"
PURPOSE_SIGNED_PAYLOAD = b"instamagic.signed-payload.v1"
PURPOSE_PUBLISH_ASSET = b"instamagic.publish-asset.v1"


def derive_signing_key(app_secret: str, purpose: bytes, *, length: int = 32) -> bytes:
    material = app_secret.encode("utf-8")
    return HKDF(
        algorithm=hashes.SHA256(),
        length=length,
        salt=None,
        info=purpose,
    ).derive(material)


def hmac_sha256_hex(key: bytes, message: str | bytes) -> str:
    if isinstance(message, str):
        message = message.encode("utf-8")
    return hmac.new(key, message, hashlib.sha256).hexdigest()


def hmac_sha256_digest(key: bytes, message: bytes) -> bytes:
    return hmac.new(key, message, hashlib.sha256).digest()


def verify_hmac_sha256_hex(key: bytes, message: str | bytes, signature_hex: str) -> bool:
    expected = hmac_sha256_hex(key, message)
    return hmac.compare_digest(expected, signature_hex)


def verify_hmac_sha256_digest(key: bytes, message: bytes, digest: bytes) -> bool:
    expected = hmac_sha256_digest(key, message)
    return hmac.compare_digest(digest, expected)
