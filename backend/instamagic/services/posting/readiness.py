from __future__ import annotations

from instamagic.config import Settings
from instamagic.models.entities import Image, PublishingQueueItem
from instamagic.models.enums import SafetyState
from instamagic.services.blurs.gate_context import publishing_gate_kwargs
from instamagic.services.instagram.store import load_cached_account
from instamagic.services.pipeline import is_attested
from instamagic.services.recommendations.gates import gemini_gate, publishing_gate
from instamagic.services.recommendations.gemini_derivatives import has_approved_covering_blur
from sqlalchemy import select
from sqlalchemy.orm import Session


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
    # Same gate as queue/publish (incl. rotation-aware coverage and DO_NOT_POST unlock),
    # plus the "blur edited but not re-approved" block so a moved cover is never shown
    # as ready while publish would refuse it.
    from instamagic.services.instagram.publish_derivative import blur_drawn_not_approved_reason

    publish_gate = publishing_gate(attested=attested, **publishing_gate_kwargs(session, image))
    publishing_allowed = publish_gate.allowed
    publishing_reasons = list(publish_gate.reasons)
    blur_pending = blur_drawn_not_approved_reason(image)
    if blur_pending:
        publishing_allowed = False
        publishing_reasons.append(blur_pending)
    from instamagic.services.blurs.coverage_report import readiness_blur_reason

    blur_check = readiness_blur_reason(image)
    if blur_check:
        # Detail only: the gate above already blocks DO_NOT_POST until the unlock is verified.
        publishing_allowed = False
        publishing_reasons.append(blur_check)
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
        "publishing_allowed": publishing_allowed,
        "publishing_reasons": publishing_reasons,
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
