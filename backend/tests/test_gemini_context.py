import json

import pytest
from instamagic.errors import GeminiBlocked
from instamagic.services.gemini.context import build_context
from instamagic.services.gemini.google import build_request_body


def _context():
    return build_context(
        attested=True,
        human_rating="safe",
        model_state="REVIEW",
        needs_crop=False,
        has_approved_crop=False,
        policy="text_only",
        operator_confirmed=False,
        content_tags=["portrait", "window light"],
        composition="portrait",
        mood="soft",
        safe_description="A portrait photograph.",
        crop=None,
    )


def test_context_has_no_file_path() -> None:
    context = _context()
    blob = context.canonical_json()
    assert "originals" not in blob
    assert "/" not in blob
    assert "inline_data" not in blob


def test_context_rejects_do_not_post_and_missing_attestation() -> None:
    with pytest.raises(GeminiBlocked):
        build_context(
            attested=True,
            human_rating="safe",
            model_state="DO_NOT_POST",
            needs_crop=False,
            has_approved_crop=False,
            policy="text_only",
            operator_confirmed=True,
            content_tags=[],
            composition="portrait",
            mood="soft",
            safe_description="A portrait photograph.",
            crop=None,
        )
    with pytest.raises(GeminiBlocked):
        build_context(
            attested=False,
            human_rating="safe",
            model_state="REVIEW",
            needs_crop=False,
            has_approved_crop=False,
            policy="text_only",
            operator_confirmed=True,
            content_tags=[],
            composition="portrait",
            mood="soft",
            safe_description="A portrait photograph.",
            crop=None,
        )


def test_text_only_request_drops_image_bytes() -> None:
    body = build_request_body(_context(), policy="text_only", image_bytes=b"original-bytes-should-not-leave")
    blob = json.dumps(body)
    assert "inline_data" not in blob
    assert "original-bytes" not in blob


def test_safe_previews_can_attach_only_supplied_derivative_bytes() -> None:
    body = build_request_body(_context(), policy="safe_previews", image_bytes=b"derivative-jpeg")
    blob = json.dumps(body)
    assert "inline_data" in blob
    assert "original" not in blob


def test_text_only_rejects_inline_data_in_payload_guard() -> None:
    from instamagic.errors import GeminiBlocked
    from instamagic.services.gemini.context import assert_safe_gemini_payload

    with pytest.raises(GeminiBlocked):
        assert_safe_gemini_payload({"inline_data": {"data": "x"}}, policy="text_only")
    assert_safe_gemini_payload(
        {"inline_data": {"data": "x", "mime_type": "image/jpeg"}},
        policy="safe_previews",
    )
