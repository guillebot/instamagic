"""Unit tests for all-eligible chunking and pixel-policy resolution."""

from instamagic.services.gemini.captions import FakeGeminiProvider
from instamagic.services.gemini.context import (
    assert_safe_gemini_payload,
    resolve_curation_send_policy,
)
from instamagic.services.gemini.curation import (
    CurationCandidatePayload,
    CurationResult,
    build_curation_request_body,
    curation_chunk_size,
)
from instamagic.services.recommendations.curate import (
    chunk_payloads,
    merge_chunked_drafts,
    resolve_curation_limit,
)


def test_resolve_limit_zero_means_all() -> None:
    assert resolve_curation_limit(0, 100) == 100
    assert resolve_curation_limit(None, 100) == 100
    assert resolve_curation_limit(12, 100) == 12
    assert resolve_curation_limit(200, 50) == 50


def test_chunk_and_merge_preserves_ids() -> None:
    payloads = [
        CurationCandidatePayload(image_id=f"id-{i}", local_score=100 - i) for i in range(40)
    ]
    chunks = chunk_payloads(payloads, chunk_size=16)
    assert len(chunks) == 3
    provider = FakeGeminiProvider()
    results = [
        provider.curate_batch(chunk, account_voice="v", policy="text_only") for chunk in chunks
    ]
    merged = merge_chunked_drafts(results, payloads=payloads)
    assert len(merged) == 40
    assert {pick.image_id for pick in merged} == {p.image_id for p in payloads}
    assert merged[0].rank == 1


def test_send_policy_ceiling() -> None:
    assert resolve_curation_send_policy(env_policy="text_only", include_images=True) == "text_only"
    assert resolve_curation_send_policy(env_policy="preview", include_images=True) == "preview"
    assert resolve_curation_send_policy(env_policy="preview", include_images=False) == "text_only"
    assert resolve_curation_send_policy(env_policy="preview", include_images=None) == "preview"
    assert resolve_curation_send_policy(env_policy="manual_only", include_images=True) == "manual_only"
    assert curation_chunk_size("preview") < curation_chunk_size("text_only")


def test_curation_body_can_attach_preview_bytes() -> None:
    body = build_curation_request_body(
        [CurationCandidatePayload(image_id="a", local_score=50)],
        account_voice="voice",
        policy="preview",
        image_bytes_by_id={"a": b"jpeg-bytes"},
    )
    assert_safe_gemini_payload(body, policy="preview")
    blob = str(body).lower()
    assert "inline_data" in blob
    assert "originals" not in blob
    assert "file://" not in blob


def test_curation_body_text_only_rejects_bytes() -> None:
    body = build_curation_request_body(
        [CurationCandidatePayload(image_id="a", local_score=50)],
        account_voice="voice",
        policy="text_only",
        image_bytes_by_id={"a": b"should-drop"},
    )
    assert "inline_data" not in str(body).lower()


def test_merge_empty_gemini_falls_back_to_local() -> None:
    payloads = [CurationCandidatePayload(image_id="only", local_score=9, local_reasons=["r"])]
    merged = merge_chunked_drafts([CurationResult(picks=[])], payloads=payloads)
    assert len(merged) == 1
    assert merged[0].image_id == "only"
