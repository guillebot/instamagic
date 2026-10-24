"""Prefetch tier for background enrichment: unrated photos the local classifier found clean.

Clearly-safe only. A photo qualifies only when ALL of these hold:
- no human rating at all (rated photos go through the central gate; rated review/reject never qualify),
- local model state REVIEW (never DO_NOT_POST, never SAFE_AFTER_CROP),
- it has at least one safety assessment and EVERY assessment: ran a real detector (available, model
  name set, not uncertain), is REVIEW, has nudity/explicit/lingerie/skin-exposure scores all below
  ``max_score`` (setting, default 0.10, clamped to at most 0.25), and has ZERO detections at or above
  ``max_score`` of any EXPOSED body part (breasts, buttocks, genitalia, anus, male breast, belly,
  armpits, feet) or any covered intimate part (breasts, buttocks, genitalia, anus), and no unsafe region,
- no blur covers of any kind, no approved crop, not skipped, library attested,
- Gemini never refused it, and its shoot folder (library + folder) is a real sub-folder in which Gemini
  never refused any photo and no photo is human-rated review/reject; by default
  (``enrich_prefetch_require_safe_folder``) the folder must also have at least one human safe/excellent photo,
- no captions yet (photos that already have captions are skipped).

Folders with more human-rated safe photos are prefetched first.

The same SQL check is re-run right before every prefetch Gemini call (``assert_prefetch_safe``).

Prefetch only stores labels/captions/hashtags. It never makes a photo eligible for queue, publish or
curation: those still require the human safe/excellent rating (``publishing_gate``).
"""

from __future__ import annotations

from instamagic.config import get_settings
from instamagic.models import (
    AdultAttestation,
    BlurCover,
    Caption,
    CropSuggestion,
    HumanRating,
    Image,
    ImageScore,
    PostingScheduleConfig,
    SafetyAssessment,
    SafetyRegion,
    SafetyState,
)
from instamagic.services.safety.labels import (
    AMBIGUOUS_SCORE,
    EXPOSED_LABELS,
    LINGERIE_LABELS,
    MODEL_LABELS,
)
from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.orm import Session, aliased

MAX_ATTEMPTS = 5


def attested_library_ids(session: Session) -> list:
    rows = session.execute(
        select(AdultAttestation.library_id, AdultAttestation.confirmed).order_by(
            AdultAttestation.library_id, AdultAttestation.attested_at.desc()
        )
    ).all()
    latest: dict = {}
    for library_id, confirmed in rows:
        latest.setdefault(library_id, bool(confirmed))
    return [library_id for library_id, confirmed in latest.items() if confirmed]


# Every EXPOSED class (incl. belly, armpits, feet) and every covered intimate class.
FORBIDDEN_PREFETCH_LABELS = (
    frozenset(label for label in MODEL_LABELS if label.endswith("_EXPOSED"))
    | EXPOSED_LABELS
    | LINGERIE_LABELS
)
SAFE_RATINGS = (HumanRating.SAFE.value, HumanRating.EXCELLENT.value)


class PrefetchIneligible(Exception):
    """Raised by the pre-send re-check when a photo no longer qualifies for prefetch."""


def effective_max_score(value: float) -> float:
    return max(0.0, min(float(value), AMBIGUOUS_SCORE))


def _dirty_assessment(max_score: float):
    """Any assessment of the image that is not clearly safe."""
    bad_region = exists().where(
        SafetyRegion.assessment_id == SafetyAssessment.id,
        or_(
            SafetyRegion.unsafe.is_(True),
            and_(
                or_(
                    SafetyRegion.label.in_(sorted(FORBIDDEN_PREFETCH_LABELS)),
                    SafetyRegion.label.like("%\\_EXPOSED", escape="\\"),
                ),
                SafetyRegion.score >= max_score,
            ),
        ),
    )
    return exists().where(
        SafetyAssessment.image_id == Image.id,
        or_(
            SafetyAssessment.detector_available.is_not(True),
            SafetyAssessment.uncertain.is_not(False),
            func.coalesce(SafetyAssessment.model_name, "none") == "none",
            SafetyAssessment.model_state != SafetyState.REVIEW.value,
            func.coalesce(SafetyAssessment.nudity_score, 1.0) >= max_score,
            func.coalesce(SafetyAssessment.explicit_score, 1.0) >= max_score,
            func.coalesce(SafetyAssessment.lingerie_score, 1.0) >= max_score,
            func.coalesce(SafetyAssessment.skin_exposure_score, 1.0) >= max_score,
            bad_region,
        ),
    )


