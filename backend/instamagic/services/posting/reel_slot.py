"""Daily Reel slot: auto-prepare a Reel version of the photo/carousel that lands in the Reel slot.

The Reel version is created as a CANDIDATE (never auto-approved): the user previews and approves
it in the Queue. When approved and auto-scheduled, it takes the source's Reel slot and the source
photo/carousel is unscheduled (kept approved) so the same photo is not posted twice. Rendering uses only the verified feed derivatives
(the same blurred render that would be published), via the existing reel pipeline.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime

from instamagic.errors import NotAllowed, NotFound
from instamagic.logging_config import get_logger
from instamagic.models import PublishingQueueItem, PublishingQueueSlide, QueueMediaType, QueueState
from instamagic.services.posting.schedule_config import ScheduleConfigView
from instamagic.services.posting.slots import is_reel_slot
from sqlalchemy import select
from sqlalchemy.orm import Session

log = get_logger(__name__)

_SOURCE_TYPES = {QueueMediaType.IMAGE.value, QueueMediaType.CAROUSEL.value}


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def existing_reel_version(session: Session, source_id) -> PublishingQueueItem | None:
    return session.scalars(
        select(PublishingQueueItem).where(PublishingQueueItem.reel_for_item_id == source_id)
    ).first()


def _source_image_ids(session: Session, item: PublishingQueueItem) -> list:
    if item.media_type == QueueMediaType.CAROUSEL.value:
        slides = session.scalars(
            select(PublishingQueueSlide)
            .where(PublishingQueueSlide.queue_item_id == item.id)
            .order_by(PublishingQueueSlide.sort_order)
        ).all()
        ids = [slide.image_id for slide in slides][:10]
        if ids:
            return ids
    return [item.image_id]


def _start_render(reel_id) -> None:
    from instamagic.config import get_settings
    from instamagic.services.instagram.media_publish import mark_reel_rendering, run_reel_render

    settings = get_settings()
    if settings.pipeline_mode == "inline":  # tests / dev: leave for the manual "Render" button
        return
    from instamagic.db import session_factory

    try:
        with session_factory()() as session:
            mark_reel_rendering(session, reel_id)
    except (NotAllowed, NotFound):
        return
    threading.Thread(target=run_reel_render, args=(settings, reel_id), daemon=True).start()


def prepare_reel_version(session: Session, item: PublishingQueueItem) -> PublishingQueueItem | None:
    """Create (once) a candidate Reel from this approved photo/carousel. Never approves it."""
    from instamagic.services.instagram.media_publish import create_reel_item

    if item.media_type not in _SOURCE_TYPES or item.state != QueueState.APPROVED.value:
        return None
    found = existing_reel_version(session, item.id)
    if found is not None:
        return found
    try:
        reel = create_reel_item(session, _source_image_ids(session, item))  # same publish gates
    except (NotAllowed, NotFound) as exc:
        log.info("reel_version_skipped", queue_item_id=str(item.id), reason=str(getattr(exc, "detail", exc))[:200])
        return None
    reel.reel_for_item_id = item.id
    reel.caption_id = item.caption_id
    reel.collaborators_json = item.collaborators_json
    session.commit()
    session.refresh(reel)
    log.info("reel_version_prepared", queue_item_id=str(item.id), reel_item_id=str(reel.id))
    try:
        _start_render(reel.id)
    except Exception:  # render can be retried from the Queue
        log.info("reel_version_render_not_started", reel_item_id=str(reel.id))
    return reel


def prepare_reel_versions_for(
    session: Session, items: list[PublishingQueueItem], config: ScheduleConfigView
) -> int:
    made = 0
    for item in items:
        when = _aware(item.scheduled_at)
        if when is None or item.media_type not in _SOURCE_TYPES or item.state != QueueState.APPROVED.value:
            continue
        if not is_reel_slot(config, when):
            continue
        if prepare_reel_version(session, item) is not None:
            made += 1
    return made


def take_source_reel_slot(session: Session, reel: PublishingQueueItem, config: ScheduleConfigView) -> bool:
    """An approved Reel version takes its source's future Reel slot; the source moves on."""
    if reel.media_type != QueueMediaType.REELS.value or reel.reel_for_item_id is None:
        return False
    source = session.get(PublishingQueueItem, reel.reel_for_item_id)
    if source is None or source.state != QueueState.APPROVED.value:
        return False
    when = _aware(source.scheduled_at)
    if when is None or when <= datetime.now(UTC) or not is_reel_slot(config, when):
        return False
    reel.scheduled_at = when
    # The Reel replaces the photo/carousel in that slot (no same-day duplicate). The source stays
    # approved but unscheduled; the user can schedule it again from the Queue if they want both.
    source.scheduled_at = None
    source.schedule_mode = None
    session.flush()
    return True
