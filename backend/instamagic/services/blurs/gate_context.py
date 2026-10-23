"""Shared publishing-gate kwargs for an image row."""

from __future__ import annotations

from instamagic.models import Image
from instamagic.models.enums import SafetyState
from instamagic.services.blurs.unlock import blur_unlock_effective
from instamagic.services.recommendations.gemini_derivatives import has_approved_covering_blur
from sqlalchemy.orm import Session


def publishing_gate_kwargs(session: Session, image: Image) -> dict:
    approved_crop = next((crop for crop in image.crops if crop.approved), None)
    has_blur = any(blur.approved for blur in image.blurs) if image.blurs else False
    covering = has_approved_covering_blur(session, image)
    return {
        "human_rating": image.human_rating,
        "model_state": image.model_state,
        "needs_crop": image.model_state == SafetyState.SAFE_AFTER_CROP.value,
        "has_approved_crop": approved_crop is not None,
        "has_approved_covering_blur": covering,
        "has_approved_blur": has_blur,
        "do_not_post_blur_unlocked": blur_unlock_effective(image),
    }
