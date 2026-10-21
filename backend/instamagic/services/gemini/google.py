from __future__ import annotations

import httpx

from instamagic.errors import NotConfigured
from instamagic.services.gemini.captions import (
    CaptionBatch,
    caption_request_text,
    system_instruction,
)
from instamagic.services.gemini.context import GeminiContext


class GoogleGeminiProvider:
    def __init__(self, api_key: str, model: str, timeout: float = 30.0) -> None:
        self._api_key = api_key
        self._model = model
        self._timeout = timeout

    def generate_captions(
        self,
        context: GeminiContext,
        *,
        policy: str,
        image_bytes: bytes | None,
    ) -> CaptionBatch:
        if not self._api_key:
            raise NotConfigured("GEMINI_API_KEY is not configured.")
        pixels = image_bytes if policy == "safe_previews" else None
        parts: list[dict[str, object]] = [{"text": caption_request_text(context)}]
        if pixels:
            import base64

            parts.append(
                {
                    "inline_data": {
                        "mime_type": "image/jpeg",
                        "data": base64.b64encode(pixels).decode("ascii"),
                    }
                }
            )
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self._model}:generateContent"
        body = {
            "system_instruction": {"parts": [{"text": system_instruction()}]},
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {
                "temperature": 0.7,
                "responseMimeType": "application/json",
                "responseSchema": {
                    "type": "OBJECT",
                    "properties": {
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
                        }
                    },
                    "required": ["captions"],
                },
            },
        }
        response = httpx.post(url, params={"key": self._api_key}, json=body, timeout=self._timeout)
        if response.status_code == 429 or response.status_code >= 500:
            response = httpx.post(url, params={"key": self._api_key}, json=body, timeout=self._timeout)
        if response.status_code >= 400:
            raise NotConfigured("Gemini request failed.")
        payload = response.json()
        text = _extract_text(payload)
        return CaptionBatch.model_validate_json(text)


def _extract_text(payload: dict) -> str:
    candidates = payload.get("candidates") or []
    if not candidates:
        raise NotConfigured("Gemini returned no candidates.")
    parts = ((candidates[0].get("content") or {}).get("parts")) or []
    for part in parts:
        if "text" in part:
            return str(part["text"])
    raise NotConfigured("Gemini returned no text.")
