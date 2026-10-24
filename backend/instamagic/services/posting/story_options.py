"""Resolve per-queue-item Instagram Story options with global defaults."""

from __future__ import annotations

from dataclasses import dataclass

from instamagic.models import PublishingQueueItem
from instamagic.models.enums import StoryRenderMode, StoryTimingMode
from instamagic.services.posting.schedule_config import get_or_create_config
from sqlalchemy.orm import Session


@dataclass(frozen=True)
class StoryOptions:
    share_enabled: bool
    render_mode: str
    border_color: str
    timing_mode: str
    delay_hours: int | None


def effective_story_options(session: Session, item: PublishingQueueItem) -> StoryOptions:
    cfg = get_or_create_config(session)
    render = (
        item.story_render_mode
        or cfg.story_render_mode_default
        or StoryRenderMode.BLUR_BACKDROP.value
    )
    if render not in StoryRenderMode._value2member_map_:
        render = StoryRenderMode.BLUR_BACKDROP.value
    border = (item.story_border_color or cfg.story_border_color_default or "black").strip().lower()
    if border not in {"black", "white"}:
        border = "black"
    timing = item.story_timing_mode or StoryTimingMode.WITH_POST.value
    delay = item.story_delay_hours
    if timing == StoryTimingMode.DELAY_HOURS.value and delay is not None:
        delay = max(1, min(int(delay), 12))
    return StoryOptions(
        share_enabled=bool(item.story_share_enabled),
        render_mode=render,
        border_color=border,
        timing_mode=timing,
        delay_hours=delay,
    )


def apply_story_defaults_to_new_item(session: Session, item: PublishingQueueItem) -> None:
    cfg = get_or_create_config(session)
    if item.story_share_enabled:
        return
    item.story_share_enabled = bool(cfg.story_share_default)
    if not item.story_render_mode:
        item.story_render_mode = None
    if not item.story_timing_mode:
        item.story_timing_mode = StoryTimingMode.WITH_POST.value
