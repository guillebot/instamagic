"""At-rest sealing for operator secrets (Instagram tokens). Never log plaintext."""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken


def _fernet(app_secret: str) -> Fernet:
    # Deterministic Fernet key from APP_SECRET (HKDF-like via SHA-256).
    digest = hashlib.sha256(b"instamagic.secret_box.v1|" + app_secret.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def seal(plaintext: str, *, app_secret: str) -> str:
    value = plaintext.strip()
    if not value:
        raise ValueError("Cannot seal an empty secret.")
    return _fernet(app_secret).encrypt(value.encode("utf-8")).decode("ascii")


def open_sealed(ciphertext: str, *, app_secret: str) -> str:
    blob = ciphertext.strip()
    if not blob:
        raise ValueError("Missing sealed secret.")
    try:
        return _fernet(app_secret).decrypt(blob.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError) as exc:
        raise ValueError("Could not decrypt stored secret (APP_SECRET rotated?).") from exc
