"""Posting slot selection — defaults from config; override best_slots() later for insights."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from instamagic.models import PublishingQueueItem, QueueState
from instamagic.services.posting.schedule_config import ScheduleConfigView
from sqlalchemy import func, select
from sqlalchemy.orm import Session


def best_slots(
    config: ScheduleConfigView, *, from_day: datetime | None = None, days: int = 14
) -> list[datetime]:
    """Return candidate UTC datetimes for the next `days` calendar days in config timezone."""
    tz = ZoneInfo(config.timezone)
    start_local = (
        (from_day or datetime.now(UTC))
        .astimezone(tz)
        .replace(hour=0, minute=0, second=0, microsecond=0)
    )
    slots: list[datetime] = []
    for offset in range(days):
        day = start_local + timedelta(days=offset)
        key = day.strftime("%A").lower()
        for hhmm in config.weekday_slots.get(key, []):
            try:
                hour, minute = hhmm.split(":", 1)
                local = day.replace(hour=int(hour), minute=int(minute))
                slots.append(local.astimezone(UTC))
            except (ValueError, TypeError):
                continue
    return sorted(slots)


def _scheduled_today_count(session: Session, config: ScheduleConfigView, now: datetime) -> int:
    tz = ZoneInfo(config.timezone)
    local = now.astimezone(tz)
    day_start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=1)
    start_utc = day_start.astimezone(UTC)
    end_utc = day_end.astimezone(UTC)
    count = session.scalar(
        select(func.count())
        .select_from(PublishingQueueItem)
        .where(
            PublishingQueueItem.scheduled_at.is_not(None),
            PublishingQueueItem.scheduled_at >= start_utc,
            PublishingQueueItem.scheduled_at < end_utc,
            PublishingQueueItem.state.in_(
                [QueueState.APPROVED.value, QueueState.PUBLISHING.value, QueueState.PUBLISHED.value]
            ),
        )
    )
    return int(count or 0)


def _busy_times(session: Session, start: datetime, end: datetime) -> list[datetime]:
    """Scheduled (approved/publishing) and published post times in [start, end]."""
    scheduled = session.scalars(
        select(PublishingQueueItem.scheduled_at).where(
            PublishingQueueItem.scheduled_at.is_not(None),
            PublishingQueueItem.scheduled_at >= start,
            PublishingQueueItem.scheduled_at <= end,
            PublishingQueueItem.state.in_([QueueState.APPROVED.value, QueueState.PUBLISHING.value]),
        )
    ).all()
    published = session.scalars(
        select(PublishingQueueItem.published_at).where(
            PublishingQueueItem.published_at.is_not(None),
            PublishingQueueItem.published_at >= start,
            PublishingQueueItem.published_at <= end,
        )
    ).all()
    out: list[datetime] = []
    for value in [*scheduled, *published]:
        if value is None:
            continue
        out.append(value if value.tzinfo else value.replace(tzinfo=UTC))
    return out


def next_available_slot(
    session: Session, config: ScheduleConfigView, now: datetime | None = None
) -> datetime | None:
    """Pick the earliest open slot respecting max_posts_per_day and min_gap_hours.

    The min gap is checked against neighbouring posts on both sides (scheduled or
    already published), so empty slots before a later scheduled item get filled
    instead of always appending after the last scheduled post. Existing items are
    never moved.
    """
    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    candidates = [s for s in best_slots(config, from_day=now, days=21) if s > now]
    if not candidates:
        return None
    min_gap = timedelta(hours=config.min_gap_hours)
    busy = _busy_times(session, candidates[0] - min_gap, candidates[-1] + min_gap)
    for slot in candidates:
        if any(abs(slot - b) < min_gap for b in busy):
            continue
        if _scheduled_today_count(session, config, slot) >= config.max_posts_per_day:
            continue
        return slot
    return None


def assign_auto_slots(
    session: Session, items: list[PublishingQueueItem], config: ScheduleConfigView
) -> int:
    """Assign scheduled_at for approved auto-slot items without a time yet."""
    assigned = 0
    now = datetime.now(UTC)
    for item in items:
        if item.scheduled_at is not None:
            continue
        slot = next_available_slot(session, config, now=now)
        if slot is None:
            break
        item.scheduled_at = slot
        item.schedule_mode = "auto_slot"
        session.flush()
        assigned += 1
    session.commit()
    return assigned
