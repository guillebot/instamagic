from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from instamagic.config import Settings
from instamagic.models.entities import Image, PublishingQueueItem
from instamagic.models.enums import SafetyState
from instamagic.services.blurs.generator import BlurWindow, covers_unsafe_boxes
from instamagic.services.instagram.store import load_cached_account
from instamagic.services.pipeline import clear_exposed_boxes, is_attested
from instamagic.services.recommendations.gates import gemini_gate, publishing_gate


def _window_from_blur(blur) -> BlurWindow:
    return BlurWindow(
        x=blur.x,
        y=blur.y,
        width=blur.width,
        height=blur.height,
        shape=blur.shape,
        feather=blur.feather,
        radius=blur.radius,
        reason=blur.reason,
        covers_unsafe=blur.covers_unsafe,
        strength=blur.strength,
    )


def has_approved_covering_blur(session: Session, image: Image) -> bool:
    approved = [blur for blur in image.blurs if blur.approved]
    if not approved:
        return False
    unsafe = clear_exposed_boxes(session, image.id)
    if not unsafe:
        return True
    return covers_unsafe_boxes([_window_from_blur(blur) for blur in approved], unsafe)


def instagram_target_username(settings: Settings, session: Session) -> str | None:
    expected = settings.instagram_expected_username.strip()
    if expected:
        return expected.lstrip("@")
    cached = load_cached_account(session)
    if isinstance(cached, dict):
        username = str(cached.get("username") or "").strip()
        if username:
            return username.lstrip("@")
    return None


def build_image_publish_readiness(
    session: Session,
    settings: Settings,
    image: Image,
    *,
    instagram_configured: bool,
) -> dict[str, object]:
    approved_crop = next((crop for crop in image.crops if crop.approved), None) if image.crops else None
    covering_blur = has_approved_covering_blur(session, image)
    attested = is_attested(session, image.library_id)
    publish_gate = publishing_gate(
        attested=attested,
        human_rating=image.human_rating,
        model_state=image.model_state,
        needs_crop=image.model_state == SafetyState.SAFE_AFTER_CROP.value,
        has_approved_crop=approved_crop is not None,
        has_approved_covering_blur=covering_blur,
    )
    caption_gate = gemini_gate(
        attested=attested,
        human_rating=image.human_rating,
        model_state=image.model_state,
        needs_crop=image.model_state == SafetyState.SAFE_AFTER_CROP.value,
        has_approved_crop=approved_crop is not None,
        has_approved_covering_blur=covering_blur,
        operator_confirmed=True,
        policy=settings.gemini_image_policy,
    )
    queue_item = session.scalar(
        select(PublishingQueueItem)
        .where(PublishingQueueItem.image_id == image.id)
        .order_by(PublishingQueueItem.created_at.desc())
    )
    return {
        "publishing_allowed": publish_gate.allowed,
        "publishing_reasons": publish_gate.reasons,
        "caption_generation_allowed": caption_gate.allowed,
        "caption_generation_reasons": caption_gate.reasons,
        "library_attested": attested,
        "queue_item_id": str(queue_item.id) if queue_item else None,
        "queue_state": queue_item.state if queue_item else None,
        "scheduled_at": queue_item.scheduled_at.isoformat() if queue_item and queue_item.scheduled_at else None,
        "published_at": queue_item.published_at.isoformat() if queue_item and queue_item.published_at else None,
        "instagram_configured": instagram_configured,
        "instagram_target_username": instagram_target_username(settings, session),
        "uses_approved_crop": approved_crop is not None,
    }
