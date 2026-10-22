from __future__ import annotations

from typing import Protocol

from instamagic.services.gemini.context import BANNED_PHRASES, GeminiContext
from pydantic import BaseModel, Field, field_validator

CAPTION_SPECS: tuple[tuple[str, str], ...] = (
    ("minimal", "en"),
    ("elegant", "en"),
    ("playful", "en"),
    ("storytelling", "en"),
    ("photography", "en"),
    ("question", "en"),
    ("spanish", "es"),
    ("bilingual", "bilingual"),
)


class CaptionDraft(BaseModel):
    style: str
    language: str
    text: str = Field(min_length=1, max_length=2000)

    @field_validator("text")
    @classmethod
    def reject_bait(cls, value: str) -> str:
        lowered = value.lower()
        for phrase in BANNED_PHRASES:
            if phrase in lowered:
                raise ValueError("Caption uses engagement bait.")
        return value.strip()


class CaptionBatch(BaseModel):
    captions: list[CaptionDraft] = Field(min_length=1, max_length=12)


class GeminiProvider(Protocol):
    def generate_captions(
        self,
        context: GeminiContext,
        *,
        policy: str,
        image_bytes: bytes | None,
    ) -> CaptionBatch: ...


def system_instruction() -> str:
    return (
        "You write Instagram captions for the account owner's own photographs. "
        "Return only JSON matching the schema. Write distinct options for each requested style. "
        "Do not use engagement bait, including requests to comment YES, tag friends, or follow for follow. "
        "Do not claim anything about the person's body. Stay with mood, light, and photographic craft. "
        "Prefer insightful observations about craft that help grow an organic photography audience. "
        "Spanish captions are in Spanish. Bilingual captions use both English and Spanish."
    )


def caption_request_text(context: GeminiContext) -> str:
    styles = ", ".join(f"{style}/{language}" for style, language in CAPTION_SPECS)
    voice = ""
    if isinstance(context.account_context, dict) and context.account_context.get("voice"):
        voice = f" Honor this account voice: {context.account_context['voice']}."
    return (
        "Write one caption for each of these style/language pairs: "
        f"{styles}.{voice} Context follows.\n{context.canonical_json()}"
    )


class FakeGeminiProvider:
    def generate_captions(
        self,
        context: GeminiContext,
        *,
        policy: str,
        image_bytes: bytes | None,
    ) -> CaptionBatch:
        del policy, image_bytes
        mood = context.mood or "quiet"
        drafts = []
        for style, language in CAPTION_SPECS:
            if language == "es":
                text = f"Luz {mood}, en una frase."
            elif language == "bilingual":
                text = f"Quiet light. Luz {mood}."
            elif style == "question":
                text = "Which part of the light stays with you?"
            else:
                text = f"{style}: {mood} light, held simply."
            drafts.append(CaptionDraft(style=style, language=language, text=text))
        return CaptionBatch(captions=drafts)
