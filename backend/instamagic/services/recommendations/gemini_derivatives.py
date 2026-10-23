"""Derivative bytes and blur coverage helpers for Gemini (no FastAPI imports)."""

from __future__ import annotations

from instamagic.config import Settings
from instamagic.errors import GeminiBlocked
from instamagic.models import BlurCover, CropSuggestion, Image, SafetyState
from instamagic.services.blurs.generator import BlurWindow, covers_unsafe_boxes
from instamagic.services.blurs.unlock import blur_unlock_effective
from instamagic.services.gemini.context import gemini_allows_pixels
from instamagic.services.images.paths import safe_join
from instamagic.services.instagram.publish_derivative import (
    ensure_publish_derivative,
    has_any_approved_blur,
)
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
        rotation_deg=blur.rotation_deg,
        corner_radius=blur.corner_radius,
        edge_mode=blur.edge_mode,
    )


def has_approved_covering_blur(session: Session, image: Image) -> bool:
    approved = [blur for blur in image.blurs if blur.approved]
    if not approved:
        return False
    unsafe = clear_exposed_boxes(session, image.id)
    if not unsafe:
        return True
    if image.width is None or image.height is None:
        return False
    return covers_unsafe_boxes(
        [_window_from_row(blur) for blur in approved],
        unsafe,
        (image.width, image.height),
    )


def derivative_bytes_for_gemini(
    session: Session,
    settings: Settings,
    image: Image,
    approved: CropSuggestion | None,
    *,
    require_policy: bool,
) -> bytes | None:
    if require_policy and not gemini_allows_pixels(settings.gemini_image_policy):
        return None
    if image.model_state == SafetyState.DO_NOT_POST.value and not blur_unlock_effective(session, image):
        return None
    relative = None
    approved_blur_path = next(
        (blur.derivative_path for blur in (image.blurs or []) if blur.approved and blur.derivative_path),
        None,
    )
    if has_any_approved_blur(image):
        if approved is not None and approved.derivative_path:
            relative = approved.derivative_path
        elif approved_blur_path:
            relative = approved_blur_path
        else:
            if require_policy:
                raise GeminiBlocked("Approved blur requires a rendered blur derivative.")
            return None
    elif image.model_state == SafetyState.SAFE_AFTER_CROP.value:
        if approved is not None and approved.derivative_path:
            relative = approved.derivative_path
        else:
            if require_policy:
                raise GeminiBlocked("SAFE_AFTER_CROP requires an approved crop or blur derivative.")
            return None
    else:
        relative = image.preview_path or image.thumbnail_path
    if not relative:
        return None
    path = safe_join(settings.derivatives_root, str(relative))
    if not path.is_file():
        return None
    return path.read_bytes()


def curation_derivative_bytes(session: Session, settings: Settings, image: Image) -> bytes | None:
    if image.model_state == SafetyState.DO_NOT_POST.value and not blur_unlock_effective(session, image):
        return None
    approved = next((crop for crop in image.crops if crop.approved), None) if image.crops else None
    return gemini_preview_bytes(session, settings, image, approved, require_policy=False)


def gemini_preview_bytes(
    session: Session,
    settings: Settings,
    image: Image,
    approved: CropSuggestion | None,
    *,
    require_policy: bool,
) -> bytes | None:
    if require_policy and not gemini_allows_pixels(settings.gemini_image_policy):
        return None
    if has_any_approved_blur(image):
        relative = ensure_publish_derivative(session, settings, image, approved)
        path = safe_join(settings.derivatives_root, str(relative))
        if not path.is_file():
            if require_policy:
                raise GeminiBlocked("Approved blur requires a rendered publish derivative.")
            return None
        return path.read_bytes()
    return derivative_bytes_for_gemini(session, settings, image, approved, require_policy=require_policy)
