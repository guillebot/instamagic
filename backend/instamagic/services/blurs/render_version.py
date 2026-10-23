"""Cache-busting version for blur derivative URLs (geometry + file mtime)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import UUID

from instamagic.models import BlurCover


def _geometry_payload(blurs: list[BlurCover]) -> list[dict[str, object]]:
    ordered = sorted(
        blurs,
        key=lambda row: (row.x, row.y, row.width, row.height, str(row.id)),
    )
    return [
        {
            "x": blur.x,
            "y": blur.y,
            "width": blur.width,
            "height": blur.height,
            "shape": blur.shape,
            "feather": blur.feather,
            "edge_mode": blur.edge_mode,
            "radius": round(blur.radius, 4),
            "strength": blur.strength,
            "rotation_deg": round(blur.rotation_deg, 4),
            "corner_radius": round(blur.corner_radius, 4),
        }
        for blur in ordered
    ]


def blur_derivative_version(
    blurs: list[BlurCover],
    *,
    derivatives_root: Path | None = None,
    image_id: UUID | None = None,
) -> str:
    """Short hash for ?v= on blur JPEG URLs; changes when geometry or cover file changes."""
    payload = _geometry_payload(blurs)
    mtime_ns = 0
    if derivatives_root is not None and image_id is not None:
        cover = derivatives_root / f"{image_id}/blurs/cover.jpg"
        if cover.is_file():
            mtime_ns = cover.stat().st_mtime_ns
    raw = json.dumps({"g": payload, "mtime_ns": mtime_ns}, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()[:16]
