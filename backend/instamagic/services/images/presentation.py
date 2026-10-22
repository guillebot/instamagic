from __future__ import annotations

from typing import Any

from instamagic.models.enums import HumanRating, SafetyState
from sqlalchemy import and_, exists, not_, or_

# Human ratings are stored lowercase; accept badge-style / case variants from filters.
_HUMAN_RATING_ALIASES = {
    "reject": HumanRating.REJECT.value,
    "review": HumanRating.REVIEW.value,
    "safe": HumanRating.SAFE.value,
    "excellent": HumanRating.EXCELLENT.value,
    # Badge / SafetyState labels users often pick when they mean a rating.
    "do_not_post": HumanRating.REJECT.value,
    "rejected": HumanRating.REJECT.value,
    "unsafe": HumanRating.REJECT.value,
}

# Presentation-only badges (not stored on Image.model_state).
PUBLISHABLE = "PUBLISHABLE"
REJECTED = "REJECTED"

# Blur covers remediates the same classifier state as crop (SAFE_AFTER_CROP).
# Review exposes a dedicated "Safe after blur" filter that aliases that state.
_SAFE_AFTER_REMEDIATE = frozenset(
    {
        SafetyState.SAFE_AFTER_CROP.value,
        "SAFE_AFTER_BLUR",
    }
)

# Review filter: human-rated safe/excellent with at least one approved blur cover.
BLURRED = "BLURRED"

_PUBLISH_VARIANT_LABELS = {
    "ORIGINAL": "SAFE",
    "BLURRED": "SAFE · BLURRED",
    "CROPPED": "SAFE · CROPPED",
    "BLURRED_CROPPED": "SAFE · BLURRED + CROPPED",
}


def publish_variant(
    human_rating: str | None,
    *,
    has_approved_crop: bool = False,
    has_approved_blur: bool = False,
) -> str | None:
    if human_rating not in {HumanRating.SAFE.value, HumanRating.EXCELLENT.value}:
        return None
    if has_approved_blur and has_approved_crop:
        return "BLURRED_CROPPED"
    if has_approved_blur:
        return "BLURRED"
    if has_approved_crop:
        return "CROPPED"
    return "ORIGINAL"


def publish_variant_label(variant: str | None) -> str | None:
    if variant is None:
        return None
    return _PUBLISH_VARIANT_LABELS.get(variant)


def effective_state(
    human_rating: str | None,
    model_state: str | None,
    *,
    has_approved_crop: bool = False,
    has_approved_covering_blur: bool = False,
) -> str:
    """Lifecycle badge for the operator UI.

    Stored fields stay ``model_state`` (classifier) + ``human_rating``. This value is
    derived:

    - **Safe** — originally clear (classifier ``SAFE``), or human cleared a non-remediation
      state (e.g. ``REVIEW``) without needing crop/blur.
    - **Publishable** — was ``SAFE_AFTER_CROP``, human rated safe/excellent, and an
      approved crop or covering blur is in place.
    - **Safe after crop / blur** — still needs alter (or rated but not yet altered).
    - **Rejected** — human reject.
    - **Unsafe** — classifier ``DO_NOT_POST`` (unrated).
    - **Review** — human review, or unrated classifier ``REVIEW``.
    """
    if human_rating == HumanRating.REJECT.value:
        return REJECTED
    if human_rating == HumanRating.REVIEW.value:
        return SafetyState.REVIEW.value

    remediable = model_state == SafetyState.SAFE_AFTER_CROP.value
    remediated = has_approved_crop or has_approved_covering_blur

    if human_rating in {HumanRating.SAFE.value, HumanRating.EXCELLENT.value}:
        if remediable:
            return PUBLISHABLE if remediated else SafetyState.SAFE_AFTER_CROP.value
        return SafetyState.SAFE.value

    if model_state == SafetyState.DO_NOT_POST.value:
        return SafetyState.DO_NOT_POST.value
    return model_state or "UNREVIEWED"


def normalize_human_rating_filter(raw: str | None) -> str | None:
    """Map filter query values to stored human_rating enum values, or None if empty."""
    if raw is None:
        return None
    text = raw.strip()
    if not text:
        return None
    key = text.lower().replace("-", "_").replace(" ", "_")
    return _HUMAN_RATING_ALIASES.get(key)


def _has_approved_crop_exists(image_model: Any) -> Any:
    from instamagic.models.entities import CropSuggestion

    return exists().where(
        CropSuggestion.image_id == image_model.id,
        CropSuggestion.approved.is_(True),
    )


def _has_approved_blur_exists(image_model: Any) -> Any:
    from instamagic.models.entities import BlurCover

    return exists().where(
        BlurCover.image_id == image_model.id,
        BlurCover.approved.is_(True),
    )


def effective_state_filter_clause(image_model: Any, state: str) -> Any:
    """SQL clause matching the Review badge (human rating overrides model_state).

    Selecting SAFE means originally safe / human-cleared without remediation — not
    remediable frames that only become PUBLISHABLE after crop/blur.

    SAFE_AFTER_CROP and SAFE_AFTER_BLUR match frames that still need alter
    (unrated remediable, or rated but not yet cropped/blurred).

    PUBLISHABLE matches remediable frames that are rated and altered.
    REJECTED is human reject; DO_NOT_POST is unrated classifier unsafe.
    """
    human = image_model.human_rating
    model = image_model.model_state
    remediated = or_(_has_approved_crop_exists(image_model), _has_approved_blur_exists(image_model))
    human_ok = human.in_([HumanRating.SAFE.value, HumanRating.EXCELLENT.value])

    if state == BLURRED:
        return and_(human_ok, _has_approved_blur_exists(image_model))
    if state == PUBLISHABLE:
        return and_(
            human_ok,
            model == SafetyState.SAFE_AFTER_CROP.value,
            remediated,
        )
    if state == REJECTED:
        return human == HumanRating.REJECT.value
    if state == SafetyState.SAFE.value:
        return or_(
            and_(human_ok, or_(model.is_(None), model != SafetyState.SAFE_AFTER_CROP.value)),
            and_(human.is_(None), model == SafetyState.SAFE.value),
        )
    if state == SafetyState.REVIEW.value:
        return or_(
            human == HumanRating.REVIEW.value,
            and_(human.is_(None), model == SafetyState.REVIEW.value),
        )
    if state == SafetyState.DO_NOT_POST.value:
        return and_(human.is_(None), model == SafetyState.DO_NOT_POST.value)
    if state in _SAFE_AFTER_REMEDIATE:
        return and_(
            model == SafetyState.SAFE_AFTER_CROP.value,
            or_(
                human.is_(None),
                and_(human_ok, not_(remediated)),
            ),
        )
    return model == state
