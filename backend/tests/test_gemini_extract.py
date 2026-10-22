import pytest
from instamagic.errors import GeminiBlocked, GeminiEmpty, GeminiTruncated
from instamagic.services.gemini.google import _extract_text


def test_extract_text_block_reason() -> None:
    with pytest.raises(GeminiBlocked) as exc:
        _extract_text({"promptFeedback": {"blockReason": "PROHIBITED_CONTENT"}, "candidates": []})
    assert exc.value.reason == "PROHIBITED_CONTENT"


def test_extract_text_finish_safety() -> None:
    payload = {
        "candidates": [{"finishReason": "SAFETY", "content": {"parts": [{"text": "{}"}]}}],
    }
    with pytest.raises(GeminiBlocked) as exc:
        _extract_text(payload)
    assert exc.value.reason == "SAFETY"


def test_extract_text_max_tokens() -> None:
    payload = {"candidates": [{"finishReason": "MAX_TOKENS", "content": {"parts": []}}]}
    with pytest.raises(GeminiTruncated):
        _extract_text(payload)


def test_extract_text_success() -> None:
    payload = {"candidates": [{"content": {"parts": [{"text": '{"ok": true}'}]}}]}
    assert _extract_text(payload) == '{"ok": true}'


def test_extract_text_empty_candidates() -> None:
    with pytest.raises(GeminiEmpty):
        _extract_text({"candidates": []})