def _base_query(session: Session, max_score: float, columns):
    max_score = effective_max_score(max_score)
    libraries = attested_library_ids(session)
    has_blur = exists().where(BlurCover.image_id == Image.id)
    # Framing crops (4:5 etc.) are generated for every photo; an approved crop excludes it.
    has_crop = exists().where(
        CropSuggestion.image_id == Image.id, CropSuggestion.approved.is_(True)
    )
    has_assessment = exists().where(SafetyAssessment.image_id == Image.id)
    return (
        select(*columns)
        .select_from(Image)
        .where(
            Image.human_rating.is_(None),
            Image.model_state == SafetyState.REVIEW.value,
            Image.skip_reason.is_(None),
            Image.library_id.in_(libraries) if libraries else Image.id.is_(None),
            ~has_blur,
            ~has_crop,
            has_assessment,
            ~_dirty_assessment(max_score),
            ~_refused(Image),
            *_folder_conditions(),
        )
    )


def _refused(img):
    return or_(
        img.gemini_refused_at.is_not(None),
        func.coalesce(img.gemini_label_status, "").like("blocked:%"),
        func.coalesce(img.enrich_status, "").like("blocked:%"),
    )


def _siblings():
    sibling = aliased(Image)
    return sibling, and_(
        sibling.library_id == Image.library_id, sibling.session_name == Image.session_name
    )


def _folder_conditions() -> list:
    """Shoot-folder rules: no refusal and no human review/reject anywhere in the folder."""
    sibling, same = _siblings()
    conditions = [
        Image.session_name.is_not(None),
        Image.session_name != "",
        ~exists().where(same, _refused(sibling)),
        ~exists().where(
            same, sibling.human_rating.is_not(None), sibling.human_rating.not_in(SAFE_RATINGS)
        ),
    ]
    if get_settings().enrich_prefetch_require_safe_folder:
        conditions.append(exists().where(same, sibling.human_rating.in_(SAFE_RATINGS)))
    return conditions


def _folder_safe_count():
    sibling, same = _siblings()
    return (
        select(func.count())
        .select_from(sibling)
        .where(same, sibling.human_rating.in_(SAFE_RATINGS))
        .correlate(Image)
        .scalar_subquery()
    )


def _has_caption():
    return exists().where(Caption.image_id == Image.id)


def _needs_label():
    return or_(
        Image.gemini_label_status.is_(None),
        and_(
            Image.gemini_label_status == "error",
            func.coalesce(Image.gemini_label_attempts, 0) < MAX_ATTEMPTS,
        ),
    )


def _needs_hashtags():
    return and_(
        Image.gemini_label_status == "ok",
        Image.enrich_hashtags_json.is_(None),
        or_(Image.enrich_status.is_(None), ~Image.enrich_status.like("blocked:%")),
        or_(
            func.coalesce(Image.enrich_status, "") != "error",
            func.coalesce(Image.enrich_attempts, 0) < MAX_ATTEMPTS,
        ),
    )


def _pending_condition():
    # Label (+captions) only when the photo has no captions yet; hashtags once labeled.
    return or_(and_(_needs_label(), ~_has_caption()), _needs_hashtags())


def pending_prefetch_ids(session: Session, *, max_score: float, limit: int) -> list:
    """Most promising first: aesthetic score, then technical quality and composition."""
    query = (
        _base_query(session, max_score, (Image.id,))
        .outerjoin(ImageScore, ImageScore.image_id == Image.id)
        .where(_pending_condition())
        .order_by(
            _folder_safe_count().desc(),
            func.coalesce(ImageScore.aesthetic_score, 0).desc(),
            func.coalesce(ImageScore.technical_quality, 0).desc(),
            func.coalesce(ImageScore.composition, 0).desc(),
            Image.id,
        )
        .limit(max(0, limit))
    )
    return list(session.scalars(query).all())


def is_prefetch_candidate(session: Session, image: Image, *, max_score: float) -> bool:
    """Per-image re-check right before any Gemini call (rating/state may have changed)."""
    query = _base_query(session, max_score, (Image.id,)).where(Image.id == image.id)
    return session.scalar(query) is not None


def assert_prefetch_safe(session: Session, image: Image, *, max_score: float) -> None:
    """Final pre-send re-check from the DB, run immediately before each prefetch Gemini call."""
    enabled = session.scalar(select(PostingScheduleConfig.enrichment_prefetch_enabled).limit(1))
    if not enabled:  # turned off (by the operator or the refusal circuit breaker) mid-batch
        raise PrefetchIneligible(f"{image.id}: prefetch disabled")
    if not is_prefetch_candidate(session, image, max_score=max_score):
        raise PrefetchIneligible(str(image.id))


def prefetch_counts(session: Session, *, max_score: float) -> dict[str, int]:
    ready = and_(Image.gemini_label_status == "ok", Image.enrich_hashtags_json.is_not(None))
    row = session.execute(
        _base_query(
            session,
            max_score,
            (
                func.count(),
                func.count().filter(ready),
                func.count().filter(_pending_condition()),
            ),
        )
    ).one()
    return {"eligible": int(row[0]), "ready": int(row[1]), "pending": int(row[2])}
