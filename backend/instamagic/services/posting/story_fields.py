"""Apply optional story fields from API payloads onto queue items."""

from __future__ import annotations

from instamagic.models import PublishingQueueItem
from instamagic.models.enums import StoryRenderMode, StoryTimingMode
from instamagic.services.posting.story_options import apply_story_defaults_to_new_item
from sqlalchemy.orm import Session


def _clean_render_mode(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip().lower()
    if text in StoryRenderMode._value2member_map_:
        return text
    return None


def _clean_timing(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip().lower()
    if text in StoryTimingMode._value2member_map_:
        return text
    return None


def apply_story_fields_from_payload(
    session: Session,
    item: PublishingQueueItem,
    *,
    story_share_enabled: bool | None,
    story_render_mode: str | None = None,
    story_border_color: str | None = None,
    story_timing_mode: str | None = None,
    story_delay_hours: int | None = None,
    is_new: bool = False,
) -> None:
    if is_new:
        apply_story_defaults_to_new_item(session, item)
    if story_share_enabled is not None:
        item.story_share_enabled = story_share_enabled
    mode = _clean_render_mode(story_render_mode)
    if story_render_mode is not None:
        item.story_render_mode = mode
    if story_border_color is not None:
        color = story_border_color.strip().lower()
        item.story_border_color = color if color in {"black", "white"} else None
    timing = _clean_timing(story_timing_mode)
    if story_timing_mode is not None and timing:
        item.story_timing_mode = timing
    if story_delay_hours is not None:
        item.story_delay_hours = max(1, min(int(story_delay_hours), 12))
