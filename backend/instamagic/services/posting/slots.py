"""Posting slot selection — defaults from config; override best_slots() later for insights."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from instamagic.models import PublishingQueueItem, QueueState
from instamagic.services.posting.schedule_config import ScheduleConfigView
from sqlalchemy import func, select
from sqlalchemy.orm import Session


def best_slots(config: ScheduleConfigView, *, from_day: datetime | None = None, days: int = 14) -> list[datetime]:
    """Return candidate UTC datetimes for the next `days` calendar days in config timezone."""
    tz = ZoneInfo(config.timezone)
    start_local = (from_day or datetime.now(UTC)).astimezone(tz).replace(hour=0, minute=0, second=0, microsecond=0)
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


def _last_scheduled_at(session: Session) -> datetime | None:
    return session.scalar(
        select(func.max(PublishingQueueItem.scheduled_at)).where(PublishingQueueItem.scheduled_at.is_not(None))
    )


def next_available_slot(session: Session, config: ScheduleConfigView, now: datetime | None = None) -> datetime | None:
    """Pick the next open slot respecting max_posts_per_day, min_gap_hours, and existing schedule."""
    now = now or datetime.now(UTC)
    if _scheduled_today_count(session, config, now) >= config.max_posts_per_day:
        # Try tomorrow onward
        tz = ZoneInfo(config.timezone)
        tomorrow = now.astimezone(tz).replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        candidates = best_slots(config, from_day=tomorrow.astimezone(UTC), days=21)
    else:
        candidates = [s for s in best_slots(config, from_day=now, days=21) if s > now]

    last = _last_scheduled_at(session)
    min_gap = timedelta(hours=config.min_gap_hours)
    for slot in candidates:
        if last is not None and slot - last < min_gap:
            continue
        day_count = _scheduled_today_count(session, config, slot)
        if day_count >= config.max_posts_per_day:
            continue
        return slot
    return None


def assign_auto_slots(session: Session, items: list[PublishingQueueItem], config: ScheduleConfigView) -> int:
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
        now = slot
    session.commit()
    return assigned
