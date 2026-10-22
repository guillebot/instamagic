from instamagic.services.recommendations.gates import gemini_gate, publishing_gate


def test_queue_requires_attestation_human_rating_and_crop() -> None:
    blocked = publishing_gate(
        attested=False,
        human_rating="safe",
        model_state="REVIEW",
        needs_crop=False,
        has_approved_crop=False,
    )
    assert not blocked.allowed
    needs_crop = publishing_gate(
        attested=True,
        human_rating="excellent",
        model_state="SAFE_AFTER_CROP",
        needs_crop=True,
        has_approved_crop=False,
    )
    assert not needs_crop.allowed
    do_not_post = publishing_gate(
        attested=True,
        human_rating="safe",
        model_state="DO_NOT_POST",
        needs_crop=False,
        has_approved_crop=True,
    )
    assert not do_not_post.allowed
    allowed = publishing_gate(
        attested=True,
        human_rating="safe",
        model_state="REVIEW",
        needs_crop=False,
        has_approved_crop=False,
    )
    assert allowed.allowed


def test_queue_accepts_approved_covering_blur_for_safe_after_crop() -> None:
    allowed = publishing_gate(
        attested=True,
        human_rating="safe",
        model_state="SAFE_AFTER_CROP",
        needs_crop=True,
        has_approved_crop=False,
        has_approved_covering_blur=True,
    )
    assert allowed.allowed


def test_queue_rejects_do_not_post_even_with_covering_blur() -> None:
    result = publishing_gate(
        attested=True,
        human_rating="safe",
        model_state="DO_NOT_POST",
        needs_crop=False,
        has_approved_crop=False,
        has_approved_covering_blur=True,
    )
    assert not result.allowed


def test_queue_rejects_blur_that_does_not_cover_when_flag_false() -> None:
    """Missed coverage is represented as has_approved_covering_blur=False (fail closed)."""
    result = publishing_gate(
        attested=True,
        human_rating="safe",
        model_state="SAFE_AFTER_CROP",
        needs_crop=True,
        has_approved_crop=False,
        has_approved_covering_blur=False,
    )
    assert not result.allowed
    assert any("covering blur" in reason or "approved crop" in reason for reason in result.reasons)


def test_gemini_allows_safe_human_rating_with_classifier_review() -> None:
    result = gemini_gate(
        attested=True,
        human_rating="excellent",
        model_state="REVIEW",
        needs_crop=False,
        has_approved_crop=False,
        operator_confirmed=True,
        policy="preview",
    )
    assert result.allowed


def test_gemini_manual_policy_needs_confirmation() -> None:
    result = gemini_gate(
        attested=True,
        human_rating="safe",
        model_state="REVIEW",
        needs_crop=False,
        has_approved_crop=False,
        operator_confirmed=False,
        policy="manual_only",
    )
    assert not result.allowed
