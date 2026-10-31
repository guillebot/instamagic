"""Growth content mix: what to post first, and the 1-in-5 cap on skin/body shots.

Heuristic (local only; nothing new is sent to Gemini). Signals, all already stored:

* ``SafetyAssessment`` from the local detector (latest row): ``skin_exposure_score``,
  ``nudity_score``, ``lingerie_score``, ``face_count``;
* ``Image.model_state`` (DO_NOT_POST published only as a verified blur counts as sensitive);
* text Gemini already produced from the blurred render: ``gemini_description``, ``gemini_mood``,
  content tags and enrichment hashtags.

Classes:

* **sensitive** ("skin/body"): skin >= 0.35, nudity >= 0.25 or lingerie >= 0.30, a DO_NOT_POST photo
  published as a verified blur, or words like skin/body/torso/back/tattoo/projected light in its
  description or tags. At most 1 in every 5 feed posts (any 5 consecutive feed posts, published +
  scheduled), and preferably used as a carousel slide, not the cover. A carousel counts as
  sensitive only when its cover is.
* **still life**: still life / food / objects / flowers words, no face.
* **portrait**: detector found a face (``face_count >= 1``) or portrait/face words.
* **color** vs **mono**: black-and-white words/tags mean mono; otherwise colour is assumed.

Boost (added to the local rank, -1..+1): still life +1.0, portrait with a face +0.8, colour +0.4
(capped at 1.0); sensitive -1.0 regardless of the rest.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from instamagic.models import (
    Image,
    PublishingQueueItem,
    QueueMediaType,
    QueueState,
    SafetyAssessment,
)
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

SENSITIVE_ONE_IN = 5  # at most 1 skin/body post in any 5 consecutive feed posts
SKIN_THRESHOLD = 0.35
NUDITY_THRESHOLD = 0.25
LINGERIE_THRESHOLD = 0.30

_SENSITIVE_WORDS = re.compile(
    r"\b(skin|body|bodies|torso|bare (?:back|shoulders?|skin|legs?)|(?:her|his) (?:bare )?back|chest|waist|hips?|"
    r"thighs?|belly|abdomen|"
    r"nude|naked|implied|figure study|tattoo\w*|projected|projection|projector|light projection|lingerie|"
    r"curves?|silhouette of (?:a|her|his) body)\b",
    re.I,
)
_STILL_LIFE_WORDS = re.compile(
    r"(still ?life|stilllife|food|fruit|vegetable|bread|plate|kitchen|table ?top|vase|flowers?|bouquet|bottle|"
    r"cup|coffee|objects?|botanical|flat ?lay)",
    re.I,
)
_PORTRAIT_WORDS = re.compile(r"\b(portrait|face|facial|gaze|eyes|smil\w+|expression|headshot)\b", re.I)
_MONO_WORDS = re.compile(r"(black[ -]?and[ -]?white|monochrom\w*|\bb&w\b|\bbnw\b|grayscale|greyscale)", re.I)


@dataclass(frozen=True)
class ContentProfile:
    sensitive: bool = False
    still_life: bool = False
    portrait: bool = False
    mono: bool = False
    boost: float = 0.0
    reasons: list[str] = field(default_factory=list)

    @property
    def label(self) -> str:
        if self.sensitive:
            return "skin/body"
        if self.still_life:
            return "still life"
        if self.portrait:
            return "portrait"
        return "mono" if self.mono else "color"


def _latest_assessment(image: Image) -> SafetyAssessment | None:
    rows = list(getattr(image, "assessments", None) or [])
    if not rows:
        return None
    return max(rows, key=lambda a: (a.assessed_at is not None, a.assessed_at or datetime.min))


def _text(image: Image) -> str:
    parts = [image.gemini_description or "", image.gemini_mood or ""]
    try:
        parts += [link.tag.name for link in (image.tags or []) if link.tag is not None]
    except Exception:  # detached / not loaded
        pass
    parts.append(image.enrich_hashtags_json or "")
    return " ".join(parts)


def image_is_mono(image: Image) -> bool:
    return bool(_MONO_WORDS.search(_text(image)))


def classify_image(image: Image | None) -> ContentProfile:
    if image is None:
        return ContentProfile()
    text = _text(image)
    a = _latest_assessment(image)
    reasons: list[str] = []
    sensitive = False
    if a is not None:
        if (a.skin_exposure_score or 0) >= SKIN_THRESHOLD:
            sensitive = True
            reasons.append("high skin exposure (local detector)")
        elif (a.nudity_score or 0) >= NUDITY_THRESHOLD or (a.lingerie_score or 0) >= LINGERIE_THRESHOLD:
            sensitive = True
            reasons.append("body-focused (local detector)")
    if image.model_state == "DO_NOT_POST":
        sensitive = True
        reasons.append("published only as a verified blur")
    words = _SENSITIVE_WORDS.findall(text)
    if words:
        sensitive = True
        reasons.append(f"described as {words[0].lower()}")
    mono = bool(_MONO_WORDS.search(text))
    face = bool(a is not None and (a.face_count or 0) >= 1)
    portrait = face or bool(_PORTRAIT_WORDS.search(text))
    still_life = bool(_STILL_LIFE_WORDS.search(text)) and not face
    if sensitive:
        return ContentProfile(True, still_life, portrait, mono, -1.0, reasons or ["skin/body"])
    boost = 0.0
    if still_life:
        boost += 1.0
        reasons.append("still life")
    if portrait:
        boost += 0.8 if face else 0.5
        reasons.append("portrait with a visible face" if face else "portrait")
    if not mono:
        boost += 0.4
        reasons.append("colour")
    return ContentProfile(False, still_life, portrait, mono, min(1.0, boost), reasons)


def item_is_sensitive(session: Session, item: PublishingQueueItem) -> bool:
    """A feed post counts against the cap when its cover (first photo) is skin/body."""
    if item.media_type == QueueMediaType.STORY.value:
        return False
    image = session.get(Image, item.image_id) if item.image_id else None
    return classify_image(image).sensitive


def violates_one_in_n(before: list[bool], after: list[bool], *, n: int = SENSITIVE_ONE_IN) -> bool:
    """Would a sensitive post between ``before`` (oldest..newest) and ``after`` (next..later) break
    "at most 1 sensitive in any n consecutive feed posts"? Only the n-1 neighbours on each side matter.
    """
    window = n - 1
    return any(before[-window:]) or any(after[:window])


def _feed_neighbours(
    session: Session, slot: datetime, *, exclude_id=None, span_days: int = 21
) -> tuple[list[PublishingQueueItem], list[PublishingQueueItem]]:
    start, end = slot - timedelta(days=span_days), slot + timedelta(days=span_days)
    rows = session.scalars(
        select(PublishingQueueItem).where(
            PublishingQueueItem.media_type != QueueMediaType.STORY.value,
            or_(
                (PublishingQueueItem.state == QueueState.PUBLISHED.value)
                & PublishingQueueItem.published_at.is_not(None)
                & (PublishingQueueItem.published_at >= start),
                PublishingQueueItem.state.in_([QueueState.APPROVED.value, QueueState.PUBLISHING.value])
                & PublishingQueueItem.scheduled_at.is_not(None)
                & (PublishingQueueItem.scheduled_at <= end),
            ),
        )
    ).all()

    def _when(row: PublishingQueueItem) -> datetime | None:
        value = row.published_at if row.state == QueueState.PUBLISHED.value else row.scheduled_at
        if value is None:
            return None
        return value if value.tzinfo else value.replace(tzinfo=slot.tzinfo)

    timed = [(w, r) for r in rows if r.id != exclude_id and (w := _when(r)) is not None]
    timed.sort(key=lambda pair: pair[0])
    before = [r for w, r in timed if w <= slot]
    after = [r for w, r in timed if w > slot]
    return before, after


def sensitive_slot_allowed(session: Session, item: PublishingQueueItem, slot: datetime) -> bool:
    """False when scheduling this skin/body post at ``slot`` would break the 1-in-5 cap."""
    if not item_is_sensitive(session, item):
        return True
    before, after = _feed_neighbours(session, slot, exclude_id=item.id)
    window = SENSITIVE_ONE_IN - 1
    flags_before = [item_is_sensitive(session, r) for r in before[-window:]]
    flags_after = [item_is_sensitive(session, r) for r in after[:window]]
    return not violates_one_in_n(flags_before, flags_after)


def growth_rank_key(session: Session, item: PublishingQueueItem) -> float:
    image = session.get(Image, item.image_id) if item.image_id else None
    return classify_image(image).boost


def queue_select_sensitive_ids(session: Session, items: list[PublishingQueueItem]) -> set:
    return {item.id for item in items if item_is_sensitive(session, item)}
