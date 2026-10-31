"""Standalone Instagram Stories and Reels (human-approved, same gates as feed posts).

Safety model (identical to feed): every pixel that leaves the box comes from the *verified feed
publish derivative* produced by ``ensure_publish_derivative`` (DO_NOT_POST photos only through
their verified blurred render). Stories are a fit/pad 9:16 composition of that file; Reels are
zoom/crossfade frames of the same composition. Gates are re-applied right before upload and the
exact file that was rendered (and previewed) is the file that is uploaded via a short-lived
signed token.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from instamagic.config import Settings
from instamagic.errors import InstagramNotConfigured, NotAllowed, NotConfigured, NotFound
from instamagic.logging_config import get_logger
from instamagic.models import (
    Caption,
    CaptionStatus,
    Image,
    InstagramPost,
    PublishAttempt,
    PublishingQueueItem,
    PublishingQueueSlide,
    QueueState,
)
from instamagic.models.enums import (
    PublishAttemptKind,
    PublishTrigger,
    QueueMediaType,
    StoryRenderMode,
)
from instamagic.services.blurs.gate_context import publishing_gate_kwargs
from instamagic.services.blurs.unlock import blur_gate_passes
from instamagic.services.images.paths import safe_join
from instamagic.services.instagram.assets import (
    REEL_TTL_SECONDS,
    is_allowed_publish_relative_path,
    mint_publish_token,
    public_image_url,
)
from instamagic.services.instagram.client import InstagramClient, PublishResult
from instamagic.services.instagram.publish_derivative import (
    blur_drawn_not_approved_reason,
    ensure_publish_derivative,
)
from instamagic.services.instagram.reel_video import file_sha256, write_reel_mp4
from instamagic.services.instagram.story_image import (
    assert_story_file_matches_feed,
    assert_story_uses_feed_source,
    render_story_from_feed_file,
    write_story_jpeg,
)
from instamagic.services.pipeline import is_attested
from instamagic.services.posting.collaborators import item_collaborators
from instamagic.services.posting.publish_errors import (
    is_permanent_publish_error,
    is_transient_publish_error,
)
from instamagic.services.posting.story_options import effective_story_options
from instamagic.services.recommendations.gates import publishing_gate
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

log = get_logger(__name__)

STANDALONE_TYPES = {QueueMediaType.STORY.value, QueueMediaType.REELS.value}
REEL_MAX_PHOTOS = 10
REEL_STATUS_NONE = "none"
REEL_STATUS_RENDERING = "rendering"
REEL_STATUS_READY = "ready"
REEL_STATUS_FAILED = "failed"
REEL_STATUS_STALE = "stale"
REEL_RENDER_STUCK_AFTER = timedelta(minutes=10)


# ---------------------------------------------------------------------------------------------
# gates + sources
# ---------------------------------------------------------------------------------------------


def _load_image(session: Session, image_id: UUID) -> Image:
    image = session.get(
        Image,
        image_id,
        options=(selectinload(Image.crops), selectinload(Image.blurs), selectinload(Image.captions)),
    )
    if image is None:
        raise NotFound("Image was not found.")
    return image


def gate_image(session: Session, image: Image) -> None:
    """Same checks as feed publish/approve: rating/attestation gates, drawn blurs, verified covers."""
    gate = publishing_gate(attested=is_attested(session, image.library_id), **publishing_gate_kwargs(session, image))
    reasons = list(gate.reasons) if not gate.allowed else []
    blur_pending = blur_drawn_not_approved_reason(image)
    if blur_pending:
        reasons.append(blur_pending)
    if not blur_gate_passes(session, image):
        reasons.append("Blur covers must be approved and verified before this photo can be published.")
    if reasons:
        raise NotAllowed(" ".join(reasons))


def item_images(session: Session, item: PublishingQueueItem) -> list[Image]:
    if item.media_type == QueueMediaType.REELS.value:
        slides = sorted(
            session.scalars(
                select(PublishingQueueSlide).where(PublishingQueueSlide.queue_item_id == item.id)
            ).all(),
            key=lambda row: row.sort_order,
        )
        if slides:
            return [_load_image(session, slide.image_id) for slide in slides]
    return [_load_image(session, item.image_id)]


def _fit_mode(session: Session, item: PublishingQueueItem) -> tuple[str, str]:
    """Stories/Reels never crop: fit the photo with blurred or solid padding."""
    opts = effective_story_options(session, item)
    mode = opts.render_mode
    if mode not in {StoryRenderMode.BLUR_BACKDROP.value, StoryRenderMode.FIT_BORDER.value}:
        mode = StoryRenderMode.BLUR_BACKDROP.value
    return mode, opts.border_color


def _verified_feed_file(session: Session, settings: Settings, image: Image) -> tuple[str, str]:
    """Re-gate, rebuild the feed publish derivative (blur integrity check inside) and hash it."""
    gate_image(session, image)
    approved = next((crop for crop in image.crops if crop.approved), None)
    relative = ensure_publish_derivative(session, settings, image, approved)
    path = safe_join(settings.derivatives_root, relative)
    return relative, file_sha256(path)


# ---------------------------------------------------------------------------------------------
# queue item creation / conversion
# ---------------------------------------------------------------------------------------------


def _new_item(image: Image, media_type: str) -> PublishingQueueItem:
    return PublishingQueueItem(
        image_id=image.id,
        media_type=media_type,
        state=QueueState.CANDIDATE.value,
        story_share_enabled=False,
    )


def create_story_items(session: Session, image_ids: list[UUID]) -> list[PublishingQueueItem]:
    unique = list(dict.fromkeys(image_ids))
    if not 1 <= len(unique) <= 10:
        raise NotAllowed("Pick between 1 and 10 photos for stories.")
    images = [_load_image(session, image_id) for image_id in unique]
    for image in images:
        gate_image(session, image)
    items: list[PublishingQueueItem] = []
    for image in images:
        existing = session.scalar(
            select(PublishingQueueItem).where(
                PublishingQueueItem.image_id == image.id,
                PublishingQueueItem.media_type == QueueMediaType.STORY.value,
                PublishingQueueItem.state.in_([QueueState.CANDIDATE.value, QueueState.APPROVED.value]),
            )
        )
        if existing is not None:
            items.append(existing)
            continue
        item = _new_item(image, QueueMediaType.STORY.value)
        session.add(item)
        items.append(item)
    session.commit()
    for item in items:
        session.refresh(item)
    return items


def create_reel_item(session: Session, image_ids: list[UUID]) -> PublishingQueueItem:
    unique = list(dict.fromkeys(image_ids))
    if not 1 <= len(unique) <= REEL_MAX_PHOTOS:
        raise NotAllowed("A reel needs between 1 and 10 distinct photos.")
    images = [_load_image(session, image_id) for image_id in unique]
    for image in images:
        gate_image(session, image)
    item = _new_item(images[0], QueueMediaType.REELS.value)
    session.add(item)
    session.flush()
    for order, image in enumerate(images):
        approved = next((crop for crop in image.crops if crop.approved), None)
        session.add(
            PublishingQueueSlide(
                queue_item_id=item.id,
                image_id=image.id,
                crop_id=approved.id if approved else None,
                sort_order=order,
            )
        )
    session.commit()
    session.refresh(item)
    return item


def set_post_type(session: Session, item_id: UUID, media_type: str) -> PublishingQueueItem:
    """Switch a not-yet-published single-photo item between Feed / Story / Reel."""
    media_type = media_type.strip().upper()
    if media_type == "FEED":
        media_type = QueueMediaType.IMAGE.value
    if media_type not in {QueueMediaType.IMAGE.value, *STANDALONE_TYPES}:
        raise NotAllowed("Post type must be FEED, STORY or REELS.")
    item = session.get(PublishingQueueItem, item_id, options=(selectinload(PublishingQueueItem.slides),))
    if item is None:
        raise NotFound("Queue item was not found.")
    if item.state not in {QueueState.CANDIDATE.value, QueueState.APPROVED.value, QueueState.FAILED.value}:
        raise NotAllowed("Only unpublished queue items can change post type.")
    if item.media_type == QueueMediaType.CAROUSEL.value:
        raise NotAllowed("Carousels cannot change post type; create a reel from the selected photos instead.")
    if item.media_type == QueueMediaType.REELS.value and len(item.slides) > 1 and media_type != item.media_type:
        raise NotAllowed("Multi-photo reels cannot change post type.")
    if media_type == item.media_type:
        return item
    image = _load_image(session, item.image_id)
    gate_image(session, image)
    for slide in list(item.slides):
        session.delete(slide)
    session.flush()
    item.media_type = media_type
    # Changing what will be posted always needs a fresh human approval.
    item.state = QueueState.CANDIDATE.value
    item.approved_at = None
    item.schedule_mode = None
    item.scheduled_at = None
    item.publish_retry_after = None
    item.last_error = None
    _clear_reel(item)
    if media_type in STANDALONE_TYPES:
        item.story_share_enabled = False
    if media_type == QueueMediaType.STORY.value:
        item.caption_id = None
    if media_type == QueueMediaType.REELS.value:
        approved = next((crop for crop in image.crops if crop.approved), None)
        session.add(
            PublishingQueueSlide(
                queue_item_id=item.id,
                image_id=image.id,
                crop_id=approved.id if approved else None,
                sort_order=0,
            )
        )
    session.commit()
    session.refresh(item)
    return item


def _clear_reel(item: PublishingQueueItem) -> None:
    item.reel_status = REEL_STATUS_NONE
    item.reel_relative_path = None
    item.reel_sha256 = None
    item.reel_source_json = None
    item.reel_error = None
    item.reel_duration_s = None
    item.reel_rendered_at = None


# ---------------------------------------------------------------------------------------------
# Story render
# ---------------------------------------------------------------------------------------------


def story_item_relative_path(item: PublishingQueueItem) -> str:
    return f"{item.image_id}/publish/story-{item.id.hex}.jpg"


def render_story_item(session: Session, settings: Settings, item: PublishingQueueItem) -> str:
    """Render (or re-render) the exact Story JPEG for a STORY item; returns derivatives-relative path."""
    if item.media_type != QueueMediaType.STORY.value:
        raise NotAllowed("Not a story queue item.")
    image = _load_image(session, item.image_id)
    feed_relative, _sha = _verified_feed_file(session, settings, image)
    feed_path = safe_join(settings.derivatives_root, feed_relative)
    mode, border = _fit_mode(session, item)
    story_rgb = render_story_from_feed_file(feed_path, mode=mode, border_color=border)
    assert_story_uses_feed_source(feed_path, story_rgb, mode)
    relative = story_item_relative_path(item)
    dest = safe_join(settings.derivatives_root, relative)
    dest.parent.mkdir(parents=True, exist_ok=True)
    write_story_jpeg(story_rgb, dest)
    try:
        assert_story_file_matches_feed(dest, feed_path, mode=mode, border_color=border)
    except NotAllowed:
        dest.unlink(missing_ok=True)
        raise
    return relative


# ---------------------------------------------------------------------------------------------
# Reel render
# ---------------------------------------------------------------------------------------------


def reel_item_relative_path(item: PublishingQueueItem) -> str:
    return f"{item.image_id}/publish/reel-{item.id.hex}.mp4"


def _reel_sources(session: Session, settings: Settings, item: PublishingQueueItem) -> list[dict[str, str]]:
    sources = []
    for image in item_images(session, item):
        relative, sha = _verified_feed_file(session, settings, image)
        sources.append({"image_id": str(image.id), "feed": relative, "sha256": sha})
    return sources


def mark_reel_rendering(session: Session, item_id: UUID) -> PublishingQueueItem:
    item = session.get(PublishingQueueItem, item_id)
    if item is None:
        raise NotFound("Queue item was not found.")
    if item.media_type != QueueMediaType.REELS.value:
        raise NotAllowed("Not a reel queue item.")
    if item.state not in {QueueState.CANDIDATE.value, QueueState.FAILED.value}:
        raise NotAllowed("Only candidate (not yet approved) reels can be rendered.")
    if item.reel_status == REEL_STATUS_RENDERING and item.reel_rendered_at is not None:
        started = item.reel_rendered_at if item.reel_rendered_at.tzinfo else item.reel_rendered_at.replace(tzinfo=UTC)
        if datetime.now(UTC) - started < REEL_RENDER_STUCK_AFTER:
            raise NotAllowed("This reel is already rendering.")
    # Fail fast on gates before starting background work.
    for image in item_images(session, item):
        gate_image(session, image)
    item.reel_status = REEL_STATUS_RENDERING
    item.reel_error = None
    item.reel_rendered_at = datetime.now(UTC)  # render start; replaced on success
    session.commit()
    session.refresh(item)
    return item


def render_reel_item(session: Session, settings: Settings, item_id: UUID) -> PublishingQueueItem:
    """Render the Reel MP4 from verified story frames. Records sha + source fingerprint."""
    item = session.get(PublishingQueueItem, item_id)
    if item is None:
        raise NotFound("Queue item was not found.")
    if item.media_type != QueueMediaType.REELS.value:
        raise NotAllowed("Not a reel queue item.")
    try:
        sources = _reel_sources(session, settings, item)
        mode, border = _fit_mode(session, item)
        frames = []
        for source in sources:
            feed_path = safe_join(settings.derivatives_root, source["feed"])
            frame = render_story_from_feed_file(feed_path, mode=mode, border_color=border)
            assert_story_uses_feed_source(feed_path, frame, mode)
            frames.append(frame)
        relative = reel_item_relative_path(item)
        dest = safe_join(settings.derivatives_root, relative)
        result = write_reel_mp4(frames, dest)
        # Sources must not have changed while we were encoding.
        if _reel_sources(session, settings, item) != sources:
            dest.unlink(missing_ok=True)
            raise NotAllowed("Photos or blurs changed while the reel was rendering; render again.")
    except Exception as exc:
        session.rollback()
        item = session.get(PublishingQueueItem, item_id)
        if item is None:
            raise
        item.reel_status = REEL_STATUS_FAILED
        item.reel_error = str(getattr(exc, "detail", None) or exc)[:500] or "Reel render failed."
        item.reel_relative_path = None
        item.reel_sha256 = None
        session.commit()
        log.info("reel_render_failed", queue_item_id=str(item_id), error_type=type(exc).__name__)
        return item
    item.reel_status = REEL_STATUS_READY
    item.reel_relative_path = relative
    item.reel_sha256 = result.sha256
    item.reel_source_json = json.dumps({"mode": mode, "border": border, "sources": sources}, sort_keys=True)
    item.reel_duration_s = result.duration_s
    item.reel_rendered_at = datetime.now(UTC)
    item.reel_error = None
    session.commit()
    session.refresh(item)
    log.info("reel_rendered", queue_item_id=str(item_id), duration_s=result.duration_s, photos=len(sources))
    return item


def run_reel_render(settings: Settings, item_id: UUID) -> None:
    """Background entry point (own DB session)."""
    from instamagic.db import session_factory

    with session_factory()() as session:
        render_reel_item(session, settings, item_id)


def assert_reel_fresh(session: Session, settings: Settings, item: PublishingQueueItem) -> str:
    """The stored MP4 must be byte-identical to the render and built from today's verified sources."""
    if item.reel_status != REEL_STATUS_READY or not item.reel_relative_path or not item.reel_sha256:
        raise NotAllowed("Render the reel video (and check the preview) before approving or publishing.")
    try:
        recorded = json.loads(item.reel_source_json or "{}")
    except json.JSONDecodeError:
        recorded = {}
    mode, border = _fit_mode(session, item)
    current = _reel_sources(session, settings, item)
    if recorded.get("sources") != current or recorded.get("mode") != mode or recorded.get("border") != border:
        item.reel_status = REEL_STATUS_STALE
        session.commit()
        raise NotAllowed("Photos, blurs or render options changed since the reel was rendered; render it again.")
    path = safe_join(settings.derivatives_root, item.reel_relative_path)
    if not path.is_file() or file_sha256(path) != item.reel_sha256:
        item.reel_status = REEL_STATUS_STALE
        session.commit()
        raise NotAllowed("The rendered reel file changed or is missing; render it again.")
    return item.reel_relative_path


