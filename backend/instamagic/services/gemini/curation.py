"""Gemini request/response shapes for batch curation.

Default text_only sends metadata only. With safe_previews / preview / selected,
derivative JPEG bytes (preview or thumb) may be attached — never originals or paths.
"""

from __future__ import annotations

import base64
import json
from typing import Protocol

from instamagic.services.gemini.captions import CaptionDraft
from instamagic.services.gemini.context import assert_safe_gemini_payload, gemini_allows_pixels
from pydantic import BaseModel, Field, field_validator

# One Gemini call stays small enough for cost/latency; run_curation chunks larger shortlists.
GEMINI_CURATION_CHUNK_SIZE = 8
# Fewer frames per call when derivative pixels ride along (payload + token cost).
GEMINI_CURATION_PIXEL_CHUNK_SIZE = 4

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
        image_bytes_by_id: dict[str, bytes] | None = None,
        thinking_budget: int = 512,
        max_output_tokens: int | None = None,
    ) -> CurationResult: ...


def curation_chunk_size(policy: str) -> int:
    return GEMINI_CURATION_PIXEL_CHUNK_SIZE if gemini_allows_pixels(policy) else GEMINI_CURATION_CHUNK_SIZE


def curation_max_output_tokens(candidate_count: int) -> int:
    count = max(1, candidate_count)
    return min(65536, max(2048, count * 600))


def curation_system_instruction() -> str:
    return (
        "You curate a photography Instagram feed from the operator's own archive. "
        "Rank every candidate (1 = best post next). Prefer diversity of mood and composition. "
        "Use the provided tags and descriptions; when JPEG parts follow, ground reasons in what you see "
        "without inventing identity or body details. "
        "Return one ranked row per candidate image_id — do not omit ids. "
        "Do not use engagement bait. Do not claim anything about bodies. "
        "Return only JSON matching the schema. Never invent filesystem paths."
    )


def curation_request_text(*, candidates: list[CurationCandidatePayload], account_voice: str) -> str:
    styles = ", ".join(f"{style}/{language}" for style, language in CURATION_CAPTION_STYLES)
    payload = [item.model_dump() for item in candidates]
    ids = [item.image_id for item in candidates]
    return (
        f"Account voice: {account_voice[:400]}\n"
        f"Candidates (JSON): {json.dumps(payload, sort_keys=True)}\n"
        f"Rank every candidate. Required image_ids: {json.dumps(ids)}. "
        f"When JPEG parts follow, each is labeled with its image_id — match them to the JSON. "
        f"For each ranked row provide reasons, up to 8 tags, and captions for: {styles}."
    )


def build_curation_request_body(
    candidates: list[CurationCandidatePayload],
    *,
    account_voice: str,
    policy: str = "text_only",
    image_bytes_by_id: dict[str, bytes] | None = None,
    thinking_budget: int = 512,
    max_output_tokens: int | None = None,
) -> dict[str, object]:
    parts: list[dict[str, object]] = [
        {"text": curation_request_text(candidates=candidates, account_voice=account_voice)}
    ]
    if gemini_allows_pixels(policy) and image_bytes_by_id:
        for item in candidates:
            raw = image_bytes_by_id.get(item.image_id)
            if not raw:
                continue
            parts.append({"text": f"image_id={item.image_id}"})
            parts.append(
                {
                    "inline_data": {
                        "mime_type": "image/jpeg",
                        "data": base64.b64encode(raw).decode("ascii"),
                    }
                }
            )
    body: dict[str, object] = {
        "system_instruction": {"parts": [{"text": curation_system_instruction()}]},
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {
            "temperature": 0.5,
            "maxOutputTokens": max_output_tokens or curation_max_output_tokens(len(candidates)),
            "thinkingConfig": {"thinkingBudget": thinking_budget},
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
    assert_safe_gemini_payload(body, policy=policy)
    return body
