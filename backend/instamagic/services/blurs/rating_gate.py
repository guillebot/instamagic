"""Rule: a flagged (DO_NOT_POST) or blurred photo can't be safe/excellent until its blur is verified.

Also the last-moment check before any Gemini call, read straight from the database so a blur
edit committed by another request after the caller loaded the image is still honoured.
"""

from __future__ import annotations

import logging
from typing import Any

from instamagic.models import (
    BlurCover,
    Image,
    ImageRating,
    PublishingQueueItem,
    PublishingQueueSlide,
)
from instamagic.models.enums import BlurUnlockStatus, HumanRating, QueueState, SafetyState
from instamagic.services.blurs.unlock import (
    blur_covers_verified,
    blur_gate_passes,
    blur_geometry_hash,
    requires_blur_verification,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

POSITIVE_RATINGS = frozenset({HumanRating.SAFE.value, HumanRating.EXCELLENT.value})
BLUR_VERIFY_FIRST = (
    "Blur and verify first: this photo is flagged or has blur covers, so it can't be rated safe "
    "or excellent until its blur is approved and verified."
)
BLUR_NOT_VERIFIED_GEMINI = "Blur not verified for this photo; nothing is sent to Gemini."


def rating_blocked_reason(session: Session, image: Image, rating: str) -> str | None:
    if rating not in POSITIVE_RATINGS:
        return None
    if blur_gate_passes(session, image):
        return None
    return BLUR_VERIFY_FIRST


def _pull_from_approved(session: Session, image: Image) -> list[str]:
    """Move APPROVED queue items that show this photo back to CANDIDATE (unscheduled)."""
    item_ids: set[Any] = {item.id for item in image.queue_items}
    item_ids.update(
        session.scalars(select(PublishingQueueSlide.queue_item_id).where(PublishingQueueSlide.image_id == image.id))
    )
    pulled: list[str] = []
    for item_id in item_ids:
        item = session.get(PublishingQueueItem, item_id)
        if item is None or item.state != QueueState.APPROVED.value:
            continue
        item.state = QueueState.CANDIDATE.value
        item.approved_at = None
        item.scheduled_at = None
        item.schedule_mode = None
        item.publish_retry_after = None
        pulled.append(str(item.id))
    return pulled


def demote_if_unverified(session: Session, image: Image, *, reason: str) -> dict[str, Any] | None:
    """Drop safe/excellent back to review when the blur gate no longer passes.

    History stays in image_ratings (a new "review" row is appended); captions are kept but become
    ineligible through the gates. Returns what changed, or None when nothing did.
    """
    if image.human_rating not in POSITIVE_RATINGS:
        return None
    if blur_gate_passes(session, image):
        return None
    previous = image.human_rating
    image.human_rating = HumanRating.REVIEW.value
    session.add(ImageRating(image_id=image.id, rating=HumanRating.REVIEW.value))
    pulled = _pull_from_approved(session, image)
    log.info(
        "rating_demoted_blur_unverified image_id=%s previous=%s reason=%s pulled_queue_items=%s",
        image.id,
        previous,
        reason,
        pulled,
    )
    return {"image_id": str(image.id), "previous": previous, "pulled_queue_items": pulled}


def fresh_blur_gate_passes(session: Session, image: Image) -> bool:
    """Re-read the blur state from the database right before a Gemini call.

    Column selects bypass the ORM identity map, so a stale in-memory image can't vouch for itself.
    """
    row = session.execute(
        select(Image.model_state, Image.blur_unlock_status, Image.blur_unlock_geometry_hash).where(
            Image.id == image.id
        )
    ).one_or_none()
    if row is None:
        return False
    covers = session.execute(
        select(
            BlurCover.id,
            BlurCover.x,
            BlurCover.y,
            BlurCover.width,
            BlurCover.height,
            BlurCover.shape,
            BlurCover.feather,
            BlurCover.radius,
            BlurCover.strength,
            BlurCover.rotation_deg,
            BlurCover.corner_radius,
            BlurCover.edge_mode,
            BlurCover.approved,
        ).where(BlurCover.image_id == image.id)
    ).all()
    needs = row.model_state == SafetyState.DO_NOT_POST.value or bool(covers)
    if not needs:
        # In-memory view must agree; otherwise something is out of sync, so refuse.
        return not requires_blur_verification(image)
    if row.blur_unlock_status != BlurUnlockStatus.VERIFIED.value or not row.blur_unlock_geometry_hash:
        return False
    if not covers or not all(cover.approved for cover in covers):
        return False
    if blur_geometry_hash(list(covers)) != row.blur_unlock_geometry_hash:  # type: ignore[arg-type]
        return False
    # Coverage and threshold checks on the loaded image (same covers, proven by the hash above).
    return blur_covers_verified(session, image)


