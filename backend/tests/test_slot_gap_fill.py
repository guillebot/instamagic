"""Auto-slot picks the earliest open slot, filling gaps before later scheduled posts."""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from instamagic.services.posting import slots as slots_mod
from instamagic.services.posting.schedule_config import ScheduleConfigView

TZ = ZoneInfo("America/Buenos_Aires")
SLOTS = ["04:00", "09:00", "11:00", "13:00", "15:00", "17:00", "19:00", "21:00"]
DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def _config(max_per_day: int = 8, gap: float = 1.5) -> ScheduleConfigView:
    return ScheduleConfigView(
        timezone="America/Buenos_Aires",
        weekday_slots={d: list(SLOTS) for d in DAYS},
        max_posts_per_day=max_per_day,
        min_gap_hours=gap,
        paused=False,
        story_share_default=False,
        story_render_mode_default="blur_backdrop",
        story_border_color_default="black",
        blur_coverage_threshold=95,
        blur_region_padding_px=6,
        updated_at=None,
    )


def _local(y, m, d, h, mi=0) -> datetime:
    return datetime(y, m, d, h, mi, tzinfo=TZ).astimezone(UTC)


def _patch(monkeypatch, busy: list[datetime], per_day: dict | None = None) -> None:
    monkeypatch.setattr(slots_mod, "_busy_times", lambda session, start, end, lane="feed": list(busy))

    def count(session, config, when, lane="feed"):
        return (per_day or {}).get(when.astimezone(TZ).date(), 0)

    monkeypatch.setattr(slots_mod, "_scheduled_today_count", count)


def test_fills_gap_before_later_scheduled_post(monkeypatch) -> None:
    now = _local(2026, 10, 24, 10, 15)
    busy = [_local(2026, 10, 24, 11), _local(2026, 10, 25, 11), _local(2026, 10, 26, 10)]
    _patch(monkeypatch, busy)
    slot = slots_mod.next_available_slot(None, _config(), now=now)
    assert slot == _local(2026, 10, 24, 13)


def test_skips_slot_within_min_gap_of_neighbour(monkeypatch) -> None:
    now = _local(2026, 10, 26, 0, 0)
    busy = [_local(2026, 10, 26, 4), _local(2026, 10, 26, 10)]
    _patch(monkeypatch, busy)
    # 04:00 taken; 09:00 and 11:00 are within 1.5h of the 10:00 post.
    assert slots_mod.next_available_slot(None, _config(), now=now) == _local(2026, 10, 26, 13)


def test_respects_daily_max(monkeypatch) -> None:
    now = _local(2026, 10, 24, 10, 15)
    _patch(monkeypatch, [], per_day={datetime(2026, 10, 24).date(): 8})
    assert slots_mod.next_available_slot(None, _config(), now=now) == _local(2026, 10, 25, 4)
