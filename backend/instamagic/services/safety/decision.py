"""Safety states are about whether a frame should be queued, not how good it looks.

The crop search exists to keep unsuitable regions out of a post. It is not a way around moderation.
"""

from __future__ import annotations

from dataclasses import dataclass

from instamagic.models.enums import SafetyState


@dataclass(frozen=True)
class DecisionInput:
    attested: bool
    detector_available: bool
    uncertain: bool
    clear_exposed: bool
    viable_safety_crop: bool


def decide(data: DecisionInput) -> SafetyState:
    if not data.attested or not data.detector_available:
        return SafetyState.REVIEW
    if data.uncertain and not data.clear_exposed:
        return SafetyState.REVIEW
    if data.clear_exposed:
        if data.viable_safety_crop:
            return SafetyState.SAFE_AFTER_CROP
        return SafetyState.DO_NOT_POST
    # No exposed region is still not an automatic clearance. Lingerie and context stay with the operator.
    return SafetyState.REVIEW
