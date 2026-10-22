"""Human-gated publish orchestration: re-check gates, mint public URL, call Graph API."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from instamagic.config import Settings
from instamagic.errors import InstagramNotConfigured, NotAllowed, NotConfigured, NotFound
from instamagic.models import (
    Caption,
    CaptionStatus,
    CropSuggestion,
    Image,
    InstagramPost,
    Library,
    PublishingQueueItem,
    QueueState,
    SafetyState,
)
from instamagic.services.images.derivatives import crop_image, oriented_rgb, read_image
from instamagic.services.instagram.feed_image import prepare_publish_file_from_path, write_instagram_publish_jpeg
from instamagic.services.images.paths import safe_join
from instamagic.services.instagram.assets import mint_publish_token, public_image_url
from instamagic.services.instagram.client import InstagramClient, PublishResult
from instamagic.services.pipeline import is_attested, render_blur_derivative
from instamagic.services.recommendations.gates import publishing_gate
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
    caption_id: UUID,
) -> PublishOutcome:
    if not client.configured():
        raise InstagramNotConfigured(
            "Instagram Graph API is not configured. Connect on /instagram or use "
            "INSTAGRAM_PROVIDER=fake for local tests."
        )
    item = session.get(PublishingQueueItem, item_id)
    if item is None:
        raise NotFound("Queue item was not found.")
    if item.state not in {QueueState.APPROVED.value, QueueState.FAILED.value}:
        raise NotAllowed("Only an approved (or previously failed) queue item can be published.")
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
    caption = session.get(Caption, caption_id)
    if caption is None or caption.image_id != image.id:
        raise NotFound("Caption was not found for this image.")
    if not caption.text.strip():
        raise NotAllowed("Caption text is empty.")

    approved = next((crop for crop in image.crops if crop.approved), None)
    covering_blur = _has_approved_covering_blur(image)
    gate = publishing_gate(
        attested=is_attested(session, image.library_id),
        human_rating=image.human_rating,
        model_state=image.model_state,
        needs_crop=image.model_state == SafetyState.SAFE_AFTER_CROP.value,
        has_approved_crop=approved is not None,
        has_approved_covering_blur=covering_blur,
    )
    if not gate.allowed:
        raise NotAllowed(" ".join(gate.reasons))

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

    try:
        result = client.publish_image(image_url=image_url, caption=caption.text.strip())
    except (InstagramNotConfigured, NotConfigured, NotAllowed) as exc:
        item.state = QueueState.FAILED.value
        item.last_error = str(exc.detail if hasattr(exc, "detail") else exc)
        session.commit()
        raise
    except Exception:
        item.state = QueueState.FAILED.value
        item.last_error = "Instagram publish failed."
        session.commit()
        raise NotConfigured("Instagram publish failed.") from None

    return _record_success(session, item, image, caption, result)


def _record_success(
    session: Session,
    item: PublishingQueueItem,
    image: Image,
    caption: Caption,
    result: PublishResult,
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
        media_type="IMAGE",
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


def ensure_publish_derivative(
    session: Session,
    settings: Settings,
    image: Image,
    approved: CropSuggestion | None,
) -> str:
    """Return a derivatives-relative JPEG path suitable for Meta to fetch."""
    if approved is not None:
        if not approved.derivative_path:
            _refresh_crop_derivative(session, settings, image, approved)
            session.commit()
        if approved.derivative_path:
            return _instagram_publish_copy(settings, image.id, approved.derivative_path, "from-crop")
    approved_blur = next((blur for blur in image.blurs if blur.approved), None)
    if approved_blur is not None:
        if not approved_blur.derivative_path or not any(
            blur.derivative_path for blur in image.blurs if blur.approved
        ):
            _render_blur(session, settings, image)
            session.commit()
        blur_path = next(
            (blur.derivative_path for blur in image.blurs if blur.approved and blur.derivative_path),
            None,
        )
        if blur_path:
            return _instagram_publish_copy(settings, image.id, blur_path, "from-blur")
    if image.preview_path:
        source = settings.derivatives_root / image.preview_path
        if source.is_file():
            relative = f"{image.id}/publish/feed.jpg"
            dest = settings.derivatives_root / relative
            if not dest.is_file() or dest.stat().st_mtime < source.stat().st_mtime:
                prepare_publish_file_from_path(source, dest)
            return relative
    raise NotConfigured("No publishable derivative is available for this image.")


def _instagram_publish_copy(
    settings: Settings,
    image_id: UUID,
    source_relative: str,
    label: str,
) -> str:
    source = settings.derivatives_root / source_relative
    relative = f"{image_id}/publish/{label}.jpg"
    dest = settings.derivatives_root / relative
    if not dest.is_file() or dest.stat().st_mtime < source.stat().st_mtime:
        prepare_publish_file_from_path(source, dest)
    return relative


def _has_approved_covering_blur(image: Image) -> bool:
    return any(blur.approved and blur.covers_unsafe for blur in image.blurs)


def _render_blur(session: Session, settings: Settings, image: Image) -> None:
    library = session.get(Library, image.library_id)
    if library is None:
        raise NotFound("Library was not found.")
    source = safe_join(Path(library.root_path), image.relative_path)
    if not source.is_file():
        raise NotFound("Original image was not found.")
    rgb = oriented_rgb(read_image(source))
    relative = render_blur_derivative(
        rgb=rgb,
        covers=list(image.blurs),
        image_id=image.id,
        derivatives_root=settings.derivatives_root,
    )
    for blur in image.blurs:
        blur.derivative_path = relative


def _refresh_crop_derivative(session: Session, settings: Settings, image: Image, crop: CropSuggestion) -> None:
    library = session.get(Library, image.library_id)
    if library is None:
        raise NotFound("Library was not found.")
    source = safe_join(Path(library.root_path), image.relative_path)
    if source.is_file():
        rgb = oriented_rgb(read_image(source))
    elif image.preview_path and (settings.derivatives_root / image.preview_path).is_file():
        rgb = oriented_rgb(read_image(settings.derivatives_root / image.preview_path))
    else:
        raise NotFound("Original image was not found.")
    x, y, width, height = crop.x, crop.y, crop.width, crop.height
    if image.width and image.height and rgb.size != (image.width, image.height):
        scale_x = rgb.size[0] / image.width
        scale_y = rgb.size[1] / image.height
        x = max(0, int(round(x * scale_x)))
        y = max(0, int(round(y * scale_y)))
        width = max(1, int(round(width * scale_x)))
        height = max(1, int(round(height * scale_y)))
        width = min(width, rgb.size[0] - x)
        height = min(height, rgb.size[1] - y)
    relative = f"{image.id}/crops/{crop.aspect_ratio.replace(':', 'x')}.jpg"
    rendered = crop_image(rgb, x, y, width, height)
    write_instagram_publish_jpeg(rendered, settings.derivatives_root / relative)
    crop.derivative_path = relative
