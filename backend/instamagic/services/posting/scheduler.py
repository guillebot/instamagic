"""Idempotent scheduled publish tick — run from worker-io periodically."""

from __future__ import annotations

from datetime import UTC, datetime

from instamagic.config import Settings
from instamagic.logging_config import get_logger
from instamagic.models import PublishingQueueItem, QueueMediaType, QueueState
from instamagic.models.enums import PublishTrigger
from instamagic.services.factory import build_instagram
from instamagic.services.instagram.client import InstagramClient
from instamagic.services.instagram.publish import publish_queue_item
from instamagic.services.instagram.story_publish import run_scheduled_story_tick
from instamagic.services.posting.schedule_config import config_view, get_or_create_config
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

log = get_logger("posting.scheduler")

_LIMIT_BACKOFF_THRESHOLD = 48
def content_limit_near_cap(client: InstagramClient) -> bool:
    if not client.configured():
        return True
    try:
        limit = client.fetch_content_publishing_limit()
    except Exception:
        log.info("content_limit_check_failed")
        return True
    return limit.usage >= _LIMIT_BACKOFF_THRESHOLD


def run_scheduled_publish_tick(session: Session, settings: Settings, client: InstagramClient) -> int:
    """Publish due scheduled items. Returns count of successful publishes."""
    get_or_create_config(session)
    view = config_view(session)
    if view.paused:
        return 0
    if not client.configured():
        return 0
    if content_limit_near_cap(client):
        log.info("content_limit_backoff", threshold=_LIMIT_BACKOFF_THRESHOLD)
        return 0

    now = datetime.now(UTC)
    stmt = (
        select(PublishingQueueItem)
        .where(
            PublishingQueueItem.state == QueueState.APPROVED.value,
            PublishingQueueItem.schedule_mode.is_not(None),
            PublishingQueueItem.scheduled_at.is_not(None),
            PublishingQueueItem.scheduled_at <= now,
            # Standalone Stories carry no caption; everything else needs an approved caption.
            or_(
                PublishingQueueItem.caption_id.is_not(None),
                PublishingQueueItem.media_type == QueueMediaType.STORY.value,
            ),
        )
        .order_by(PublishingQueueItem.scheduled_at.asc())
        .limit(5)
    )
    # Row lock on PostgreSQL to avoid double publish across workers.
    if session.bind and session.bind.dialect.name == "postgresql":
        stmt = stmt.with_for_update(skip_locked=True)
    items = list(session.scalars(stmt).all())
    published = 0
    for item in items:
        if item.publish_retry_after and item.publish_retry_after > now:
            continue
        try:
            publish_queue_item(
                session,
                settings,
                client,
                item_id=item.id,
                caption_id=item.caption_id,
                trigger=PublishTrigger.SCHEDULER.value,
            )
            published += 1
        except Exception as exc:
            err = str(getattr(exc, "detail", exc))
            log.info("scheduled_publish_failed", queue_item_id=str(item.id), error=err)
    published += run_scheduled_story_tick(session, settings, client)
    return published


def run_scheduler_job() -> int:
    """Entry point for RQ / worker thread."""
    from instamagic.config import get_settings
    from instamagic.db import session_factory

    settings = get_settings()
    session = session_factory()()
    try:
        client = build_instagram(settings, session)
        return run_scheduled_publish_tick(session, settings, client)
    finally:
        session.close()
