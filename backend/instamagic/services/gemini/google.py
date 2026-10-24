from __future__ import annotations

import httpx
from instamagic.errors import (
    GeminiBlocked,
    GeminiEmpty,
    GeminiRateLimited,
    GeminiTruncated,
    NotConfigured,
)
from instamagic.logging_config import get_logger
from instamagic.services.gemini.captions import (
    CaptionBatch,
    caption_request_text,
    system_instruction,
)
from instamagic.services.gemini.context import (
    GeminiContext,
    assert_safe_gemini_payload,
    gemini_allows_pixels,
)
from instamagic.services.gemini.curation import (
    CurationCandidatePayload,
    CurationResult,
    build_curation_request_body,
    curation_max_output_tokens,
)
from instamagic.services.gemini.growth import (
    CommentDraftBatch,
    EngageCommentBatch,
    EngageTargetText,
    HashtagSuggestion,
    comment_request_text,
    comment_system_instruction,
    engage_request_text,
    engage_system_instruction,
    hashtag_request_text,
)
from instamagic.services.gemini.labeling import (
    ImageLabelResult,
    build_label_request_body,
)

log = get_logger("gemini.google")

_FINISH_BLOCK_REASONS = frozenset(
    {"SAFETY", "PROHIBITED_CONTENT", "RECITATION", "BLOCKLIST", "SPII", "OTHER"}
)


class GoogleGeminiProvider:
    def __init__(self, api_key: str, model: str, timeout: float = 90.0) -> None:
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

    def label_image(
        self,
        context: GeminiContext,
        *,
        policy: str,
        image_bytes: bytes | None,
        thinking_budget: int,
        max_output_tokens: int,
    ) -> ImageLabelResult:
        if not self._api_key:
            raise NotConfigured("GEMINI_API_KEY is not configured.")
        body = build_label_request_body(
            context,
            policy=policy,
            image_bytes=image_bytes,
            thinking_budget=thinking_budget,
            max_output_tokens=max_output_tokens,
        )
        return ImageLabelResult.model_validate_json(self._post(body))

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
                    "parts": [
                        {"text": hashtag_request_text(context=context, account_voice=account_voice)}
                    ],
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

    def draft_engage_comments(
        self, targets: list[EngageTargetText], *, account_voice: str
    ) -> EngageCommentBatch:
        """Text-only: captions of other accounts' posts in, short manual-comment suggestions out."""
        if not self._api_key:
            raise NotConfigured("GEMINI_API_KEY is not configured.")
        body = {
            "system_instruction": {"parts": [{"text": engage_system_instruction()}]},
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {"text": engage_request_text(targets=targets, account_voice=account_voice)}
                    ],
                }
            ],
            "generationConfig": {
                "temperature": 0.8,
                "maxOutputTokens": 4096,
                "thinkingConfig": {"thinkingBudget": 256},
                "responseMimeType": "application/json",
                "responseSchema": {
                    "type": "OBJECT",
                    "properties": {
                        "comments": {
                            "type": "ARRAY",
                            "items": {
                                "type": "OBJECT",
                                "properties": {
                                    "id": {"type": "STRING"},
                                    "text": {"type": "STRING"},
                                },
                                "required": ["id", "text"],
                            },
                        }
                    },
                    "required": ["comments"],
                },
            },
        }
        assert_safe_gemini_payload(body, policy="text_only")
        return EngageCommentBatch.lenient(self._post(body))

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
        """Rank shortlisted frames. Pixels only when policy allows derivative JPEGs."""
        if not self._api_key:
            raise NotConfigured("GEMINI_API_KEY is not configured.")
        pixels = image_bytes_by_id if gemini_allows_pixels(policy) else None
        body = build_curation_request_body(
            candidates,
            account_voice=account_voice,
            policy=policy,
            image_bytes_by_id=pixels,
            thinking_budget=thinking_budget,
            max_output_tokens=max_output_tokens or curation_max_output_tokens(len(candidates)),
        )
        return CurationResult.model_validate_json(self._post(body))

    def _post(self, body: dict[str, object]) -> str:
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/{self._model}:generateContent"
        )
        headers = {"x-goog-api-key": self._api_key}
        try:
            response = httpx.post(url, headers=headers, json=body, timeout=self._timeout)
            if response.status_code == 429 or response.status_code >= 500:
                response = httpx.post(url, headers=headers, json=body, timeout=self._timeout)
        except httpx.HTTPError:
            raise NotConfigured("Gemini request failed.") from None
        if response.status_code == 429:
            raise GeminiRateLimited("Gemini rate limit reached.")
        if response.status_code >= 400:
            raise NotConfigured("Gemini request failed.")
        return _extract_text(response.json())


def build_request_body(
    context: GeminiContext, *, policy: str, image_bytes: bytes | None
) -> dict[str, object]:
    pixels = image_bytes if gemini_allows_pixels(policy) else None
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
            "maxOutputTokens": 2048,
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
    usage = payload.get("usageMetadata")
    if usage:
        log.info(
            "gemini_usage",
            prompt_tokens=usage.get("promptTokenCount"),
            candidates_tokens=usage.get("candidatesTokenCount"),
            total_tokens=usage.get("totalTokenCount"),
            thoughts_tokens=usage.get("thoughtsTokenCount"),
        )
    feedback = payload.get("promptFeedback") or {}
    block_reason = feedback.get("blockReason")
    if block_reason:
        reason = str(block_reason)
        log.info("gemini_prompt_blocked", block_reason=reason)
        raise GeminiBlocked(f"Gemini blocked: {reason}", reason=reason)

    candidates = payload.get("candidates") or []
    if not candidates:
        log.info("gemini_empty_candidates")
        raise GeminiEmpty("Gemini returned no candidates.")

    candidate = candidates[0]
    finish = candidate.get("finishReason")
    if finish:
        finish_s = str(finish)
        if finish_s == "MAX_TOKENS":
            log.info("gemini_truncated", finish_reason=finish_s)
            raise GeminiTruncated("Gemini output truncated (MAX_TOKENS).")
        if finish_s in _FINISH_BLOCK_REASONS:
            log.info("gemini_finish_blocked", finish_reason=finish_s)
            raise GeminiBlocked(f"Gemini blocked: {finish_s}", reason=finish_s)

    parts = ((candidate.get("content") or {}).get("parts")) or []
    for part in parts:
        if "text" in part:
            text = str(part["text"]).strip()
            if text:
                return text
    log.info("gemini_empty_text", finish_reason=finish)
    raise GeminiEmpty("Gemini returned no text.")
