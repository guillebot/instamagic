from __future__ import annotations

from instamagic.models.enums import HumanRating, SafetyState


def effective_state(human_rating: str | None, model_state: str | None) -> str:
    if human_rating == HumanRating.REJECT.value:
        return SafetyState.DO_NOT_POST.value
    if human_rating == HumanRating.REVIEW.value:
        return SafetyState.REVIEW.value
    if human_rating in {HumanRating.SAFE.value, HumanRating.EXCELLENT.value}:
        return SafetyState.SAFE.value
    return model_state or "UNREVIEWED"
