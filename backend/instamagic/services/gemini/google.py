from __future__ import annotations

import httpx
from instamagic.errors import NotConfigured
from instamagic.services.gemini.captions import (
    CaptionBatch,
    caption_request_text,
    system_instruction,
)
from instamagic.services.gemini.context import GeminiContext, assert_safe_gemini_payload
from instamagic.services.gemini.curation import (
    CurationCandidatePayload,
    CurationResult,
    build_curation_request_body,
)
from instamagic.services.gemini.growth import (
    CommentDraftBatch,
    HashtagSuggestion,
    comment_request_text,
    comment_system_instruction,
    hashtag_request_text,
)


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
        body = build_request_body(context, policy=policy, image_bytes=image_bytes)
        text = self._post(body)
        return CaptionBatch.model_validate_json(text)

    def generate_comment_drafts(
        self,
        context: GeminiContext,
        *,
        target_context: str,
        account_voice: str,
    ) -> CommentDraftBatch:
        if not self._api_key:
            raise NotConfigured("GEMINI_API_KEY is not configured.")
        body = {
            "system_instruction": {"parts": [{"text": comment_system_instruction()}]},
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {
                            "text": comment_request_text(
                                context=context,
                                target_context=target_context,
                                account_voice=account_voice,
                            )
                        }
                    ],
                }
            ],
            "generationConfig": {
                "temperature": 0.7,
                "responseMimeType": "application/json",
                "responseSchema": {
                    "type": "OBJECT",
                    "properties": {
                        "drafts": {
                            "type": "ARRAY",
                            "items": {
                                "type": "OBJECT",
                                "properties": {"text": {"type": "STRING"}},
                                "required": ["text"],
                            },
                        }
                    },
                    "required": ["drafts"],
                },
            },
        }
        assert_safe_gemini_payload(body, policy="text_only")
        return CommentDraftBatch.model_validate_json(self._post(body))

    def suggest_hashtags(self, context: GeminiContext, *, account_voice: str) -> HashtagSuggestion:
        if not self._api_key:
            raise NotConfigured("GEMINI_API_KEY is not configured.")
        body = {
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": hashtag_request_text(context=context, account_voice=account_voice)}],
                }
            ],
            "generationConfig": {
                "temperature": 0.5,
                "responseMimeType": "application/json",
                "responseSchema": {
                    "type": "OBJECT",
                    "properties": {"tags": {"type": "ARRAY", "items": {"type": "STRING"}}},
                    "required": ["tags"],
                },
            },
        }
        assert_safe_gemini_payload(body, policy="text_only")
        return HashtagSuggestion.model_validate_json(self._post(body))

    def curate_batch(
        self,
        candidates: list[CurationCandidatePayload],
        *,
        account_voice: str,
        policy: str,
    ) -> CurationResult:
        """Batch curation always uses text contexts (no library pixels)."""
        del policy
        if not self._api_key:
            raise NotConfigured("GEMINI_API_KEY is not configured.")
        body = build_curation_request_body(candidates, account_voice=account_voice)
        return CurationResult.model_validate_json(self._post(body))

    def _post(self, body: dict[str, object]) -> str:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self._model}:generateContent"
        headers = {"x-goog-api-key": self._api_key}
        try:
            response = httpx.post(url, headers=headers, json=body, timeout=self._timeout)
            if response.status_code == 429 or response.status_code >= 500:
                response = httpx.post(url, headers=headers, json=body, timeout=self._timeout)
        except httpx.HTTPError:
            raise NotConfigured("Gemini request failed.") from None
        if response.status_code >= 400:
            raise NotConfigured("Gemini request failed.")
        return _extract_text(response.json())


def build_request_body(context: GeminiContext, *, policy: str, image_bytes: bytes | None) -> dict[str, object]:
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
    body: dict[str, object] = {
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
    assert_safe_gemini_payload(body, policy=policy)
    return body


def _extract_text(payload: dict) -> str:
    candidates = payload.get("candidates") or []
    if not candidates:
        raise NotConfigured("Gemini returned no candidates.")
    parts = ((candidates[0].get("content") or {}).get("parts")) or []
    for part in parts:
        if "text" in part:
            return str(part["text"])
    raise NotConfigured("Gemini returned no text.")
