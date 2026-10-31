"""Posting slot selection — defaults from config; override best_slots() later for insights."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from instamagic.models import PublishingQueueItem, QueueMediaType, QueueState
from instamagic.services.posting.schedule_config import ScheduleConfigView
from sqlalchemy import func, select
from sqlalchemy.orm import Session


def _daily_times(config: ScheduleConfigView, lane: str, day: datetime) -> list[str]:
    if lane == "reel":
        return list(getattr(config, "reel_slots", None) or [])
    return list(config.weekday_slots.get(day.strftime("%A").lower(), []))


def best_slots(
    config: ScheduleConfigView, *, from_day: datetime | None = None, days: int = 14, lane: str = "feed"
) -> list[datetime]:
    """Return candidate UTC datetimes for the next `days` calendar days in config timezone.

    Feed (photos/carousels) and Stories use the weekday slots; Reels use their own daily Reel slots.
    """
    if lane == "reel" and not getattr(config, "reel_slot_enabled", True):
        return []
    tz = ZoneInfo(config.timezone)
    start_local = (from_day or datetime.now(UTC)).astimezone(tz).replace(hour=0, minute=0, second=0, microsecond=0)
    slots: list[datetime] = []
    for offset in range(days):
        day = start_local + timedelta(days=offset)
        for hhmm in _daily_times(config, lane, day):
            try:
                hour, minute = hhmm.split(":", 1)
                local = day.replace(hour=int(hour), minute=int(minute))
                slots.append(local.astimezone(UTC))
            except (ValueError, TypeError):
                continue
    return sorted(set(slots))


def _lane_filter(lane: str):
    """Three independent lanes: Stories, Reels, and the feed (photos + carousels).

    Each lane has its own slots, daily cap and gap: Reels never count toward the feed's posts/day
    or min gap, and feed photo slots are never given to (or converted into) Reels.
    """
    if lane == "story":
        return PublishingQueueItem.media_type == QueueMediaType.STORY.value
    if lane == "reel":
        return PublishingQueueItem.media_type == QueueMediaType.REELS.value
    return PublishingQueueItem.media_type.not_in([QueueMediaType.STORY.value, QueueMediaType.REELS.value])


def lane_for_media_type(media_type: str | None) -> str:
    if media_type == QueueMediaType.STORY.value:
        return "story"
    if media_type == QueueMediaType.REELS.value:
        return "reel"
    return "feed"


REEL_MIN_GAP = timedelta(hours=1)


def lane_cap(config: ScheduleConfigView, lane: str) -> int:
    if lane == "story":
        return int(getattr(config, "max_stories_per_day", 2) or 2)
    if lane == "reel":
        return int(getattr(config, "reels_per_day", 2) or 2)
    return config.max_posts_per_day


def lane_gap(config: ScheduleConfigView, lane: str) -> timedelta:
    if lane == "reel":
        return REEL_MIN_GAP
    return timedelta(hours=config.min_gap_hours)


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


def next_available_slot(
    session: Session,
    config: ScheduleConfigView,
    now: datetime | None = None,
    *,
    media_type: str | None = None,
    item: PublishingQueueItem | None = None,
) -> datetime | None:
    """Pick the earliest open slot in the item's lane, respecting that lane's daily cap and gap.

    The gap is checked against neighbouring posts of the same lane on both sides (scheduled or
    already published), so empty slots before a later scheduled item get filled instead of always
    appending after the last scheduled post. Existing items are never moved. Reels only go to the
    Reel slots; photos/carousels only to the feed slots. Feed skin/body posts (``item`` given) also
    respect the 1-in-5 cap.
    """
    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    lane = lane_for_media_type(media_type)
    candidates = [s for s in best_slots(config, from_day=now, days=21, lane=lane) if s > now]
    if not candidates:
        return None
    min_gap = lane_gap(config, lane)
    busy = _busy_times(session, candidates[0] - min_gap, candidates[-1] + min_gap, lane=lane)
    cap = lane_cap(config, lane)
    for slot in candidates:
        if any(abs(slot - b) < min_gap for b in busy):
            continue
        if _scheduled_today_count(session, config, slot, lane=lane) >= cap:
            continue
        if item is not None and lane == "feed":
            from instamagic.services.posting.content_mix import sensitive_slot_allowed

            if not sensitive_slot_allowed(session, item, slot):
                continue
        return slot
    return None


def assign_auto_slots(session: Session, items: list[PublishingQueueItem], config: ScheduleConfigView) -> int:
    """Assign scheduled_at for approved auto-slot items without a time yet (each in its own lane)."""
    assigned = 0
    now = datetime.now(UTC)
    for item in items:
        if item.scheduled_at is not None:
            continue
        slot = next_available_slot(session, config, now=now, media_type=item.media_type, item=item)
        if slot is None:
            continue
        item.scheduled_at = slot
        item.schedule_mode = "auto_slot"
        session.flush()
        assigned += 1
    session.commit()
    return assigned
