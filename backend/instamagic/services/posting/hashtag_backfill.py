"""One-off/idempotent backfill: clean stored hashtags and append hashtags to captions that lack them.

Targets captions selected on APPROVED queue items and unused captions of safe/excellent photos.
Captions that already carry 5+ hashtags (operator-edited) are left as is, except blocklisted tags
are stripped. Empty captions only get the hashtag block appended. Never touches published/used or
rejected captions and never publishes or schedules anything.
"""

from __future__ import annotations

import json

from instamagic.models import Caption, CaptionStatus, Image, PublishingQueueItem, QueueState
from instamagic.models.enums import HumanRating
from instamagic.services.posting.content_mix import image_is_mono
from instamagic.services.posting.hashtags import clean_tags, compose_caption, parse_stored
from instamagic.services.posting.schedule_config import hashtag_pools
from sqlalchemy import false, select
from sqlalchemy.orm import Session


def backfill_hashtags(session: Session, *, dry_run: bool = False) -> dict[str, int]:
    stats = {"images_cleaned": 0, "captions_updated": 0, "captions_checked": 0}
    for image in session.scalars(select(Image).where(Image.enrich_hashtags_json.is_not(None))):
        raw = parse_stored(image.enrich_hashtags_json)
        cleaned = clean_tags(raw)
        if cleaned != raw:
            stats["images_cleaned"] += 1
            if not dry_run:
                image.enrich_hashtags_json = json.dumps(cleaned[:15], ensure_ascii=False)
    queued_caption_ids = set(
        session.scalars(
            select(PublishingQueueItem.caption_id).where(
                PublishingQueueItem.state == QueueState.APPROVED.value,
                PublishingQueueItem.caption_id.is_not(None),
            )
        )
    )
    rated = select(Image.id).where(Image.human_rating.in_([HumanRating.SAFE.value, HumanRating.EXCELLENT.value]))
    captions = session.scalars(
        select(Caption).where(
            (Caption.id.in_(queued_caption_ids) if queued_caption_ids else false())
            | (
                Caption.image_id.in_(rated)
                & Caption.status.not_in([CaptionStatus.USED.value, CaptionStatus.REJECTED.value])
            )
        )
    ).all()
    for caption in captions:
        stats["captions_checked"] += 1
        image = session.get(Image, caption.image_id)
        if image is None:
            continue
        new_text = compose_caption(
            caption.text or "",
            parse_stored(image.enrich_hashtags_json),
            seed=str(image.id),
            pools=hashtag_pools(session),
            mono=image_is_mono(image),
        )
        if new_text != (caption.text or "").strip() and new_text != caption.text:
            stats["captions_updated"] += 1
            if not dry_run:
                caption.text = new_text
    if dry_run:
        session.rollback()
    else:
        session.commit()
    return stats


def main() -> None:  # pragma: no cover - operator entry point
    import sys

    from instamagic.db import session_factory

    dry = "--dry-run" in sys.argv
    session = session_factory()()
    try:
        print(json.dumps(backfill_hashtags(session, dry_run=dry)))
    finally:
        session.close()


if __name__ == "__main__":  # pragma: no cover
    main()
