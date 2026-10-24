"""Create and reorder carousel queue items (2–10 gated images)."""

from __future__ import annotations

from uuid import UUID

from instamagic.errors import NotAllowed, NotFound
from instamagic.models import Image, PublishingQueueItem, PublishingQueueSlide, QueueState
from instamagic.models.enums import QueueMediaType
from instamagic.services.blurs.gate_context import publishing_gate_kwargs
from instamagic.services.instagram.publish_derivative import blur_drawn_not_approved_reason
from instamagic.services.pipeline import is_attested
from instamagic.services.recommendations.gates import publishing_gate
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload


def _gate_image(session: Session, image: Image) -> None:
    gate = publishing_gate(
        attested=is_attested(session, image.library_id), **publishing_gate_kwargs(session, image)
    )
    blur_pending = blur_drawn_not_approved_reason(image)
    if not gate.allowed or blur_pending:
        reasons = list(gate.reasons)
        if blur_pending:
            reasons.append(blur_pending)
        raise NotAllowed(" ".join(reasons))


def create_carousel_queue_item(session: Session, image_ids: list[UUID]) -> PublishingQueueItem:
    unique = list(dict.fromkeys(image_ids))
    if len(unique) < 2 or len(unique) > 10:
        raise NotAllowed("Carousel posts require between 2 and 10 distinct images.")
    images: list[Image] = []
    for image_id in unique:
        image = session.get(
            Image,
            image_id,
            options=(selectinload(Image.crops), selectinload(Image.blurs)),
        )
        if image is None:
            raise NotFound("Image was not found.")
        _gate_image(session, image)
        images.append(image)

    item = PublishingQueueItem(
        image_id=images[0].id,
        media_type=QueueMediaType.CAROUSEL.value,
        state=QueueState.CANDIDATE.value,
    )
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


def reorder_carousel_slides(
    session: Session, item_id: UUID, image_ids: list[UUID]
) -> PublishingQueueItem:
    item = session.get(
        PublishingQueueItem,
        item_id,
        options=(selectinload(PublishingQueueItem.slides),),
    )
    if item is None:
        raise NotFound("Queue item was not found.")
    if item.media_type != QueueMediaType.CAROUSEL.value:
        raise NotAllowed("Only carousel queue items can be reordered.")
    if len(image_ids) != len(item.slides):
        raise NotAllowed("Reorder must include every slide exactly once.")
    by_image = {slide.image_id: slide for slide in item.slides}
    for image_id in image_ids:
        if image_id not in by_image:
            raise NotAllowed("Unknown slide image id.")
    for order, image_id in enumerate(image_ids):
        by_image[image_id].sort_order = order
    item.image_id = image_ids[0]
    session.commit()
    session.refresh(item)
    return item


def slides_for_item(session: Session, item: PublishingQueueItem) -> list[PublishingQueueSlide]:
    return list(
        session.scalars(
            select(PublishingQueueSlide)
            .where(PublishingQueueSlide.queue_item_id == item.id)
            .order_by(PublishingQueueSlide.sort_order.asc())
        ).all()
    )
