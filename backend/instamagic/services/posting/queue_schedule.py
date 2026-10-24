"""Schedule / unschedule approved queue items."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from instamagic.errors import NotAllowed, NotFound
from instamagic.models import PublishingQueueItem, QueueState
from instamagic.models.enums import ScheduleMode
from instamagic.services.posting.schedule_config import config_view
from instamagic.services.posting.slots import assign_auto_slots, next_available_slot
from sqlalchemy import select
from sqlalchemy.orm import Session


def schedule_queue_item(
    session: Session,
    item_id: UUID,
    *,
    scheduled_at: datetime | None,
    mode: str,
) -> PublishingQueueItem:
    item = session.get(PublishingQueueItem, item_id)
    if item is None:
        raise NotFound("Queue item was not found.")
    if item.state != QueueState.APPROVED.value:
        raise NotAllowed("Only approved queue items can be scheduled.")
    if mode not in {ScheduleMode.EXPLICIT.value, ScheduleMode.AUTO_SLOT.value}:
        raise NotAllowed("Schedule mode must be explicit or auto_slot.")
    item.schedule_mode = mode
    if mode == ScheduleMode.EXPLICIT.value:
        if scheduled_at is None:
            raise NotAllowed("scheduled_at is required for explicit scheduling.")
        if scheduled_at.tzinfo is None:
            scheduled_at = scheduled_at.replace(tzinfo=UTC)
        item.scheduled_at = scheduled_at.astimezone(UTC)
    else:
        item.scheduled_at = None
        config = config_view(session)
        slot = next_available_slot(session, config)
        if slot is not None:
            item.scheduled_at = slot
    item.publish_retry_after = None
    item.last_error = None
    session.commit()
    session.refresh(item)
    return item


def unschedule_queue_item(session: Session, item_id: UUID) -> PublishingQueueItem:
    item = session.get(PublishingQueueItem, item_id)
    if item is None:
        raise NotFound("Queue item was not found.")
    if item.state not in {QueueState.APPROVED.value, QueueState.FAILED.value}:
        raise NotAllowed("Only approved or failed items can be unscheduled.")
    item.schedule_mode = None
    item.scheduled_at = None
    item.publish_retry_after = None
    session.commit()
    session.refresh(item)
    return item


def fill_slots_from_approved(session: Session) -> int:
    config = config_view(session)
    items = list(
        session.scalars(
            select(PublishingQueueItem)
            .where(
                PublishingQueueItem.state == QueueState.APPROVED.value,
                PublishingQueueItem.caption_id.is_not(None),
                PublishingQueueItem.schedule_mode == ScheduleMode.AUTO_SLOT.value,
                PublishingQueueItem.scheduled_at.is_(None),
            )
            .order_by(PublishingQueueItem.approved_at.asc())
        ).all()
    )
    return assign_auto_slots(session, items, config)


def list_upcoming(session: Session, limit: int = 50) -> list[PublishingQueueItem]:
    now = datetime.now(UTC)
    return list(
        session.scalars(
            select(PublishingQueueItem)
            .where(
                PublishingQueueItem.scheduled_at.is_not(None),
                PublishingQueueItem.scheduled_at >= now,
                PublishingQueueItem.state.in_([QueueState.APPROVED.value, QueueState.PUBLISHING.value]),
            )
            .order_by(PublishingQueueItem.scheduled_at.asc())
            .limit(limit)
        ).all()
    )
