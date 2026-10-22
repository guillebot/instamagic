"""Gemini request/response shapes for batch curation (text-only; no library pixels)."""

from __future__ import annotations

import json
from typing import Protocol

from instamagic.services.gemini.captions import CaptionDraft
from instamagic.services.gemini.context import assert_safe_gemini_payload
from pydantic import BaseModel, Field, field_validator

CURATION_CAPTION_STYLES: tuple[tuple[str, str], ...] = (
    ("minimal", "en"),
    ("elegant", "en"),
    ("storytelling", "en"),
    ("photography", "en"),
)


class CurationCandidatePayload(BaseModel):
    image_id: str
    local_score: int
    human_rating: str | None = None
    model_state: str | None = None
    aesthetic_score: float = 0
    sharpness: float = 0
    composition: float = 0
    uniqueness: float = 0
    content_tags: list[str] = Field(default_factory=list)
    composition_label: str = "unknown orientation"
    mood: str = "unspecified"
    safe_description: str = "A photograph."
    local_reasons: list[str] = Field(default_factory=list)


class CurationPickDraft(BaseModel):
    image_id: str
    rank: int = Field(ge=1)
    reasons: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    captions: list[CaptionDraft] = Field(default_factory=list)

    @field_validator("tags")
    @classmethod
    def clean_tags(cls, values: list[str]) -> list[str]:
        cleaned: list[str] = []
        for raw in values:
            tag = raw.strip().lstrip("#").lower().replace(" ", "")
            if not tag or any(ch in tag for ch in "/\\\x00\n\r\t"):
                continue
            if tag not in cleaned:
                cleaned.append(tag[:48])
        return cleaned[:12]


class CurationResult(BaseModel):
    picks: list[CurationPickDraft] = Field(default_factory=list)


class CurationProvider(Protocol):
    def curate_batch(
        self,
        candidates: list[CurationCandidatePayload],
        *,
        account_voice: str,
        policy: str,
    ) -> CurationResult: ...


def curation_system_instruction() -> str:
    return (
        "You curate a photography Instagram feed from the operator's own archive. "
        "Rank the safest, most distinctive frames for posting. Prefer diversity of mood and composition. "
        "Suggest short content tags and a few caption options per pick. "
        "Do not use engagement bait. Do not claim anything about bodies. "
        "Return only JSON matching the schema. Never invent filesystem paths."
    )


def curation_request_text(*, candidates: list[CurationCandidatePayload], account_voice: str) -> str:
    styles = ", ".join(f"{style}/{language}" for style, language in CURATION_CAPTION_STYLES)
    payload = [item.model_dump() for item in candidates]
    return (
        f"Account voice: {account_voice[:400]}\n"
        f"Candidates (JSON): {json.dumps(payload, sort_keys=True)}\n"
        f"Pick and rank the best subset (up to {len(candidates)}). "
        f"For each pick provide reasons, up to 8 tags, and captions for: {styles}."
    )


def build_curation_request_body(
    candidates: list[CurationCandidatePayload],
    *,
    account_voice: str,
) -> dict[str, object]:
    body: dict[str, object] = {
        "system_instruction": {"parts": [{"text": curation_system_instruction()}]},
        "contents": [
            {
                "role": "user",
                "parts": [{"text": curation_request_text(candidates=candidates, account_voice=account_voice)}],
            }
        ],
        "generationConfig": {
            "temperature": 0.5,
            "responseMimeType": "application/json",
            "responseSchema": {
                "type": "OBJECT",
                "properties": {
                    "picks": {
                        "type": "ARRAY",
                        "items": {
                            "type": "OBJECT",
                            "properties": {
                                "image_id": {"type": "STRING"},
                                "rank": {"type": "INTEGER"},
                                "reasons": {"type": "ARRAY", "items": {"type": "STRING"}},
                                "tags": {"type": "ARRAY", "items": {"type": "STRING"}},
                                "captions": {
                                    "type": "ARRAY",
                                    "items": {
                                        "type": "OBJECT",
                                        "properties": {
                                            "style": {"type": "STRING"},
                                            "language": {"type": "STRING"},
                                            "text": {"type": "STRING"},
                                        },
                                        "required": ["style", "language", "text"],
                                    },
                                },
                            },
                            "required": ["image_id", "rank", "reasons", "tags", "captions"],
                        },
                    }
                },
                "required": ["picks"],
            },
        },
    }
    assert_safe_gemini_payload(body, policy="text_only")
    return body
