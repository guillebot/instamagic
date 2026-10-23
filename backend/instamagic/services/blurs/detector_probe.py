"""Read-only classifier probe on the blurred render (same render path as unlock verification)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from instamagic.config import Settings
from instamagic.models import BlurCover, Image, Library
from instamagic.services.blurs import unlock as blur_unlock
from instamagic.services.blurs.generator import BlurWindow, apply_blur_covers
from instamagic.services.images.derivatives import oriented_rgb, read_image
from instamagic.services.images.paths import safe_join
from sqlalchemy.orm import Session


def _windows_from_blurs(blurs: list[BlurCover]) -> list[BlurWindow]:
    return [
        BlurWindow(
            x=blur.x,
            y=blur.y,
            width=blur.width,
            height=blur.height,
            shape=blur.shape,
            feather=blur.feather,
            radius=blur.radius,
            strength=blur.strength,
            reason=blur.reason,
            covers_unsafe=blur.covers_unsafe,
            rotation_deg=blur.rotation_deg,
            corner_radius=blur.corner_radius,
            edge_mode=blur.edge_mode or "soft",
        )
        for blur in blurs
    ]


def render_blurred_rgb(session: Session, settings: Settings, image: Image) -> Any:
    if not image.blurs:
        raise ValueError("Add blur covers before running the detector.")
    library = session.get(Library, image.library_id)
    if library is None:
        raise ValueError("Library was not found.")
    source = safe_join(Path(library.root_path), image.relative_path)
    if not source.is_file():
        raise ValueError("Original image was not found.")
    original_rgb = oriented_rgb(read_image(source))
    return apply_blur_covers(original_rgb, _windows_from_blurs(image.blurs))


def probe_blurred_detector(
    session: Session,
    settings: Settings,
    image: Image,
    classifier: object,
) -> dict[str, Any]:
    blurred_rgb = render_blurred_rgb(session, settings, image)
    signals = blur_unlock._classify_render(blurred_rgb, classifier)
    detections = [
        {
            "label": region.label,
            "score": round(region.score, 4),
            "x": region.box.x,
            "y": region.box.y,
            "width": region.box.width,
            "height": region.box.height,
        }
        for region in signals.clear_exposed()
    ]
    top_score = round(max((region.score for region in signals.regions), default=0.0), 4)
    passed = len(detections) == 0
    return {
        "passed": passed,
        "top_score": top_score,
        "detections": detections,
        "model_name": signals.model_name,
        "detector_available": signals.detector_available,
    }
