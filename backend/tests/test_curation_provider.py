from instamagic.services.gemini.captions import FakeGeminiProvider
from instamagic.services.gemini.context import assert_safe_gemini_payload
from instamagic.services.gemini.curation import (
    CurationCandidatePayload,
    build_curation_request_body,
)


def test_fake_curate_batch_orders_by_local_score() -> None:
    provider = FakeGeminiProvider()
    result = provider.curate_batch(
        [
            CurationCandidatePayload(image_id="low", local_score=10, mood="soft"),
            CurationCandidatePayload(image_id="high", local_score=90, mood="dramatic"),
        ],
        account_voice="quiet craft",
        policy="text_only",
    )
    assert [pick.image_id for pick in result.picks] == ["high", "low"]
    assert result.picks[0].tags
    assert result.picks[0].captions


def test_curation_request_body_is_text_only() -> None:
    body = build_curation_request_body(
        [CurationCandidatePayload(image_id="a", local_score=50, safe_description="A portrait.")],
        account_voice="voice",
    )
    assert_safe_gemini_payload(body, policy="text_only")
    blob = str(body).lower()
    assert "inline_data" not in blob
    assert "originals" not in blob
