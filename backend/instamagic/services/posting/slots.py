"""Posting slot selection — defaults from config; override best_slots() later for insights."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from instamagic.models import PublishingQueueItem, QueueMediaType, QueueState
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


def _lane_filter(lane: str):
    """Stories have their own lane (cap + gap); feed posts, carousels and reels share the feed lane."""
    if lane == "story":
        return PublishingQueueItem.media_type == QueueMediaType.STORY.value
    return PublishingQueueItem.media_type != QueueMediaType.STORY.value


def lane_for_media_type(media_type: str | None) -> str:
    return "story" if media_type == QueueMediaType.STORY.value else "feed"


def lane_cap(config: ScheduleConfigView, lane: str) -> int:
    if lane == "story":
        return int(getattr(config, "max_stories_per_day", 2) or 2)
    return config.max_posts_per_day


def _scheduled_today_count(session: Session, config: ScheduleConfigView, now: datetime, lane: str = "feed") -> int:
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
            _lane_filter(lane),
        )
    )
    return int(count or 0)


def _busy_times(session: Session, start: datetime, end: datetime, lane: str = "feed") -> list[datetime]:
    """Scheduled (approved/publishing) and published post times in [start, end]."""
    scheduled = session.scalars(
        select(PublishingQueueItem.scheduled_at).where(
            PublishingQueueItem.scheduled_at.is_not(None),
            PublishingQueueItem.scheduled_at >= start,
            PublishingQueueItem.scheduled_at <= end,
            PublishingQueueItem.state.in_([QueueState.APPROVED.value, QueueState.PUBLISHING.value]),
            _lane_filter(lane),
        )
    ).all()
    published = session.scalars(
        select(PublishingQueueItem.published_at).where(
            PublishingQueueItem.published_at.is_not(None),
            PublishingQueueItem.published_at >= start,
            PublishingQueueItem.published_at <= end,
            _lane_filter(lane),
        )
    ).all()
    out: list[datetime] = []
    for value in [*scheduled, *published]:
        if value is None:
            continue
        out.append(value if value.tzinfo else value.replace(tzinfo=UTC))
    return out


REEL_SLOT_FALLBACK = timedelta(hours=24)


def is_reel_slot(config: ScheduleConfigView, slot: datetime) -> bool:
    """The daily feed slot reserved for a Reel (default 11:00 local)."""
    if not getattr(config, "reel_slot_enabled", False):
        return False
    local = slot.astimezone(ZoneInfo(config.timezone))
    return local.strftime("%H:%M") == (getattr(config, "reel_slot_time", None) or "11:00")


def next_available_slot(
    session: Session,
    config: ScheduleConfigView,
    now: datetime | None = None,
    *,
    media_type: str | None = None,
    item: PublishingQueueItem | None = None,
) -> datetime | None:
    """Pick the earliest open slot respecting max_posts_per_day and min_gap_hours.

    The min gap is checked against neighbouring posts on both sides (scheduled or
    already published), so empty slots before a later scheduled item get filled
    instead of always appending after the last scheduled post. Existing items are
    never moved.

    Reel slot (feed lane, when enabled): Reels take the earliest free Reel slot first (any slot
    if none is free); photos/carousels skip Reel slots unless the slot is within 24 h and still
    free, so the slot never goes empty. Skin/body posts (``item`` given) also respect the 1-in-5 cap.
    """
    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    candidates = [s for s in best_slots(config, from_day=now, days=21) if s > now]
    if not candidates:
        return None
    min_gap = timedelta(hours=config.min_gap_hours)
    lane = lane_for_media_type(media_type)
    lane_kw = {} if lane == "feed" else {"lane": lane}
    busy = _busy_times(session, candidates[0] - min_gap, candidates[-1] + min_gap, **lane_kw)
    reel_aware = media_type is not None and lane == "feed" and bool(getattr(config, "reel_slot_enabled", False))

    def _open(slot: datetime) -> bool:
        if any(abs(slot - b) < min_gap for b in busy):
            return False
        if _scheduled_today_count(session, config, slot, **lane_kw) >= lane_cap(config, lane):
            return False
        if item is not None and lane == "feed":
            from instamagic.services.posting.content_mix import sensitive_slot_allowed

            if not sensitive_slot_allowed(session, item, slot):
                return False
        return True

    if reel_aware and media_type == QueueMediaType.REELS.value:
        for slot in candidates:
            if is_reel_slot(config, slot) and _open(slot):
                return slot
    for slot in candidates:
        if reel_aware and media_type != QueueMediaType.REELS.value and is_reel_slot(config, slot):
            if slot - now > REEL_SLOT_FALLBACK:
                continue
        if _open(slot):
            return slot
    return None


def assign_auto_slots(session: Session, items: list[PublishingQueueItem], config: ScheduleConfigView) -> int:
    """Assign scheduled_at for approved auto-slot items without a time yet."""
    assigned = 0
    now = datetime.now(UTC)
    for item in items:
        if item.scheduled_at is not None:
            continue
        from instamagic.services.posting.reel_slot import take_source_reel_slot

        if take_source_reel_slot(session, item, config):
            item.schedule_mode = "auto_slot"
            session.flush()
            assigned += 1
            continue
        slot = next_available_slot(session, config, now=now, media_type=item.media_type, item=item)
        if slot is None:
            continue
        item.scheduled_at = slot
        item.schedule_mode = "auto_slot"
        session.flush()
        assigned += 1
    session.commit()
    from instamagic.services.posting.reel_slot import prepare_reel_versions_for

    prepare_reel_versions_for(session, items, config)
    return assigned
