from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator

from instamagic.services.gemini.context import caption_is_allowed


class CaptionDraft(BaseModel):
    style: str
    language: str
    text: str = Field(min_length=1, max_length=2000)

    @field_validator("style")
    @classmethod
    def _style(cls, value: str) -> str:
        allowed = {
            "minimal",
            "elegant",
            "playful",
            "storytelling",
            "photography",
            "question",
            "spanish",
            "bilingual",
        }
        if value not in allowed:
            raise ValueError("Unknown caption style.")
        return value

    @field_validator("language")
    @classmethod
    def _language(cls, value: str) -> str:
        if value not in {"en", "es", "bilingual"}:
            raise ValueError("Unknown caption language.")
        return value

    @field_validator("text")
    @classmethod
    def _text(cls, value: str) -> str:
        cleaned = value.strip()
        if not caption_is_allowed(cleaned):
            raise ValueError("Caption uses engagement bait.")
        return cleaned


class CaptionBatch(BaseModel):
    captions: list[CaptionDraft] = Field(min_length=1, max_length=12)


REQUESTED_CAPTIONS: tuple[tuple[str, str], ...] = (
    ("minimal", "en"),
    ("elegant", "en"),
    ("playful", "en"),
    ("storytelling", "en"),
    ("photography", "en"),
    ("question", "en"),
    ("spanish", "es"),
    ("bilingual", "bilingual"),
)


def caption_prompt(context: dict[str, Any]) -> str:
    styles = ", ".join(f"{style}/{language}" for style, language in REQUESTED_CAPTIONS)
    return (
        "Write Instagram caption options for the operator's own photograph. "
        "Use only the JSON context. Do not invent nudity or identity details. "
        "Do not use engagement bait such as asking people to comment YES, tag friends, or follow for follow. "
        f"Return JSON with a captions array. Include these style/language pairs: {styles}. "
        "Each item has style, language, and text.\n"
        f"Context:\n{context}"
    )


class GeminiProvider:
    def generate(self, context: dict[str, Any], image_bytes: bytes | None, policy: str) -> CaptionBatch:
        raise NotImplementedError


class FakeGeminiProvider(GeminiProvider):
    def generate(self, context: dict[str, Any], image_bytes: bytes | None, policy: str) -> CaptionBatch:
        if policy != "safe_previews":
            image_bytes = None
        _ = image_bytes
        mood = str(context.get("mood") or "quiet")
        drafts = []
        for style, language in REQUESTED_CAPTIONS:
            if language == "es":
                text = f"Luz {mood}, encuadre {style}."
            elif language == "bilingual":
                text = f"Quiet light. Luz {mood}."
            else:
                text = f"{style.capitalize()} frame, {mood} light."
            drafts.append(CaptionDraft(style=style, language=language, text=text))
        return CaptionBatch(captions=drafts)


class GoogleGeminiProvider(GeminiProvider):
    def __init__(self, api_key: str, model: str) -> None:
        self.api_key = api_key
        self.model = model

    def generate(self, context: dict[str, Any], image_bytes: bytes | None, policy: str) -> CaptionBatch:
        import httpx

        if policy != "safe_previews":
            image_bytes = None
        parts: list[dict[str, Any]] = [{"text": caption_prompt(context)}]
        if image_bytes:
            import base64

            parts.append(
                {
                    "inline_data": {
                        "mime_type": "image/jpeg",
                        "data": base64.b64encode(image_bytes).decode("ascii"),
                    }
                }
            )
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
        response = httpx.post(
            url,
            params={"key": self.api_key},
            json={
                "contents": [{"role": "user", "parts": parts}],
                "generationConfig": {"responseMimeType": "application/json", "temperature": 0.7},
            },
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        text = payload["candidates"][0]["content"]["parts"][0]["text"]
        return CaptionBatch.model_validate_json(text)
