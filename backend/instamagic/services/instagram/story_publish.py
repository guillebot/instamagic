"""Publish Instagram Stories after a successful feed post (optional, human-approved)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from instamagic.config import Settings
from instamagic.errors import InstagramNotConfigured, NotAllowed, NotConfigured, NotFound
from instamagic.models import Image, PublishAttempt, PublishingQueueItem, QueueState
from instamagic.models.enums import PublishAttemptKind, PublishTrigger, StoryTimingMode
from instamagic.services.blurs.gate_context import publishing_gate_kwargs
from instamagic.services.images.derivatives import oriented_rgb, read_image
from instamagic.services.images.paths import safe_join
from instamagic.services.instagram.assets import mint_publish_token, public_image_url
from instamagic.services.instagram.client import InstagramClient
from instamagic.services.instagram.publish_derivative import (
    _approved_blur_windows,
    _load_original_rgb,
    assert_publish_blur_integrity,
    blur_drawn_not_approved_reason,
    has_any_approved_blur,
)
from instamagic.services.instagram.story_image import (
    assert_story_uses_feed_source,
    render_story_from_feed_file,
    write_story_jpeg,
)
from instamagic.services.pipeline import is_attested
from instamagic.services.posting.story_options import effective_story_options
from instamagic.services.recommendations.gates import publishing_gate
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload


def schedule_story_after_feed(item: PublishingQueueItem, *, published_at: datetime) -> None:
    if not item.story_share_enabled or item.media_type == "CAROUSEL":
        return
    if item.story_timing_mode == StoryTimingMode.WITH_POST.value:
        item.story_scheduled_at = published_at
        return
    hours = item.story_delay_hours or 1
    hours = max(1, min(int(hours), 12))
    item.story_delay_hours = hours
    item.story_scheduled_at = published_at + timedelta(hours=hours)


def _next_attempt_number(session: Session, item_id: UUID, kind: str) -> int:
    prior = session.scalar(
        select(func.count())
        .select_from(PublishAttempt)
        .where(PublishAttempt.queue_item_id == item_id, PublishAttempt.kind == kind)
    )
    return int(prior or 0) + 1


def _story_relative_path(image_id: UUID) -> str:
    return f"{image_id}/publish/story.jpg"


def ensure_story_derivative(
    session: Session,
    settings: Settings,
    image: Image,
    item: PublishingQueueItem,
) -> str:
    if not item.feed_publish_relative_path:
        raise NotConfigured("Feed publish asset path is missing; cannot render story.")
    feed_path = safe_join(settings.derivatives_root, item.feed_publish_relative_path)
    opts = effective_story_options(session, item)
    story_rgb = render_story_from_feed_file(
        feed_path,
        mode=opts.render_mode,
        border_color=opts.border_color,
    )
    assert_story_uses_feed_source(feed_path, story_rgb, opts.render_mode)
    if has_any_approved_blur(image):
        feed_rgb = oriented_rgb(read_image(feed_path))
        original_rgb = _load_original_rgb(session, settings, image)
        windows = _approved_blur_windows(image)
        assert_publish_blur_integrity(
            original_rgb=original_rgb,
            publish_rgb=feed_rgb,
            windows=windows,
            crop_box=None,
        )
    relative = _story_relative_path(image.id)
    dest = settings.derivatives_root / relative
    dest.parent.mkdir(parents=True, exist_ok=True)
    write_story_jpeg(story_rgb, dest)
    return relative


def publish_story_for_queue_item(
    session: Session,
    settings: Settings,
    client: InstagramClient,
    *,
    item_id: UUID,
    trigger: str = PublishTrigger.MANUAL.value,
) -> PublishAttempt | None:
    if not client.configured():
        raise InstagramNotConfigured("Instagram Graph API is not configured.")
    item = session.get(PublishingQueueItem, item_id)
    if item is None:
        raise NotFound("Queue item was not found.")
    if not item.story_share_enabled or item.media_type == "CAROUSEL":
        return None
    if item.state != QueueState.PUBLISHED.value:
        return None
    if item.story_published_at is not None:
        return None
    if item.story_scheduled_at and item.story_scheduled_at > datetime.now(UTC):
        return None

    image = session.get(
        Image,
        item.image_id,
        options=(selectinload(Image.crops), selectinload(Image.blurs)),
    )
    if image is None:
        raise NotFound("Image was not found.")
    gate = publishing_gate(attested=is_attested(session, image.library_id), **publishing_gate_kwargs(session, image))
    blur_pending = blur_drawn_not_approved_reason(image)
    if not gate.allowed or blur_pending:
        reasons = list(gate.reasons)
        if blur_pending:
            reasons.append(blur_pending)
        msg = " ".join(reasons)
        item.story_last_error = msg
        session.commit()
        raise NotAllowed(msg)

    attempt = PublishAttempt(
        queue_item_id=item.id,
        attempt_number=_next_attempt_number(session, item.id, PublishAttemptKind.STORY.value),
        kind=PublishAttemptKind.STORY.value,
        trigger=trigger,
    )
    session.add(attempt)
    session.flush()

    try:
        limit = client.fetch_content_publishing_limit()
        if limit.usage >= limit.quota_total:
            msg = (
                f"Instagram content publishing limit reached ({limit.usage}/{limit.quota_total} "
                "in rolling window). Story was not published; feed post is unaffected."
            )
            attempt.finished_at = datetime.now(UTC)
            attempt.success = False
            attempt.error = msg
            item.story_last_error = msg
            session.commit()
            return attempt
    except Exception:
        pass

    try:
        relative = ensure_story_derivative(session, settings, image, item)
        public_base = settings.publish_public_base_url()
    except (NotAllowed, NotConfigured) as exc:
        err = str(exc.detail if hasattr(exc, "detail") else exc)
        attempt.finished_at = datetime.now(UTC)
        attempt.success = False
        attempt.error = err
        item.story_last_error = err
        session.commit()
        raise

    token = mint_publish_token(relative_path=relative, secret=settings.app_secret)
    image_url = public_image_url(public_base_url=public_base, token=token)
    if settings.instagram_provider == "fake" and public_base.startswith("http://"):
        image_url = public_image_url(public_base_url=public_base, token=token)

    try:
        result = client.publish_story(image_url=image_url)
    except Exception as exc:
        err = "Instagram story publish failed."
        if hasattr(exc, "detail"):
            err = str(exc.detail)
        attempt.finished_at = datetime.now(UTC)
        attempt.success = False
        attempt.error = err
        item.story_last_error = err
        session.commit()
        raise NotConfigured(err) from exc

    now = datetime.now(UTC)
    attempt.finished_at = now
    attempt.success = True
    attempt.ig_media_id = result.media_id
    attempt.ig_container_id = result.container_id
    item.story_published_at = now
    item.story_last_error = None
    item.story_scheduled_at = None
    session.commit()
    return attempt


def run_scheduled_story_tick(session: Session, settings: Settings, client: InstagramClient) -> int:
    if not client.configured():
        return 0
    now = datetime.now(UTC)
    stmt = (
        select(PublishingQueueItem)
        .where(
            PublishingQueueItem.state == QueueState.PUBLISHED.value,
            PublishingQueueItem.story_share_enabled.is_(True),
            PublishingQueueItem.story_published_at.is_(None),
            PublishingQueueItem.story_scheduled_at.is_not(None),
            PublishingQueueItem.story_scheduled_at <= now,
            PublishingQueueItem.media_type != "CAROUSEL",
        )
        .order_by(PublishingQueueItem.story_scheduled_at.asc())
        .limit(5)
    )
    if session.bind and session.bind.dialect.name == "postgresql":
        stmt = stmt.with_for_update(skip_locked=True)
    items = list(session.scalars(stmt).all())
    published = 0
    for item in items:
        try:
            attempt = publish_story_for_queue_item(
                session,
                settings,
                client,
                item_id=item.id,
                trigger=PublishTrigger.SCHEDULER.value,
            )
            if attempt and attempt.success:
                published += 1
        except Exception:
            continue
    return published
