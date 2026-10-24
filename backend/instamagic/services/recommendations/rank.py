from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class CandidateInput:
    image_id: str
    aesthetic_score: float
    human_rating: str | None
    uniqueness: float
    phash: str | None
    has_approved_crop: bool
    crop_aspect: str | None
    model_state: str | None
    sharpness: float
    composition: float
    performance_score: float = 0.0


@dataclass
class RankedCandidate:
    image_id: str
    score: int
    reasons: list[str] = field(default_factory=list)
    held_back: int = 0


def rank_candidates(
    items: list[CandidateInput],
    limit: int = 20,
    offset: int = 0,
    hamming_threshold: int = 10,
    *,
    performance_weight: float = 0.0,
) -> list[RankedCandidate]:
    weight = max(0.0, min(0.5, performance_weight))
    scored = [(_score(item, performance_weight=weight), item) for item in items]
    scored.sort(key=lambda pair: pair[0], reverse=True)
    selected: list[tuple[float, CandidateInput, int]] = []
    for score, item in scored:
        held = 0
        suppressed = False
        for _, chosen, _ in selected:
            distance = _hamming(item.phash, chosen.phash)
            if distance is not None and distance <= hamming_threshold:
                suppressed = True
                break
        if suppressed:
            continue
        for other_score, other in scored:
            if other.image_id == item.image_id:
                continue
            distance = _hamming(item.phash, other.phash)
            if distance is not None and distance <= hamming_threshold and other_score <= score:
                held += 1
        selected.append((score, item, held))
        if len(selected) >= offset + limit:
            break
    window = selected[offset : offset + limit]
    return [
        RankedCandidate(
            image_id=item.image_id,
            score=round(score * 100),
            reasons=_reasons(item, held, performance_weight=weight),
            held_back=held,
        )
        for score, item, held in window
    ]


def _score(item: CandidateInput, *, performance_weight: float = 0.0) -> float:
    human = {"excellent": 1.0, "safe": 0.75}.get(item.human_rating or "", 0.4)
    crop_bonus = 1.0 if item.has_approved_crop else 0.4
    local = (
        0.35 * _clamp(item.aesthetic_score)
        + 0.25 * human
        + 0.20 * _clamp(item.uniqueness)
        + 0.10 * crop_bonus
        + 0.10 * _clamp(item.composition)
    )
    if performance_weight <= 0:
        return local
    perf = _clamp(item.performance_score)
    return (1.0 - performance_weight) * local + performance_weight * perf


def _reasons(item: CandidateInput, held: int, *, performance_weight: float = 0.0) -> list[str]:
    reasons: list[str] = []
    if item.sharpness >= 0.6:
        reasons.append("High local sharpness.")
    if item.composition >= 0.6:
        reasons.append("Face or subject sits near a thirds position.")
    if item.uniqueness >= 0.7:
        reasons.append("Visually distinct from the rest of the scanned archive.")
    if item.has_approved_crop and item.crop_aspect:
        reasons.append(f"Approved {item.crop_aspect} crop is available.")
    if item.model_state:
        reasons.append(f"Local safety state is {item.model_state}.")
    if held:
        reasons.append(f"{held} near-duplicate frames were held back.")
    if performance_weight > 0 and item.performance_score > 0.05:
        reasons.append("Past Instagram engagement for similar tags or this frame boosted the rank.")
    elif performance_weight > 0:
        reasons.append("Little Instagram history yet — rank leans on local quality signals.")
    else:
        reasons.append("No Instagram performance history yet, so this rank uses local signals only.")
    return reasons


def _hamming(left: str | None, right: str | None) -> int | None:
    if not left or not right:
        return None
    return (int(left, 16) ^ int(right, 16)).bit_count()


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))
