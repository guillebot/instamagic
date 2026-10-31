"""Live "Ready to post" pool for Curate.

Curate used to show only the picks of the last manual Gemini curation run, so photos rated
safe/excellent after that run (including flagged photos whose blur was verified) never showed up
until someone ran curation again. The live pool is a single standing curation batch that is
re-synced on every read, with no Gemini call:

* every human safe/excellent photo that passes the central eligibility gate (``collect_eligible``:
  attested library, DO_NOT_POST only with a verified blur unlock, SAFE_AFTER_CROP only with an
  approved crop/covering blur) and is not already in the feed queue gets a pick;
* photos Gemini refused once are never sent to Gemini again, but if they still pass the publishing
  gate they appear too, so the operator can write the caption by hand;
* pending picks whose photo is no longer eligible (rating changed, blur edited -> unlock reset) are
  removed; queued/rejected picks are kept for undo/history;
* everything else safe-rated is explained with the existing "why not picked" reasons.

Nothing here rates, approves, queues or publishes anything.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from instamagic.logging_config import get_logger
from instamagic.models import (
    Caption,
    CurationBatch,
    CurationPick,
    HumanRating,
    Image,
    PublishingQueueItem,
    PublishingQueueSlide,
)
from instamagic.models.enums import CurationBatchStatus, CurationPickStatus, QueueMediaType
from instamagic.services.pipeline import is_attested
from instamagic.services.posting.content_mix import classify_image
from instamagic.services.recommendations.curate import collect_eligible
from instamagic.services.recommendations.curate_skipped import latest_rating_times, record_skipped
from instamagic.services.recommendations.gates import publishing_gate
from instamagic.services.recommendations.refusals import is_gemini_refused
from sqlalchemy import func, or_, select, text
from sqlalchemy.orm import Session, selectinload

log = get_logger(__name__)

LIVE_DETAIL = "live:ready-to-post"
PENDING = {CurationPickStatus.SUGGESTED.value, CurationPickStatus.ACCEPTED.value}


def is_live_batch(batch: CurationBatch | None) -> bool:
    return batch is not None and batch.detail == LIVE_DETAIL


# Arbitrary constant key for the "one live sync at a time" advisory lock.
LIVE_SYNC_LOCK_KEY = 0x1A5_C0A7E


def existing_live_batch(session: Session) -> CurationBatch | None:
    return session.scalar(
        select(CurationBatch).where(CurationBatch.detail == LIVE_DETAIL).order_by(CurationBatch.created_at.asc())
    )


def try_live_sync_lock(session: Session) -> bool:
    """Only one request re-syncs the live pool at a time; others serve the pool as last synced.

    Without this, several tabs/reloads/refetches each rewrote the same batch and pick rows and
    queued behind each other's row locks, so the first Curate load could hang (Oct 25 report).
    """
    if session.get_bind().dialect.name != "postgresql":
        return True
    session.execute(text("SET LOCAL lock_timeout = '3s'"))
    return bool(session.scalar(text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": LIVE_SYNC_LOCK_KEY}))


def get_or_create_live_batch(session: Session) -> CurationBatch:
    batch = session.scalar(
        select(CurationBatch).where(CurationBatch.detail == LIVE_DETAIL).order_by(CurationBatch.created_at.asc())
    )
    if batch is None:
        batch = CurationBatch(
            library_id=None,
            folder=None,
            status=CurationBatchStatus.READY.value,
            limit=0,
            detail=LIVE_DETAIL,
        )
        session.add(batch)
        session.flush()
    return batch


def _stored_tags(image: Image) -> list[str]:
    from instamagic.services.recommendations.enrichment import stored_hashtags

    return stored_hashtags(image)[:12]


def _refused_but_publishable(session: Session, *, exclude: set[str]) -> list[Image]:
    """Gemini-refused safe photos that still pass the publishing gate (manual caption only)."""
    from instamagic.services.blurs.gate_context import publishing_gate_kwargs

    rows = session.scalars(
        select(Image)
        .where(Image.skip_reason.is_(None))
        .where(func.lower(Image.human_rating).in_([HumanRating.SAFE.value, HumanRating.EXCELLENT.value]))
        .options(selectinload(Image.crops), selectinload(Image.blurs), selectinload(Image.queue_items))
    ).all()
    out: list[Image] = []
    attested: dict[Any, bool] = {}
    for image in rows:
        if str(image.id) in exclude or not is_gemini_refused(image):
            continue
        if image.library_id not in attested:
            attested[image.library_id] = is_attested(session, image.library_id)
        gate = publishing_gate(attested=attested[image.library_id], **publishing_gate_kwargs(session, image))
        if gate.allowed:
            out.append(image)
    return out


def _feed_queued_image_ids(session: Session) -> set[str]:
    feed = or_(
        PublishingQueueItem.media_type.is_(None),
        PublishingQueueItem.media_type.not_in([QueueMediaType.STORY.value, QueueMediaType.REELS.value]),
    )
    rows = session.scalars(select(PublishingQueueItem.image_id).where(feed)).all()
    slides = session.scalars(
        select(PublishingQueueSlide.image_id)
        .join(PublishingQueueItem, PublishingQueueItem.id == PublishingQueueSlide.queue_item_id)
        .where(feed)
    ).all()
    return {str(image_id) for image_id in [*rows, *slides] if image_id is not None}


def _latest_caption_ids(session: Session, image_ids: list[Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if not image_ids:
        return out
    rows = session.execute(
        select(Caption.image_id, Caption.id)
        .where(Caption.image_id.in_(image_ids))
        .where(Caption.status != "rejected")
        .order_by(Caption.created_at.asc())
    ).all()
    for image_id, caption_id in rows:
        out[str(image_id)] = caption_id  # last one wins = newest
    return out


def sync_live_batch(session: Session, *, covering_blur_fn) -> CurationBatch:
    batch = get_or_create_live_batch(session)
    eligible = collect_eligible(
        session, library_id=None, folder=None, image_ids=None, covering_blur_fn=covering_blur_fn
    )
    pool: dict[str, tuple[Image, int, list[str]]] = {}
    for item in eligible:
        reasons = ["Rated " + (item.image.human_rating or "safe").lower()]
        if item.covering_blur or (item.image.model_state == "DO_NOT_POST"):
            reasons.append("Blurred (verified): only the blurred render is used")
        mix = classify_image(item.image)
        if mix.sensitive:
            reasons.append("Skin/body: max 1 in 5 feed posts, best as a carousel slide")
        elif mix.boost > 0:
            reasons.append("Growth pick: " + ", ".join(mix.reasons[:2]))
        pool[str(item.image.id)] = (item.image, int(item.local_score), reasons)
    for image in _refused_but_publishable(session, exclude=set(pool)):
        pool[str(image.id)] = (
            image,
            0,
            ["Gemini declined this photo once; it is never sent again. Write the caption yourself."],
        )

    queued_ids = _feed_queued_image_ids(session)
    picks = {str(p.image_id): p for p in session.scalars(select(CurationPick).where(CurationPick.batch_id == batch.id))}
    caption_ids = _latest_caption_ids(session, [image.id for image, _, _ in pool.values()])

    # Drop pending picks that are no longer eligible (rating changed, blur edited, etc.).
    for image_id, pick in list(picks.items()):
        if pick.status in PENDING and image_id not in pool:
            session.delete(pick)
            picks.pop(image_id)
        elif pick.status in PENDING and image_id in queued_ids:
            pick.status = CurationPickStatus.QUEUED.value  # queued from elsewhere (Review, Candidates)

    for image_id, (image, score, reasons) in pool.items():
        pick = picks.get(image_id)
        if pick is None:
            if image_id in queued_ids:
                continue  # already in the feed queue: nothing to curate
            pick = CurationPick(
                batch_id=batch.id,
                image_id=image.id,
                gemini_rank=0,
                local_score=score,
                reasons_json=json.dumps(reasons, ensure_ascii=False),
                suggested_tags_json=json.dumps(_stored_tags(image), ensure_ascii=False),
                status=CurationPickStatus.SUGGESTED.value,
                preferred_caption_id=caption_ids.get(image_id),
            )
            session.add(pick)
            picks[image_id] = pick
            continue
        if pick.status in PENDING:
            pick.local_score = score
            pick.reasons_json = json.dumps(reasons, ensure_ascii=False)
            if pick.preferred_caption_id is None and caption_ids.get(image_id) is not None:
                pick.preferred_caption_id = caption_ids[image_id]
            if (pick.suggested_tags_json or "[]") == "[]":
                tags = _stored_tags(image)
                if tags:
                    pick.suggested_tags_json = json.dumps(tags, ensure_ascii=False)
    session.flush()

    # Newest-rated first.
    rated = latest_rating_times(session, [p.image_id for p in picks.values()])
    ordered = sorted(
        picks.values(),
        key=lambda p: (rated.get(str(p.image_id)) or datetime.min.replace(tzinfo=UTC)).isoformat(),
        reverse=True,
    )
    for index, pick in enumerate(ordered, start=1):
        pick.sort_order = index

    pool_ids = list(pool)
    record_skipped(
        session,
        batch,
        eligible_ids=pool_ids,
        shortlist_ids=pool_ids,
        picked_ids=[*pool_ids, *queued_ids],  # already in the queue is not "skipped"
        covering_blur_fn=covering_blur_fn,
    )
    batch.status = CurationBatchStatus.READY.value
    batch.finished_at = datetime.now(UTC)
    session.commit()
    return batch
