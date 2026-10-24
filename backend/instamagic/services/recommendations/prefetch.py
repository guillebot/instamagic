"""Prefetch tier for background enrichment: unrated photos the local classifier found clean.

A photo qualifies only when ALL of these hold:
- no human rating at all (rated photos go through the central gate; rated review/reject never qualify),
- local model state REVIEW (never DO_NOT_POST, never SAFE_AFTER_CROP),
- the stored safety assessment had a working detector, was not uncertain, and has no exposed-label
  region at or above the review threshold (AMBIGUOUS_SCORE), no unsafe region, nudity/explicit scores
  below the review threshold and covered/lingerie score below ``enrich_prefetch_max_lingerie``,
- no blur covers of any kind, not skipped, library attested,
- no captions yet (photos that already have captions are skipped).

Prefetch only stores labels/captions/hashtags. It never makes a photo eligible for queue, publish or
curation: those still require the human safe/excellent rating (``publishing_gate``).
"""

from __future__ import annotations

from instamagic.models import (
    AdultAttestation,
    BlurCover,
    Caption,
    Image,
    ImageScore,
    SafetyAssessment,
    SafetyRegion,
    SafetyState,
)
from instamagic.services.safety.labels import AMBIGUOUS_SCORE, EXPOSED_LABELS, LINGERIE_LABELS
from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.orm import Session

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


def _clean_conditions(max_lingerie: float):
    hit = exists().where(
        SafetyRegion.assessment_id == SafetyAssessment.id,
        or_(
            SafetyRegion.unsafe.is_(True),
            and_(SafetyRegion.label.in_(sorted(EXPOSED_LABELS)), SafetyRegion.score >= AMBIGUOUS_SCORE),
            and_(SafetyRegion.label.in_(sorted(LINGERIE_LABELS)), SafetyRegion.score >= max_lingerie),
        ),
    )
    return (
        SafetyAssessment.detector_available.is_(True),
        SafetyAssessment.uncertain.is_(False),
        SafetyAssessment.model_state == SafetyState.REVIEW.value,
        SafetyAssessment.nudity_score < AMBIGUOUS_SCORE,
        SafetyAssessment.explicit_score < AMBIGUOUS_SCORE,
        SafetyAssessment.lingerie_score < max_lingerie,
        ~hit,
    )


def _base_query(session: Session, max_lingerie: float, columns):
    libraries = attested_library_ids(session)
    has_blur = exists().where(BlurCover.image_id == Image.id)
    return (
        select(*columns)
        .select_from(Image)
        .join(SafetyAssessment, SafetyAssessment.image_id == Image.id)
        .where(
            Image.human_rating.is_(None),
            Image.model_state == SafetyState.REVIEW.value,
            Image.skip_reason.is_(None),
            Image.library_id.in_(libraries) if libraries else Image.id.is_(None),
            ~has_blur,
            *_clean_conditions(max_lingerie),
        )
    )


def _has_caption():
    return exists().where(Caption.image_id == Image.id)


def _needs_label():
    return or_(
        Image.gemini_label_status.is_(None),
        and_(Image.gemini_label_status == "error", func.coalesce(Image.gemini_label_attempts, 0) < MAX_ATTEMPTS),
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


def pending_prefetch_ids(session: Session, *, max_lingerie: float, limit: int) -> list:
    """Most promising first: aesthetic score, then technical quality and composition."""
    query = (
        _base_query(session, max_lingerie, (Image.id,))
        .outerjoin(ImageScore, ImageScore.image_id == Image.id)
        .where(_pending_condition())
        .order_by(
            func.coalesce(ImageScore.aesthetic_score, 0).desc(),
            func.coalesce(ImageScore.technical_quality, 0).desc(),
            func.coalesce(ImageScore.composition, 0).desc(),
            Image.id,
        )
        .limit(max(0, limit))
    )
    return list(session.scalars(query).all())


def is_prefetch_candidate(session: Session, image: Image, *, max_lingerie: float) -> bool:
    """Per-image re-check right before any Gemini call (rating/state may have changed)."""
    query = _base_query(session, max_lingerie, (Image.id,)).where(Image.id == image.id)
    return session.scalar(query) is not None


def prefetch_counts(session: Session, *, max_lingerie: float) -> dict[str, int]:
    ready = and_(Image.gemini_label_status == "ok", Image.enrich_hashtags_json.is_not(None))
    row = session.execute(
        _base_query(
            session,
            max_lingerie,
            (
                func.count(),
                func.count().filter(ready),
                func.count().filter(_pending_condition()),
            ),
        )
    ).one()
    return {"eligible": int(row[0]), "ready": int(row[1]), "pending": int(row[2])}