def _reel_cover_url(item: PublishingQueueItem, settings: Settings, public_base: str, token_kwargs: dict) -> str | None:
    """Cover = the first slide's verified feed derivative (the same gated/blurred render)."""
    try:
        recorded = json.loads(item.reel_source_json or "{}")
        feed = recorded.get("sources", [])[0]["feed"]
    except (ValueError, LookupError, TypeError):
        return None
    if not feed or not is_allowed_publish_relative_path(feed):
        return None
    token = mint_publish_token(relative_path=feed, secret=settings.app_secret, **token_kwargs)
    return public_image_url(public_base_url=public_base, token=token)


def assert_ready_for_approval(session: Session, settings: Settings, item: PublishingQueueItem) -> None:
    if item.media_type == QueueMediaType.REELS.value:
        assert_reel_fresh(session, settings, item)
    elif item.media_type == QueueMediaType.STORY.value:
        render_story_item(session, settings, item)


# ---------------------------------------------------------------------------------------------
# publish
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class MediaPublishOutcome:
    queue_item_id: UUID
    post_id: UUID | None
    ig_media_id: str
    permalink: str | None
    caption: str


def final_caption_text(session: Session, item: PublishingQueueItem, caption: Caption) -> str:
    """Caption exactly as the feed pipeline publishes it: text + blocklist-filtered hashtags."""
    from instamagic.services.instagram.publish import publish_caption_text

    image = session.get(Image, caption.image_id)
    if image is None:
        raise NotFound("Caption image was not found.")
    return publish_caption_text(caption, image, session)


