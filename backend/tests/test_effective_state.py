"""Unit tests for lifecycle badge derivation (Safe vs Publishable)."""

from instamagic.services.images.presentation import PUBLISHABLE, REJECTED, effective_state


def test_safe_is_originally_clear_or_human_cleared_review() -> None:
    assert effective_state(None, "SAFE") == "SAFE"
    assert effective_state("safe", "SAFE") == "SAFE"
    assert effective_state("excellent", "REVIEW") == "SAFE"
    assert effective_state("safe", None) == "SAFE"


def test_publishable_requires_remediation_and_rating() -> None:
    assert effective_state("safe", "SAFE_AFTER_CROP") == "SAFE_AFTER_CROP"
    assert effective_state("safe", "SAFE_AFTER_CROP", has_approved_crop=True) == PUBLISHABLE
    assert (
        effective_state("excellent", "SAFE_AFTER_CROP", has_approved_covering_blur=True)
        == PUBLISHABLE
    )
    assert effective_state(None, "SAFE_AFTER_CROP", has_approved_crop=True) == "SAFE_AFTER_CROP"


def test_rejected_and_unsafe() -> None:
    assert effective_state("reject", "REVIEW") == REJECTED
    assert effective_state("reject", "SAFE_AFTER_CROP") == REJECTED
    assert effective_state(None, "DO_NOT_POST") == "DO_NOT_POST"


def test_review_and_unreviewed() -> None:
    assert effective_state("review", "SAFE") == "REVIEW"
    assert effective_state(None, "REVIEW") == "REVIEW"
    assert effective_state(None, None) == "UNREVIEWED"
