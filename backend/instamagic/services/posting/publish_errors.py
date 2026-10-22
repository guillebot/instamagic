"""Classify Instagram publish failures for retry vs terminal states."""

from __future__ import annotations

from instamagic.errors import AppError, InstagramNotConfigured, NotAllowed, NotConfigured

_PERMANENT_MARKERS = (
    "do_not_post",
    "not allowed",
    "caption is required",
    "caption text is empty",
    "attestation",
    "human safe",
    "do_not_post",
    "approved crop",
    "no publishable derivative",
    "aspect ratio",
    "invalid oauth",
    "permission",
    "190",  # Meta invalid token
    "#10",  # Meta permission
)


def is_permanent_publish_error(exc: BaseException) -> bool:
    if isinstance(exc, NotAllowed):
        return True
    if isinstance(exc, InstagramNotConfigured):
        return True
    detail = ""
    if isinstance(exc, AppError):
        detail = str(exc.detail).lower()
    else:
        detail = str(exc).lower()
    return any(marker in detail for marker in _PERMANENT_MARKERS)


def is_transient_publish_error(exc: BaseException) -> bool:
    if isinstance(exc, NotConfigured) and not isinstance(exc, InstagramNotConfigured):
        return True
    detail = str(exc).lower()
    transient_markers = ("timeout", "temporarily", "rate limit", "try again", "503", "502", "500")
    return any(marker in detail for marker in transient_markers)
