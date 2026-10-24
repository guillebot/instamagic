from __future__ import annotations

from uuid import UUID

from instamagic.models import HumanRating, Image, PublishingQueueItem, QueueState, SafetyState
from instamagic.services.pipeline import is_attested, rate_image
from instamagic.services.recommendations.gates import publishing_gate
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload


def try_enqueue_image(session: Session, image: Image, *, covering_blur_fn) -> tuple[bool, str | None]:
    approved = next((crop for crop in image.crops if crop.approved), None)
    covering_blur = covering_blur_fn(session, image)
    gate = publishing_gate(
        attested=is_attested(session, image.library_id),
        human_rating=image.human_rating,
        model_state=image.model_state,
        needs_crop=image.model_state == SafetyState.SAFE_AFTER_CROP.value,
        has_approved_crop=approved is not None,
        has_approved_covering_blur=covering_blur,
    )
    if not gate.allowed:
        return False, " ".join(gate.reasons)
    if image.model_state == SafetyState.DO_NOT_POST.value:
        return False, "The local model marked this image DO_NOT_POST."
    existing = session.scalar(
        select(PublishingQueueItem)
        .where(PublishingQueueItem.image_id == image.id)
        .order_by(PublishingQueueItem.created_at.desc())
    )
    if existing is not None:
        return True, None
    item = PublishingQueueItem(
        image_id=image.id,
        crop_id=approved.id if approved else None,
        state=QueueState.CANDIDATE.value,
    )
    session.add(item)
    return True, None


def bulk_enqueue(
    session: Session,
    image_ids: list[UUID],
    *,
    covering_blur_fn,
) -> list[tuple[UUID, bool, str | None]]:
    unique = list(dict.fromkeys(image_ids))
    images = session.scalars(
        select(Image)
        .where(Image.id.in_(unique), Image.skip_reason.is_(None))
        .options(
            selectinload(Image.crops),
            selectinload(Image.blurs),
            selectinload(Image.queue_items),
        )
    ).all()
    by_id = {image.id: image for image in images}
    results: list[tuple[UUID, bool, str | None]] = []
    for image_id in unique:
        image = by_id.get(image_id)
        if image is None:
            results.append((image_id, False, "Image was not found."))
            continue
        ok, error = try_enqueue_image(session, image, covering_blur_fn=covering_blur_fn)
        results.append((image_id, ok, error))
    session.commit()
    return results


def bulk_reject(session: Session, image_ids: list[UUID]) -> list[tuple[UUID, bool, str | None]]:
    unique = list(dict.fromkeys(image_ids))
    images = session.scalars(select(Image).where(Image.id.in_(unique), Image.skip_reason.is_(None))).all()
    by_id = {image.id: image for image in images}
    results: list[tuple[UUID, bool, str | None]] = []
    for image_id in unique:
        image = by_id.get(image_id)
        if image is None:
            results.append((image_id, False, "Image was not found."))
            continue
        rate_image(session, image, HumanRating.REJECT.value)
        results.append((image_id, True, None))
    return results
