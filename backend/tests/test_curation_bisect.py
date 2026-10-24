from instamagic.errors import GeminiBlocked
from instamagic.services.gemini.curation import CurationCandidatePayload, CurationResult
from instamagic.services.recommendations.curate import curate_chunk_resilient, merge_chunked_drafts


class BlockingProvider:
    def __init__(self, blocked_ids: set[str]) -> None:
        self.blocked_ids = blocked_ids

    def curate_batch(
        self,
        candidates: list[CurationCandidatePayload],
        *,
        account_voice: str,
        policy: str,
        image_bytes_by_id: dict[str, bytes] | None = None,
        thinking_budget: int = 512,
        max_output_tokens: int | None = None,
    ) -> CurationResult:
        del account_voice, policy, image_bytes_by_id, thinking_budget, max_output_tokens
        for item in candidates:
            if item.image_id in self.blocked_ids:
                raise GeminiBlocked("blocked", reason="PROHIBITED_CONTENT")
        from instamagic.services.gemini.curation import CurationPickDraft

        return CurationResult(
            picks=[
                CurationPickDraft(
                    image_id=item.image_id, rank=index, reasons=["ok"], tags=[], captions=[]
                )
                for index, item in enumerate(candidates, start=1)
            ]
        )


def test_bisect_skips_single_blocked() -> None:
    chunk = [
        CurationCandidatePayload(image_id="a", local_score=10),
        CurationCandidatePayload(image_id="b", local_score=9),
        CurationCandidatePayload(image_id="c", local_score=8),
    ]
    outcome = curate_chunk_resilient(
        BlockingProvider({"b"}),
        chunk,
        account_voice="v",
        policy="text_only",
        image_bytes_by_id=None,
        thinking_budget=0,
    )
    assert outcome.blocked_ids == ["b"]
    merged = merge_chunked_drafts(outcome.chunk_results, payloads=chunk)
    assert {pick.image_id for pick in merged} == {"a", "b", "c"}
