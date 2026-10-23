"""Content versions for blur renders so browsers never reuse a stale preview.

Blur derivatives are rewritten in place (``{image_id}/blurs/cover.jpg``) every time a
cover is moved, resized, rotated or re-shaped. Their URLs therefore carry a ``v``
token derived from the full cover geometry plus the on-disk file stamp; a changed
cover or a re-rendered file always yields a new URL.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from pathlib import Path
from typing import Any


def _geometry_key(blur: Any) -> tuple[object, ...]:
    edge_mode = str(getattr(blur, "edge_mode", None) or "soft")
    # Only non-default edge modes join the key so existing (soft) hashes are unchanged.
    extra: tuple[object, ...] = () if edge_mode == "soft" else (edge_mode,)
    return (
        str(blur.id),
        int(blur.x),
        int(blur.y),
        int(blur.width),
        int(blur.height),
        str(blur.shape),
        round(float(getattr(blur, "rotation_deg", 0.0) or 0.0), 3),
        round(float(getattr(blur, "corner_radius", 0.0) or 0.0), 3),
        int(blur.feather),
        round(float(blur.radius), 3),
        str(blur.strength),
    ) + extra


def blur_geometry_hash(blurs: Iterable[Any]) -> str:
    """Stable hash over every cover's full geometry (order independent)."""
    keys = sorted(_geometry_key(blur) for blur in blurs)
    return hashlib.sha1(repr(keys).encode("utf-8")).hexdigest()[:12]


def file_stamp(path: Path | None) -> str:
    if path is None:
        return "-"
    try:
        stat = path.stat()
    except OSError:
        return "-"
    return f"{stat.st_mtime_ns}:{stat.st_size}"


def blur_render_version(blurs: Iterable[Any], path: Path | None = None) -> str:
    """Version token for a blur render: geometry of all covers + rendered file stamp."""
    payload = f"{blur_geometry_hash(blurs)}|{file_stamp(path)}"
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]
