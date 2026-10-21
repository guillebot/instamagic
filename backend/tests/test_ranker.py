from instamagic.services.recommendations.rank import CandidateInput, rank_candidates


def test_ranker_holds_back_near_duplicates() -> None:
    base = "0000000000000000"
    close = "0000000000000001"
    other = "ffffffffffffffff"
    items = [
        CandidateInput("a", 0.9, "excellent", 0.2, base, True, "4:5", "REVIEW", 0.8, 0.8),
        CandidateInput("b", 0.85, "excellent", 0.2, close, True, "4:5", "REVIEW", 0.8, 0.8),
        CandidateInput("c", 0.4, "safe", 0.9, other, False, None, "REVIEW", 0.2, 0.2),
    ]
    ranked = rank_candidates(items, limit=20, hamming_threshold=10)
    ids = [item.image_id for item in ranked]
    assert ids[0] == "a"
    assert "b" not in ids
    assert ranked[0].held_back >= 1
    assert any("performance history" in reason for reason in ranked[0].reasons)
