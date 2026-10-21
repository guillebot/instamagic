from __future__ import annotations

from dataclasses import dataclass

from instamagic.models.enums import HumanRating, SafetyState


@dataclass(frozen=True)
class GateResult:
    allowed: bool
    reasons: list[str]


def publishing_gate(
    *,
    attested: bool,
    human_rating: str | None,
    model_state: str | None,
    needs_crop: bool,
    has_approved_crop: bool,
    has_approved_covering_blur: bool = False,
) -> GateResult:
    reasons: list[str] = []
    if not attested:
        reasons.append("The library has no adult attestation.")
    if human_rating not in {HumanRating.SAFE.value, HumanRating.EXCELLENT.value}:
        reasons.append("A human safe or excellent rating is required.")
    if model_state == SafetyState.DO_NOT_POST.value:
        reasons.append("The local model marked this image DO_NOT_POST.")
    if needs_crop and not (has_approved_crop or has_approved_covering_blur):
        reasons.append("SAFE_AFTER_CROP requires an approved crop or covering blur.")
    return GateResult(allowed=not reasons, reasons=reasons)


def gemini_gate(
    *,
    attested: bool,
    human_rating: str | None,
    model_state: str | None,
    needs_crop: bool,
    has_approved_crop: bool,
    operator_confirmed: bool,
    policy: str,
    has_approved_covering_blur: bool = False,
) -> GateResult:
    result = publishing_gate(
        attested=attested,
        human_rating=human_rating,
        model_state=model_state,
        needs_crop=needs_crop,
        has_approved_crop=has_approved_crop,
        has_approved_covering_blur=has_approved_covering_blur,
    )
    reasons = list(result.reasons)
    if human_rating == HumanRating.REVIEW.value or (
        human_rating is None and model_state in {None, SafetyState.REVIEW.value}
    ):
        if "A human safe or excellent rating is required." not in reasons:
            reasons.append("Review images are not sent to Gemini.")
    if policy == "manual_only" and not operator_confirmed:
        reasons.append("GEMINI_IMAGE_POLICY=manual_only requires an explicit confirmation.")
    return GateResult(allowed=not reasons, reasons=reasons)
