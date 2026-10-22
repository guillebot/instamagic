"""Per-image Gemini labeling (tags, mood, description, caption styles)."""

from __future__ import annotations

import base64
from typing import Protocol

from instamagic.services.gemini.captions import CaptionDraft
from instamagic.services.gemini.context import (
    GeminiContext,
    assert_safe_gemini_payload,
    gemini_allows_pixels,
)
from instamagic.services.gemini.curation import CURATION_CAPTION_STYLES
from pydantic import BaseModel, Field, field_validator


class ImageLabelResult(BaseModel):
    tags: list[str] = Field(default_factory=list)
    mood: str = "unspecified"
    description: str = Field(min_length=1, max_length=2000)
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


class LabelingProvider(Protocol):
    def label_image(
        self,
        context: GeminiContext,
        *,
        policy: str,
        image_bytes: bytes | None,
        thinking_budget: int,
        max_output_tokens: int,
    ) -> ImageLabelResult: ...


def label_system_instruction() -> str:
    return (
        "You label photographs for a craft-first Instagram archive. "
        "Return JSON only. Suggest content tags, mood, a short safe description "
        "(composition and light, no body claims), and one caption per requested style. "
        "Do not use engagement bait."
    )


def label_request_text(context: GeminiContext) -> str:
    styles = ", ".join(f"{style}/{language}" for style, language in CURATION_CAPTION_STYLES)
    return (
        f"Label this frame. Provide up to 12 tags, mood, description, and captions for: {styles}.\n"
        f"{context.canonical_json()}"
    )


def build_label_request_body(
    context: GeminiContext,
    *,
    policy: str,
    image_bytes: bytes | None,
    thinking_budget: int,
    max_output_tokens: int,
) -> dict[str, object]:
    pixels = image_bytes if gemini_allows_pixels(policy) else None
    parts: list[dict[str, object]] = [{"text": label_request_text(context)}]
    if pixels:
        parts.append(
            {
                "inline_data": {
                    "mime_type": "image/jpeg",
                    "data": base64.b64encode(pixels).decode("ascii"),
                }
            }
        )
    body: dict[str, object] = {
        "system_instruction": {"parts": [{"text": label_system_instruction()}]},
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {
            "temperature": 0.5,
            "maxOutputTokens": max_output_tokens,
            "thinkingConfig": {"thinkingBudget": thinking_budget},
            "responseMimeType": "application/json",
            "responseSchema": {
                "type": "OBJECT",
                "properties": {
                    "tags": {"type": "ARRAY", "items": {"type": "STRING"}},
                    "mood": {"type": "STRING"},
                    "description": {"type": "STRING"},
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
                "required": ["tags", "mood", "description", "captions"],
            },
        },
    }
    assert_safe_gemini_payload(body, policy=policy)
    return body


def label_status_is_terminal(status: str | None) -> bool:
    if not status:
        return False
    if status == "ok" or status == "empty":
        return True
    return status.startswith("blocked:")


def format_blocked_status(reason: str) -> str:
    clean = reason.strip().replace(" ", "_")[:48]
    return f"blocked:{clean}"


def should_retry_label(status: str | None, attempts: int, *, max_attempts: int = 5) -> bool:
    if label_status_is_terminal(status):
        return False
    if status == "error" and attempts >= max_attempts:
        return False
    return status is None or status == "error"
