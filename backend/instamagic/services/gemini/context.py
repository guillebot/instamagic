from __future__ import annotations

import hashlib
import json
from typing import Any

from instamagic.errors import GeminiBlocked
from instamagic.services.recommendations.gates import gemini_gate
from pydantic import BaseModel, Field

BANNED_PHRASES = (
    "comment yes",
    "tag 5 friends",
    "tag five friends",
    "follow for follow",
    "follow4follow",
)


class GeminiContext(BaseModel):
    content_tags: list[str] = Field(default_factory=list)
    composition: str
    mood: str
    safe_description: str
    crop: dict[str, Any] | None = None
    historical_performance: dict[str, Any] = Field(default_factory=dict)
    account_context: dict[str, Any] = Field(default_factory=dict)
    recent_posts: list[dict[str, Any]] = Field(default_factory=list)
    operator_confirmed: bool = False

    def canonical_json(self) -> str:
        payload = self.model_dump(exclude={"operator_confirmed"})
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))

    def sha256(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()


def build_context(
    *,
    attested: bool,
    human_rating: str | None,
    model_state: str | None,
    needs_crop: bool,
    has_approved_crop: bool,
    policy: str,
    operator_confirmed: bool,
    content_tags: list[str],
    composition: str,
    mood: str,
    safe_description: str,
    crop: dict[str, Any] | None,
    has_approved_covering_blur: bool = False,
) -> GeminiContext:
    gate = gemini_gate(
        attested=attested,
        human_rating=human_rating,
        model_state=model_state,
        needs_crop=needs_crop,
        has_approved_crop=has_approved_crop,
        has_approved_covering_blur=has_approved_covering_blur,
        operator_confirmed=operator_confirmed,
        policy=policy,
    )
    if not gate.allowed:
        raise GeminiBlocked(" ".join(gate.reasons))
    description = safe_description
    crop_payload = crop
    if model_state == "SAFE_AFTER_CROP":
        if crop is None and not has_approved_covering_blur:
            raise GeminiBlocked("SAFE_AFTER_CROP requires an approved crop or covering blur.")
        if crop is not None:
            description = (
                f"Describe only the approved {crop.get('aspect_ratio', 'crop')} crop. "
                "Do not infer what was excluded from the frame. "
                + safe_description
            )
        else:
            description = (
                "Describe the photograph with approved covering blur applied. "
                "Do not infer what was covered. "
                + safe_description
            )
            crop_payload = None
    return GeminiContext(
        content_tags=content_tags,
        composition=composition,
        mood=mood,
        safe_description=description,
        crop=crop_payload,
        historical_performance={"available": False},
        account_context={"goal": "organic audience growth from an existing photography archive"},
        recent_posts=[],
        operator_confirmed=operator_confirmed,
    )


def assert_no_original_reference(payload: dict[str, Any]) -> None:
    blob = json.dumps(payload)
    lowered = blob.lower()
    if "originals" in lowered or "/photos/" in lowered or "inline_data" in lowered:
        raise GeminiBlocked("Gemini payload included a forbidden reference.")
