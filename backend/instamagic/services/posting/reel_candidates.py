"""Auto-proposed Reel slideshows for the Reel schedule (separate from the feed photo slots).

The proposer keeps a small stock of Reel candidates: 3-6 publishable photos from the same shoot
(carousel builder in Reel mode: SAFE/EXCELLENT, attested library, DO_NOT_POST only with a
verified blur, always rendered from the verified blurred feed derivative), cover first and never a
skin/body shot. Each proposal is a CANDIDATE: nothing is approved, scheduled or published until
the user previews and approves it in the Queue ("Reel · needs approval"). Nothing here calls Gemini.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime

from instamagic.errors import NotAllowed, NotFound
from instamagic.logging_config import get_logger
from instamagic.models import Caption, PublishingQueueItem, QueueMediaType, QueueState
from instamagic.models.enums import CaptionStatus
from instamagic.services.posting.schedule_config import config_view
from sqlalchemy import func, select
from sqlalchemy.orm import Session

log = get_logger(__name__)

REEL_MIN_PHOTOS = 3
REEL_MAX_PHOTOS = 6
STOCK_DAYS = 2  # keep enough Reels (approved + waiting for approval) for this many days
MAX_NEW_PER_RUN = 2  # renders are CPU heavy; the hourly tick tops up gradually


def existing_reel_version(session: Session, source_id) -> PublishingQueueItem | None:
    """Legacy (0028): a Reel that was prepared from a feed item."""
    return session.scalars(
        select(PublishingQueueItem).where(PublishingQueueItem.reel_for_item_id == source_id)
    ).first()


def start_render(reel_id) -> bool:
    """Kick a background render (own thread + DB session). False in inline/test mode."""
    from instamagic.config import get_settings
    from instamagic.services.instagram.media_publish import mark_reel_rendering, run_reel_render

    settings = get_settings()
    if settings.pipeline_mode == "inline":  # tests / dev: leave for the manual "Render" button
        return False
    from instamagic.db import session_factory

    try:
        with session_factory()() as session:
            mark_reel_rendering(session, reel_id)
    except (NotAllowed, NotFound):
        return False
    threading.Thread(target=run_reel_render, args=(settings, reel_id), daemon=True).start()
    return True


def reel_stock(session: Session, now: datetime | None = None) -> int:
    """Reels waiting for approval + approved Reels not yet published (future or unscheduled)."""
    now = now or datetime.now(UTC)
    waiting = session.scalar(
        select(func.count())
        .select_from(PublishingQueueItem)
        .where(
            PublishingQueueItem.media_type == QueueMediaType.REELS.value,
            PublishingQueueItem.state == QueueState.CANDIDATE.value,
        )
    )
    approved = session.scalar(
        select(func.count())
        .select_from(PublishingQueueItem)
        .where(
            PublishingQueueItem.media_type == QueueMediaType.REELS.value,
            PublishingQueueItem.state.in_([QueueState.APPROVED.value, QueueState.PUBLISHING.value]),
            (PublishingQueueItem.scheduled_at.is_(None)) | (PublishingQueueItem.scheduled_at > now),
        )
    )
    return int(waiting or 0) + int(approved or 0)


def _cover_caption_id(session: Session, image_id):
    return session.scalars(
        select(Caption.id)
        .where(Caption.image_id == image_id, Caption.status != CaptionStatus.REJECTED.value)
        .order_by(Caption.created_at.desc())
    ).first()


def propose_reel_candidates(
    session: Session, *, max_new: int = MAX_NEW_PER_RUN, render: bool = True, force: bool = False
) -> list[PublishingQueueItem]:
    """Top up Reel candidates to ``reels_per_day * STOCK_DAYS``. Never approves anything.

    ``force`` (manual "Propose Reels" button) proposes up to ``max_new`` even when the stock is full.
    """
    from instamagic.services.instagram.media_publish import create_reel_item
    from instamagic.services.posting.carousel_builder import suggest_carousels
    from instamagic.services.posting.collaborators import prefill_from_shoot

    config = config_view(session)
    if not config.reel_slot_enabled:
        return []
    need = max(0, config.reels_per_day * STOCK_DAYS - reel_stock(session))
    if force:
        need = max(need, 1)
    need = min(need, max_new)
    if need <= 0:
        return []
    suggestions = suggest_carousels(
        session, min_photos=REEL_MIN_PHOTOS, max_photos=REEL_MAX_PHOTOS, for_reels=True, limit=need * 2
    )
    made: list[PublishingQueueItem] = []
    for suggestion in suggestions:
        if len(made) >= need:
            break
        try:
            reel = create_reel_item(session, [image.id for image in suggestion.images])  # same publish gates
        except (NotAllowed, NotFound) as exc:
            log.info("reel_candidate_skipped", reason=str(getattr(exc, "detail", exc))[:200])
            session.rollback()
            continue
        reel.reel_auto = True
        cover = suggestion.images[0]
        reel.caption_id = _cover_caption_id(session, cover.id)
        prefill_from_shoot(session, reel, cover)
        session.commit()
        session.refresh(reel)
        made.append(reel)
        log.info("reel_candidate_proposed", reel_item_id=str(reel.id), photos=len(suggestion.images))
    if render:
        for reel in made:
            try:
                start_render(reel.id)
            except Exception:  # render can be retried from the Queue
                log.info("reel_candidate_render_not_started", reel_item_id=str(reel.id))
    return made