def _next_attempt_number(session: Session, item_id: UUID, kind: str) -> int:
    prior = session.scalar(
        select(func.count())
        .select_from(PublishAttempt)
        .where(PublishAttempt.queue_item_id == item_id, PublishAttempt.kind == kind)
    )
    return int(prior or 0) + 1


def _check_quota(client: InstagramClient) -> None:
    try:
        limit = client.fetch_content_publishing_limit()
    except Exception:
        return
    if limit.usage >= limit.quota_total:
        raise NotAllowed(
            f"Instagram content publishing limit reached ({limit.usage}/{limit.quota_total} in the rolling window)."
        )


def publish_media_item(
    session: Session,
    settings: Settings,
    client: InstagramClient,
    *,
    item: PublishingQueueItem,
    caption_id: UUID | None,
    trigger: str,
) -> MediaPublishOutcome:
    is_story = item.media_type == QueueMediaType.STORY.value
    kind = PublishAttemptKind.STORY.value if is_story else PublishAttemptKind.FEED.value
    attempt = PublishAttempt(
        queue_item_id=item.id,
        attempt_number=_next_attempt_number(session, item.id, kind),
        kind=kind,
        trigger=trigger,
    )
    session.add(attempt)
    session.flush()

    def _fail(exc: BaseException, message: str) -> None:
        attempt.finished_at = datetime.now(UTC)
        attempt.success = False
        attempt.error = message
        if trigger == PublishTrigger.SCHEDULER.value and is_transient_publish_error(exc):
            item.state = QueueState.APPROVED.value
            item.last_error = message
            item.publish_retry_after = datetime.now(UTC) + timedelta(minutes=15)
        else:
            item.state = QueueState.FAILED.value
            item.last_error = message
            if is_permanent_publish_error(exc) or trigger != PublishTrigger.SCHEDULER.value:
                item.schedule_mode = None
                item.scheduled_at = None

    caption: Caption | None = None
    caption_text = ""
    try:
        if not is_story:
            resolved = caption_id or item.caption_id
            if resolved is None:
                raise NotAllowed("A caption must be selected before publishing.")
            caption = session.get(Caption, resolved)
            if caption is None or caption.image_id != item.image_id:
                raise NotFound("Caption was not found for this image.")
            caption_text = final_caption_text(session, item, caption)
            if not caption_text:
                raise NotAllowed("Caption text is empty.")
        _check_quota(client)
        if is_story:
            relative = render_story_item(session, settings, item)
            ttl = None
        else:
            relative = assert_reel_fresh(session, settings, item)
            ttl = REEL_TTL_SECONDS
        public_base = settings.publish_public_base_url()
        if not public_base.startswith("https://") and settings.instagram_provider == "graph":
            raise NotConfigured("PUBLIC_BASE_URL must be an https URL Meta can fetch from the public internet.")
    except ValueError as exc:
        err = str(exc)
        _fail(NotConfigured(err), err)
        session.commit()
        raise NotConfigured(err) from exc
    except (NotAllowed, NotFound, NotConfigured) as exc:
        err = str(getattr(exc, "detail", None) or exc)
        _fail(exc, err)
        session.commit()
        raise

    token_kwargs = {"ttl_seconds": ttl} if ttl else {}
    token = mint_publish_token(relative_path=relative, secret=settings.app_secret, **token_kwargs)
    asset_url = public_image_url(public_base_url=public_base, token=token)

    item.state = QueueState.PUBLISHING.value
    if caption is not None:
        item.caption_id = caption.id
    item.last_error = None
    session.commit()

    try:
        if is_story:
            result: PublishResult = client.publish_story(image_url=asset_url)
        else:
            result = client.publish_reel(
                video_url=asset_url,
                caption=caption_text,
                cover_url=_reel_cover_url(item, settings, public_base, token_kwargs),
                collaborators=item_collaborators(item) or None,
            )
    except (InstagramNotConfigured, NotConfigured, NotAllowed) as exc:
        err = str(getattr(exc, "detail", None) or exc)
        _fail(exc, err)
        session.commit()
        raise
    except Exception as exc:
        err = "Instagram story publish failed." if is_story else "Instagram reel publish failed."
        _fail(NotConfigured(err) if not is_transient_publish_error(exc) else exc, err)
        session.commit()
        raise NotConfigured(err) from None

    now = datetime.now(UTC)
    attempt.finished_at = now
    attempt.success = True
    attempt.ig_media_id = result.media_id
    attempt.ig_container_id = result.container_id
    item.state = QueueState.PUBLISHED.value
    item.published_at = now
    item.last_error = None
    item.schedule_mode = None
    item.scheduled_at = None
    item.publish_retry_after = None
    post_id: UUID | None = None
    if is_story:
        item.story_published_at = now
    else:
        assert caption is not None
        caption.status = CaptionStatus.USED.value
        post = InstagramPost(
            image_id=item.image_id,
            queue_item_id=item.id,
            ig_media_id=result.media_id,
            ig_container_id=result.container_id,
            media_type=QueueMediaType.REELS.value,
            caption=caption_text,
            status="published",
            published_at=now,
            permalink=result.permalink,
        )
        session.add(post)
        session.flush()
        post_id = post.id
    session.commit()
    return MediaPublishOutcome(
        queue_item_id=item.id,
        post_id=post_id,
        ig_media_id=result.media_id,
        permalink=result.permalink,
        caption=caption_text,
    )
