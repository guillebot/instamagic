"""Derivative bytes and blur coverage helpers for Gemini (no FastAPI imports)."""

from __future__ import annotations

from pathlib import Path

from instamagic.config import Settings
from instamagic.errors import GeminiBlocked
from instamagic.models import BlurCover, CropSuggestion, Image, SafetyState
from instamagic.services.blurs.generator import BlurWindow, covers_unsafe_boxes
from instamagic.services.gemini.context import gemini_allows_pixels
from instamagic.services.images.paths import safe_join
from instamagic.services.pipeline import clear_exposed_boxes
from sqlalchemy.orm import Session


def _window_from_row(blur: BlurCover) -> BlurWindow:
    return BlurWindow(
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
    )


def has_approved_covering_blur(session: Session, image: Image) -> bool:
    approved = [blur for blur in image.blurs if blur.approved]
    if not approved:
        return False
    unsafe = clear_exposed_boxes(session, image.id)
    if not unsafe:
        return True
    return covers_unsafe_boxes([_window_from_row(blur) for blur in approved], unsafe)


def derivative_bytes_for_gemini(
    settings: Settings,
    image: Image,
    approved: CropSuggestion | None,
    *,
    require_policy: bool,
) -> bytes | None:
    if require_policy and not gemini_allows_pixels(settings.gemini_image_policy):
        return None
    if image.model_state == SafetyState.DO_NOT_POST.value:
        return None
    relative = None
    if image.model_state == SafetyState.SAFE_AFTER_CROP.value:
        approved_blur = next(
            (blur for blur in (image.blurs or []) if blur.approved and blur.derivative_path),
            None,
        )
        if approved is not None and approved.derivative_path:
            relative = approved.derivative_path
        elif approved_blur is not None:
            relative = approved_blur.derivative_path
        else:
            if require_policy:
                raise GeminiBlocked("SAFE_AFTER_CROP requires an approved crop or blur derivative.")
            return None
    else:
        relative = image.preview_path or image.thumbnail_path
    if not relative:
        return None
    path = safe_join(settings.derivatives_root, Path(relative))
    if not path.is_file():
        return None
    return path.read_bytes()


def curation_derivative_bytes(settings: Settings, image: Image) -> bytes | None:
    if image.model_state == SafetyState.DO_NOT_POST.value:
        return None
    approved = next((crop for crop in image.crops if crop.approved), None) if image.crops else None
    return derivative_bytes_for_gemini(settings, image, approved, require_policy=False)
