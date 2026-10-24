from __future__ import annotations

from dataclasses import dataclass

from instamagic.models.enums import HumanRating, SafetyState

BLUR_NOT_VERIFIED_REASON = "Blur covers are not verified. Blur and verify first."


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
    has_approved_blur: bool = False,
    do_not_post_blur_unlocked: bool = False,
    blur_verification_pending: bool = False,
) -> GateResult:
    reasons: list[str] = []
    if not attested:
        reasons.append("The library has no adult attestation.")
    if human_rating not in {HumanRating.SAFE.value, HumanRating.EXCELLENT.value}:
        reasons.append("A human safe or excellent rating is required.")
    if model_state == SafetyState.DO_NOT_POST.value and not do_not_post_blur_unlocked:
        reasons.append("The local model marked this image DO_NOT_POST.")
    if model_state == SafetyState.DO_NOT_POST.value and do_not_post_blur_unlocked and not has_approved_blur:
        reasons.append("Verified blur unlock requires approved blur covers.")
    if needs_crop and not (has_approved_crop or has_approved_covering_blur):
        reasons.append("SAFE_AFTER_CROP requires an approved crop or covering blur.")
    if blur_verification_pending:
        reasons.append(BLUR_NOT_VERIFIED_REASON)
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
    has_approved_blur: bool = False,
    do_not_post_blur_unlocked: bool = False,
    blur_verification_pending: bool = False,
) -> GateResult:
    result = publishing_gate(
        attested=attested,
        human_rating=human_rating,
        model_state=model_state,
        needs_crop=needs_crop,
        has_approved_crop=has_approved_crop,
        has_approved_covering_blur=has_approved_covering_blur,
        has_approved_blur=has_approved_blur,
        do_not_post_blur_unlocked=do_not_post_blur_unlocked,
        blur_verification_pending=blur_verification_pending,
    )
    reasons = list(result.reasons)
    # Human "review" rating is blocked. Classifier REVIEW with human safe/excellent is allowed.
    if human_rating == HumanRating.REVIEW.value:
        if "A human safe or excellent rating is required." not in reasons:
            reasons.append("Review images are not sent to Gemini.")
    elif human_rating is None and model_state in {None, SafetyState.REVIEW.value}:
        if "A human safe or excellent rating is required." not in reasons:
            reasons.append("Review images are not sent to Gemini.")
    if policy == "manual_only" and not operator_confirmed:
        reasons.append("GEMINI_IMAGE_POLICY=manual_only requires an explicit confirmation.")
    return GateResult(allowed=not reasons, reasons=reasons)


def prefetch_gate(
    *,
    attested: bool,
    human_rating: str | None,
    model_state: str | None,
    classifier_clean: bool,
    has_any_blur: bool,
    skip_reason: str | None,
    policy: str,
    operator_confirmed: bool,
) -> GateResult:
    """Gemini prefetch for UNRATED photos the local classifier found clean (state REVIEW, no hits).

    This only allows generating labels/captions/hashtags ahead of the operator's rating. It grants no
    publish, queue or curation eligibility: ``publishing_gate`` / ``gemini_gate`` are unchanged and still
    require a human safe/excellent rating. Never true for DO_NOT_POST, SAFE_AFTER_CROP, any image with
    blur covers, detector hits, or any human rating (rated photos use the central gate instead).
    """
    reasons: list[str] = []
    if not attested:
        reasons.append("The library has no adult attestation.")
    if human_rating is not None:
        reasons.append("Prefetch is only for unrated photos.")
    if model_state != SafetyState.REVIEW.value:
        reasons.append("Prefetch requires local model state REVIEW.")
    if not classifier_clean:
        reasons.append("The local classifier reported detections or uncertainty.")
    if has_any_blur:
        reasons.append("Images with blur covers are never prefetched.")
    if skip_reason is not None:
        reasons.append("Skipped image.")
    if policy == "manual_only" and not operator_confirmed:
        reasons.append("GEMINI_IMAGE_POLICY=manual_only requires an explicit confirmation.")
    return GateResult(allowed=not reasons, reasons=reasons)
