"""DB-backed posting schedule configuration (singleton row)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from instamagic.models import PostingScheduleConfig
from sqlalchemy.orm import Session

DEFAULT_TIMEZONE = "America/Buenos_Aires"

# Local posting windows per weekday name (lowercase). Hook for best_slots() defaults.
DEFAULT_WEEKDAY_SLOTS: dict[str, list[str]] = {
    "monday": ["10:00", "19:00"],
    "tuesday": ["10:00", "19:00"],
    "wednesday": ["10:00", "19:00"],
    "thursday": ["10:00", "19:00"],
    "friday": ["10:00", "19:00"],
    "saturday": ["11:00"],
    "sunday": ["11:00"],
}


@dataclass(frozen=True)
class ScheduleConfigView:
    timezone: str
    weekday_slots: dict[str, list[str]]
    max_posts_per_day: int
    min_gap_hours: float
    paused: bool
    story_share_default: bool
    story_render_mode_default: str
    story_border_color_default: str
    updated_at: datetime | None


def _parse_slots(raw: str) -> dict[str, list[str]]:
    try:
        payload = json.loads(raw or "{}")
    except json.JSONDecodeError:
        payload = {}
    if not isinstance(payload, dict):
        return dict(DEFAULT_WEEKDAY_SLOTS)
    out: dict[str, list[str]] = {}
    for day, times in payload.items():
        if not isinstance(day, str) or not isinstance(times, list):
            continue
        cleaned = [str(t).strip() for t in times if str(t).strip()]
        if cleaned:
            out[day.lower()] = cleaned
    return out or dict(DEFAULT_WEEKDAY_SLOTS)


def get_or_create_config(session: Session) -> PostingScheduleConfig:
    row = session.get(PostingScheduleConfig, "default")
    if row is None:
        row = PostingScheduleConfig(
            id="default",
            timezone=DEFAULT_TIMEZONE,
            weekday_slots_json=json.dumps(DEFAULT_WEEKDAY_SLOTS),
            max_posts_per_day=2,
            min_gap_hours=4.0,
            paused=False,
            story_share_default=False,
            story_render_mode_default="blur_backdrop",
            story_border_color_default="black",
        )
        session.add(row)
        session.commit()
        session.refresh(row)
    elif not row.weekday_slots_json or row.weekday_slots_json == "{}":
        row.weekday_slots_json = json.dumps(DEFAULT_WEEKDAY_SLOTS)
        session.commit()
    return row


def config_view(session: Session) -> ScheduleConfigView:
    row = get_or_create_config(session)
    return ScheduleConfigView(
        timezone=row.timezone,
        weekday_slots=_parse_slots(row.weekday_slots_json),
        max_posts_per_day=row.max_posts_per_day,
        min_gap_hours=row.min_gap_hours,
        paused=row.paused,
        story_share_default=bool(row.story_share_default),
        story_render_mode_default=row.story_render_mode_default,
        story_border_color_default=row.story_border_color_default,
        updated_at=row.updated_at,
    )


def update_config(
    session: Session,
    *,
    timezone: str | None = None,
    weekday_slots: dict[str, list[str]] | None = None,
    max_posts_per_day: int | None = None,
    min_gap_hours: float | None = None,
    paused: bool | None = None,
    story_share_default: bool | None = None,
    story_render_mode_default: str | None = None,
    story_border_color_default: str | None = None,
) -> ScheduleConfigView:
    row = get_or_create_config(session)
    if timezone is not None:
        row.timezone = timezone.strip() or DEFAULT_TIMEZONE
    if weekday_slots is not None:
        row.weekday_slots_json = json.dumps(weekday_slots)
    if max_posts_per_day is not None:
        row.max_posts_per_day = max(1, min(max_posts_per_day, 24))
    if min_gap_hours is not None:
        row.min_gap_hours = max(0.5, float(min_gap_hours))
    if paused is not None:
        row.paused = paused
    if story_share_default is not None:
        row.story_share_default = story_share_default
    if story_render_mode_default is not None:
        row.story_render_mode_default = story_render_mode_default.strip() or "blur_backdrop"
    if story_border_color_default is not None:
        color = story_border_color_default.strip().lower()
        row.story_border_color_default = color if color in {"black", "white"} else "black"
    session.commit()
    return config_view(session)


def config_as_dict(view: ScheduleConfigView) -> dict[str, Any]:
    return {
        "timezone": view.timezone,
        "weekday_slots": view.weekday_slots,
        "max_posts_per_day": view.max_posts_per_day,
        "min_gap_hours": view.min_gap_hours,
        "paused": view.paused,
        "story_share_default": view.story_share_default,
        "story_render_mode_default": view.story_render_mode_default,
        "story_border_color_default": view.story_border_color_default,
        "updated_at": view.updated_at,
    }
