"""Carousel builder: 3-5 publishable photos from the same shoot/folder with a strong cover.

Only photos that already pass every publish gate are suggested (rated SAFE/EXCELLENT, attested
library, approved crop/blur where needed; DO_NOT_POST only with a verified blur, published as the
blurred render). Photos already in the queue or published are skipped. Skin/body shots are kept
as inner slides; the cover is the best colour / portrait / still life of the shoot.
Nothing here calls Gemini.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from instamagic.errors import NotAllowed
from instamagic.models import Image, PublishingQueueSlide
from instamagic.services.pipeline import is_attested
from instamagic.services.posting.collaborators import shoot_key
from instamagic.services.posting.content_mix import classify_image
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

MIN_PHOTOS = 3
MAX_PHOTOS = 5
MAX_SUGGESTIONS = 8
SCAN_LIMIT = 800


@dataclass
class CarouselSuggestion:
    key: str
    label: str
    images: list[Image]
    reasons: list[str] = field(default_factory=list)


def _quality(image: Image) -> float:
    score = image.score
    base = (score.aesthetic_score if score else 0.0) + (0.2 if (image.human_rating or "").lower() == "excellent" else 0)
    return base


def _publishable(session: Session, image: Image) -> bool:
    from instamagic.services.instagram.media_publish import gate_image

    try:
        gate_image(session, image)
    except NotAllowed:
        return False
    return True


def suggest_carousels(session: Session) -> list[CarouselSuggestion]:
    used = set(session.scalars(select(PublishingQueueSlide.image_id)).all())
    rows = session.scalars(
        select(Image)
        .where(Image.skip_reason.is_(None))
        .where(func.lower(Image.human_rating).in_(["safe", "excellent"]))
        .options(selectinload(Image.score), selectinload(Image.queue_items), selectinload(Image.assessments))
        .order_by(Image.ingested_at.desc())
        .limit(SCAN_LIMIT)
    ).all()
    attested: dict = {}
    groups: dict[str, list[Image]] = defaultdict(list)
    for image in rows:
        if image.id in used or image.queue_items:
            continue
        if image.model_state == "DO_NOT_POST" and image.blur_unlock_status != "verified":
            continue
        key = shoot_key(image)
        if key is None:
            continue
        groups[key].append(image)
    out: list[CarouselSuggestion] = []
    for key, images in groups.items():
        if len(images) < MIN_PHOTOS:
            continue
        lib = images[0].library_id
        if lib not in attested:
            attested[lib] = is_attested(session, lib)
        if not attested[lib]:
            continue
        ranked = sorted(images, key=_quality, reverse=True)
        picked = [image for image in ranked if _publishable(session, image)][: MAX_PHOTOS * 2]
        if len(picked) < MIN_PHOTOS:
            continue
        profiles = {image.id: classify_image(image) for image in picked}
        covers = [image for image in picked if not profiles[image.id].sensitive]
        if not covers:
            continue  # never lead with a skin/body shot
        cover = max(covers, key=lambda image: (profiles[image.id].boost, _quality(image)))
        rest = [image for image in picked if image.id != cover.id][: MAX_PHOTOS - 1]
        chosen = [cover, *rest]
        reasons = [f"{len(chosen)} photos from the same shoot", f"Cover: {profiles[cover.id].label}"]
        sensitive = sum(1 for image in rest if profiles[image.id].sensitive)
        if sensitive:
            reasons.append(f"{sensitive} skin/body shot(s) kept as inner slides")
        label = key.split(":", 1)[1] if key.startswith("session:") else key.rsplit(":", 1)[-1]
        out.append(CarouselSuggestion(key=key, label=label or "shoot", images=chosen, reasons=reasons))
    out.sort(key=lambda s: -sum(_quality(image) for image in s.images) / len(s.images))
    return out[:MAX_SUGGESTIONS]
