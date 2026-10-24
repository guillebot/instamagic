from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from instamagic.services.gemini.context import BANNED_PHRASES, GeminiContext
from pydantic import BaseModel, Field, field_validator

if TYPE_CHECKING:
    from instamagic.services.gemini.curation import CurationCandidatePayload, CurationResult

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

    def label_image(
        self,
        context: GeminiContext,
        *,
        policy: str,
        image_bytes: bytes | None,
        thinking_budget: int,
        max_output_tokens: int,
    ): ...

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

    def label_image(
        self,
        context: GeminiContext,
        *,
        policy: str,
        image_bytes: bytes | None,
        thinking_budget: int,
        max_output_tokens: int,
    ):
        from instamagic.services.gemini.labeling import ImageLabelResult

        del policy, image_bytes, thinking_budget, max_output_tokens
        mood = context.mood or "quiet"
        tags = list(dict.fromkeys([*(context.content_tags or []), mood.replace(" ", "")[:32]]))[:8]
        captions = [
            CaptionDraft(
                style=style,
                language=language,
                text=(
                    f"Luz {mood}, en una frase."
                    if language == "es"
                    else f"{style}: {mood} light, labeled for the feed."
                ),
            )
            for style, language in CAPTION_SPECS[:4]
        ]
        return ImageLabelResult(
            tags=tags,
            mood=mood,
            description=context.safe_description or "A photograph.",
            captions=captions,
        )

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
        from instamagic.services.gemini.curation import (
            CURATION_CAPTION_STYLES,
            CurationPickDraft,
        )
        from instamagic.services.gemini.curation import (
            CurationResult as Result,
        )

        del account_voice, policy, image_bytes_by_id, thinking_budget, max_output_tokens
        ordered = sorted(candidates, key=lambda item: item.local_score, reverse=True)
        picks: list[CurationPickDraft] = []
        for index, item in enumerate(ordered, start=1):
            mood = item.mood if item.mood != "unspecified" else "quiet"
            tags = list(dict.fromkeys([*item.content_tags, mood.replace(" ", "")[:32], "curated"]))[:8]
            captions = [
                CaptionDraft(
                    style=style,
                    language=language,
                    text=(
                        f"Luz {mood}, en una frase."
                        if language == "es"
                        else f"{style}: {mood} light, held for the feed."
                    ),
                )
                for style, language in CURATION_CAPTION_STYLES
            ]
            picks.append(
                CurationPickDraft(
                    image_id=item.image_id,
                    rank=index,
                    reasons=[
                        f"Local rank score {item.local_score}.",
                        *item.local_reasons[:2],
                        "Fake Gemini kept local order with diversity tags.",
                    ],
                    tags=tags,
                    captions=captions,
                )
            )
        return Result(picks=picks)
