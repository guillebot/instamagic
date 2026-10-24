from __future__ import annotations

from dataclasses import dataclass, field

from instamagic.services.geometry import Box
from instamagic.services.safety.labels import (
    AMBIGUOUS_SCORE,
    CLEAR_SCORE,
    EXPLICIT_LABELS,
    EXPOSED_LABELS,
    LINGERIE_LABELS,
    NUDITY_LABELS,
    SKIN_LABELS,
)


@dataclass(frozen=True)
class Region:
    label: str
    score: float
    box: Box


@dataclass
class SafetySignals:
    regions: list[Region] = field(default_factory=list)
    detector_available: bool = False
    uncertain: bool = False
    model_name: str = "none"

    def clear_exposed(self) -> list[Region]:
        return [
            region
            for region in self.regions
            if region.label in EXPOSED_LABELS and region.score >= CLEAR_SCORE
        ]

    def has_ambiguous_exposure(self) -> bool:
        return any(
            region.label in EXPOSED_LABELS and AMBIGUOUS_SCORE <= region.score < CLEAR_SCORE
            for region in self.regions
        )

    def max_score(self, labels: frozenset[str]) -> float:
        scores = [region.score for region in self.regions if region.label in labels]
        return max(scores) if scores else 0.0

    def skin_ratio(self, image_area: int) -> float:
        if image_area <= 0:
            return 0.0
        area = sum(
            region.box.area
            for region in self.regions
            if region.label in SKIN_LABELS and region.score >= CLEAR_SCORE
        )
        return min(1.0, area / image_area)


def score_summary(signals: SafetySignals, image_area: int) -> dict[str, float]:
    return {
        "nudity_score": signals.max_score(NUDITY_LABELS),
        "explicit_score": signals.max_score(EXPLICIT_LABELS),
        "lingerie_score": signals.max_score(LINGERIE_LABELS),
        "skin_exposure_score": signals.skin_ratio(image_area),
    }
