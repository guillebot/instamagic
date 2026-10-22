from __future__ import annotations

from typing import Any

from sqlalchemy import and_, or_

from instamagic.models.enums import HumanRating, SafetyState

# Human ratings are stored lowercase; accept badge-style / case variants from filters.
_HUMAN_RATING_ALIASES = {
    "reject": HumanRating.REJECT.value,
    "review": HumanRating.REVIEW.value,
    "safe": HumanRating.SAFE.value,
    "excellent": HumanRating.EXCELLENT.value,
    # Badge / SafetyState labels users often pick when they mean a rating.
    "do_not_post": HumanRating.REJECT.value,
}


def effective_state(human_rating: str | None, model_state: str | None) -> str:
    if human_rating == HumanRating.REJECT.value:
        return SafetyState.DO_NOT_POST.value
    if human_rating == HumanRating.REVIEW.value:
        return SafetyState.REVIEW.value
    if human_rating in {HumanRating.SAFE.value, HumanRating.EXCELLENT.value}:
        return SafetyState.SAFE.value
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


def effective_state_filter_clause(image_model: Any, state: str) -> Any:
    """SQL clause matching the Review badge (human rating overrides model_state).

    Selecting SAFE must include images the operator marked safe/excellent even when
    the classifier left model_state as REVIEW / SAFE_AFTER_CROP.
    """
    human = image_model.human_rating
    model = image_model.model_state
    if state == SafetyState.SAFE.value:
        return or_(
            human.in_([HumanRating.SAFE.value, HumanRating.EXCELLENT.value]),
            and_(human.is_(None), model == SafetyState.SAFE.value),
        )
    if state == SafetyState.REVIEW.value:
        return or_(
            human == HumanRating.REVIEW.value,
            and_(human.is_(None), model == SafetyState.REVIEW.value),
        )
    if state == SafetyState.DO_NOT_POST.value:
        return or_(
            human == HumanRating.REJECT.value,
            and_(human.is_(None), model == SafetyState.DO_NOT_POST.value),
        )
    if state == SafetyState.SAFE_AFTER_CROP.value:
        return and_(human.is_(None), model == SafetyState.SAFE_AFTER_CROP.value)
    return model == state
