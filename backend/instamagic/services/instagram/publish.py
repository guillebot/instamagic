"""Human-gated publish orchestration: re-check gates, mint public URL, call Graph API."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from instamagic.config import Settings
from instamagic.errors import InstagramNotConfigured, NotAllowed, NotConfigured, NotFound
from instamagic.models import (
    Caption,
    CaptionStatus,
    Image,
    InstagramPost,
    PublishAttempt,
    PublishingQueueItem,
    QueueState,
)
from instamagic.models.enums import PublishTrigger
from instamagic.services.blurs.gate_context import publishing_gate_kwargs
from instamagic.services.instagram.assets import mint_publish_token, public_image_url
from instamagic.services.instagram.client import InstagramClient, PublishResult
from instamagic.services.instagram.publish_derivative import (
    blur_drawn_not_approved_reason,
    ensure_publish_derivative,
)
from instamagic.services.pipeline import is_attested
from instamagic.services.posting.publish_errors import (
    is_permanent_publish_error,
    is_transient_publish_error,
)
from instamagic.services.recommendations.gates import publishing_gate
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload


@dataclass(frozen=True)
class PublishOutcome:
    queue_item_id: UUID
    post_id: UUID
    ig_media_id: str
    permalink: str | None
    caption: str


def publish_queue_item(
    session: Session,
    settings: Settings,
    client: InstagramClient,
    *,
    item_id: UUID,
    caption_id: UUID | None = None,
    trigger: str = PublishTrigger.MANUAL.value,
) -> PublishOutcome:
    if not client.configured():
        raise InstagramNotConfigured(
            "Instagram Graph API is not configured. Connect on /instagram or use "
            "INSTAGRAM_PROVIDER=fake for local tests."
        )
    item = session.get(
        PublishingQueueItem,
        item_id,
        options=(selectinload(PublishingQueueItem.slides),),
    )
    if item is None:
        raise NotFound("Queue item was not found.")
    if item.state not in {QueueState.APPROVED.value, QueueState.FAILED.value, QueueState.PUBLISHING.value}:
        raise NotAllowed("Only an approved (or previously failed) queue item can be published.")
    if item.state == QueueState.PUBLISHING.value and trigger != PublishTrigger.SCHEDULER.value:
        raise NotAllowed("This queue item is already publishing.")
    resolved_caption_id = caption_id or item.caption_id
    if resolved_caption_id is None:
        raise NotAllowed("A caption must be selected before publishing.")
    prior_attempts = session.scalar(
        select(func.count()).select_from(PublishAttempt).where(PublishAttempt.queue_item_id == item.id)
    )
    attempt_number = int(prior_attempts or 0) + 1
    attempt = PublishAttempt(
        queue_item_id=item.id,
        attempt_number=attempt_number,
        trigger=trigger,
    )
    session.add(attempt)
    session.flush()
    image = session.get(
        Image,
        item.image_id,
        options=(
            selectinload(Image.crops),
            selectinload(Image.blurs),
            selectinload(Image.captions),
        ),
    )
    if image is None:
        raise NotFound("Image was not found.")
    caption = session.get(Caption, resolved_caption_id)
    if caption is None or caption.image_id != image.id:
        raise NotFound("Caption was not found for this image.")
    if not caption.text.strip():
        raise NotAllowed("Caption text is empty.")

    approved = next((crop for crop in image.crops if crop.approved), None)
    gate = publishing_gate(attested=is_attested(session, image.library_id), **publishing_gate_kwargs(session, image))
    blur_pending = blur_drawn_not_approved_reason(image)
    if not gate.allowed or blur_pending:
        reasons = list(gate.reasons)
        if blur_pending:
            reasons.append(blur_pending)
        msg = " ".join(reasons)
        attempt.finished_at = datetime.now(UTC)
        attempt.success = False
        attempt.error = msg
        session.commit()
        raise NotAllowed(msg)

    if item.media_type == "CAROUSEL" and item.slides:
        return _publish_carousel_item(
            session,
            settings,
            client,
            item=item,
            caption=caption,
            attempt=attempt,
            trigger=trigger,
        )

    relative = ensure_publish_derivative(session, settings, image, approved)
    try:
        public_base = settings.publish_public_base_url()
    except ValueError as exc:
        raise NotConfigured(str(exc)) from exc
    if not public_base.startswith("https://") and settings.instagram_provider == "graph":
        raise NotConfigured(
            "PUBLIC_BASE_URL must be an https URL Meta can fetch from the public internet."
        )
    token = mint_publish_token(relative_path=relative, secret=settings.app_secret)
    image_url = public_image_url(public_base_url=public_base, token=token)
    # Fake provider accepts http for CI (TestClient / localhost).
    if settings.instagram_provider == "fake" and public_base.startswith("http://"):
        image_url = public_image_url(public_base_url=public_base, token=token)

    item.state = QueueState.PUBLISHING.value
    item.caption_id = caption.id
    item.last_error = None
    session.commit()

    def _fail_publish(exc: BaseException, message: str) -> None:
        attempt.finished_at = datetime.now(UTC)
        attempt.success = False
        attempt.error = message
        if trigger == PublishTrigger.SCHEDULER.value and is_transient_publish_error(exc):
            item.state = QueueState.APPROVED.value
            item.last_error = message
            from datetime import timedelta

            item.publish_retry_after = datetime.now(UTC) + timedelta(minutes=15)
        else:
            item.state = QueueState.FAILED.value
            item.last_error = message
            if is_permanent_publish_error(exc) or trigger != PublishTrigger.SCHEDULER.value:
                item.schedule_mode = None
                item.scheduled_at = None

    try:
        result = client.publish_image(image_url=image_url, caption=caption.text.strip())
    except (InstagramNotConfigured, NotConfigured, NotAllowed) as exc:
        err = str(exc.detail if hasattr(exc, "detail") else exc)
        _fail_publish(exc, err)
        session.commit()
        raise
    except Exception as exc:
        err = "Instagram publish failed."
        wrapped = NotConfigured(err)
        _fail_publish(wrapped if not is_transient_publish_error(exc) else exc, err)
        session.commit()
        raise NotConfigured(err) from None

    outcome = _record_success(session, item, image, caption, result)
    attempt.finished_at = datetime.now(UTC)
    attempt.success = True
    item.schedule_mode = None
    item.scheduled_at = None
    item.publish_retry_after = None
    session.commit()
    return outcome


def _record_success(
    session: Session,
    item: PublishingQueueItem,
    image: Image,
    caption: Caption,
    result: PublishResult,
    *,
    media_type: str = "IMAGE",
) -> PublishOutcome:
    now = datetime.now(UTC)
    item.state = QueueState.PUBLISHED.value
    item.published_at = now
    item.last_error = None
    caption.status = CaptionStatus.USED.value
    post = InstagramPost(
        image_id=image.id,
        queue_item_id=item.id,
        ig_media_id=result.media_id,
        ig_container_id=result.container_id,
        media_type=media_type,
        caption=caption.text,
        status="published",
        published_at=now,
        permalink=result.permalink,
    )
    session.add(post)
    session.commit()
    return PublishOutcome(
        queue_item_id=item.id,
        post_id=post.id,
        ig_media_id=result.media_id,
        permalink=result.permalink,
        caption=caption.text,
    )


def _publish_carousel_item(
    session: Session,
    settings: Settings,
    client: InstagramClient,
    *,
    item: PublishingQueueItem,
    caption: Caption,
    attempt: PublishAttempt,
    trigger: str,
) -> PublishOutcome:
    slides = sorted(item.slides, key=lambda row: row.sort_order)
    if len(slides) < 2 or len(slides) > 10:
        raise NotAllowed("Carousel posts require between 2 and 10 images.")

    image_urls: list[str] = []
    primary_image: Image | None = None
    try:
        public_base = settings.publish_public_base_url()
    except ValueError as exc:
        raise NotConfigured(str(exc)) from exc

    for index, slide in enumerate(slides):
        image = session.get(
            Image,
            slide.image_id,
            options=(
                selectinload(Image.crops),
                selectinload(Image.blurs),
            ),
        )
        if image is None:
            raise NotFound("Carousel slide image was not found.")
        approved = next((crop for crop in image.crops if crop.approved), None)
        gate = publishing_gate(
            attested=is_attested(session, image.library_id),
            **publishing_gate_kwargs(session, image),
        )
        blur_pending = blur_drawn_not_approved_reason(image)
        if not gate.allowed or blur_pending:
            reasons = list(gate.reasons)
            if blur_pending:
                reasons.append(blur_pending)
            msg = " ".join(reasons)
            attempt.finished_at = datetime.now(UTC)
            attempt.success = False
            attempt.error = msg
            session.commit()
            raise NotAllowed(msg)
        relative = ensure_publish_derivative(
            session, settings, image, approved, carousel_slide=True
        )
        token = mint_publish_token(relative_path=relative, secret=settings.app_secret)
        url = public_image_url(public_base_url=public_base, token=token)
        if settings.instagram_provider == "fake" and public_base.startswith("http://"):
            url = public_image_url(public_base_url=public_base, token=token)
        image_urls.append(url)
        if index == 0:
            primary_image = image

    if primary_image is None:
        raise NotConfigured("Carousel has no slides.")

    item.state = QueueState.PUBLISHING.value
    item.caption_id = caption.id
    item.last_error = None
    session.commit()

    def _fail_publish(exc: BaseException, message: str) -> None:
        attempt.finished_at = datetime.now(UTC)
        attempt.success = False
        attempt.error = message
        if trigger == PublishTrigger.SCHEDULER.value and is_transient_publish_error(exc):
            item.state = QueueState.APPROVED.value
            item.last_error = message
            from datetime import timedelta

            item.publish_retry_after = datetime.now(UTC) + timedelta(minutes=15)
        else:
            item.state = QueueState.FAILED.value
            item.last_error = message
            if is_permanent_publish_error(exc) or trigger != PublishTrigger.SCHEDULER.value:
                item.schedule_mode = None
                item.scheduled_at = None

    try:
        result = client.publish_carousel(image_urls=image_urls, caption=caption.text.strip())
    except (InstagramNotConfigured, NotConfigured, NotAllowed) as exc:
        err = str(exc.detail if hasattr(exc, "detail") else exc)
        _fail_publish(exc, err)
        session.commit()
        raise
    except Exception as exc:
        err = "Instagram carousel publish failed."
        wrapped = NotConfigured(err)
        _fail_publish(wrapped if not is_transient_publish_error(exc) else exc, err)
        session.commit()
        raise NotConfigured(err) from None

    outcome = _record_success(session, item, primary_image, caption, result, media_type="CAROUSEL")
    attempt.finished_at = datetime.now(UTC)
    attempt.success = True
    item.schedule_mode = None
    item.scheduled_at = None
    item.publish_retry_after = None
    session.commit()
    return outcome


def _has_approved_covering_blur(image: Image) -> bool:
    return any(blur.approved and blur.covers_unsafe for blur in image.blurs)
