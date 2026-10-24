from __future__ import annotations

import hashlib
import json
from typing import Any

from instamagic.errors import GeminiBlocked
from instamagic.services.recommendations.gates import gemini_gate, prefetch_gate
from pydantic import BaseModel, Field

BANNED_PHRASES = (
    "comment yes",
    "tag 5 friends",
    "tag five friends",
    "follow for follow",
    "follow4follow",
)

# Policies that may attach EXIF-stripped derivative JPEG bytes (never originals / paths).
# preview / selected are aliases of safe_previews: curation/captions for gated frames only.
GEMINI_PIXEL_POLICIES = frozenset({"safe_previews", "preview", "selected"})
GEMINI_IMAGE_POLICIES = frozenset({"text_only", "manual_only", *GEMINI_PIXEL_POLICIES})


def gemini_allows_pixels(policy: str) -> bool:
    """True when Gemini may receive derivative preview/thumb bytes (not library originals)."""
    return policy in GEMINI_PIXEL_POLICIES


def resolve_curation_send_policy(*, env_policy: str, include_images: bool | None) -> str:
    """Env is a ceiling; per-run include_images chooses text_only vs preview when allowed.

    - manual_only: unchanged (caller blocks curation)
    - text_only (or other non-pixel): ceiling forbids bytes → always text_only
    - preview / safe_previews / selected: toggle allowed; default ON when include_images is None
    """
    if env_policy == "manual_only":
        return "manual_only"
    if not gemini_allows_pixels(env_policy):
        return "text_only"
    if include_images is False:
        return "text_only"
    return "preview"


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
    has_approved_blur: bool = False,
    do_not_post_blur_unlocked: bool = False,
) -> GeminiContext:
    gate = gemini_gate(
        attested=attested,
        human_rating=human_rating,
        model_state=model_state,
        needs_crop=needs_crop,
        has_approved_crop=has_approved_crop,
        has_approved_covering_blur=has_approved_covering_blur,
        has_approved_blur=has_approved_blur,
        do_not_post_blur_unlocked=do_not_post_blur_unlocked,
        operator_confirmed=operator_confirmed,
        policy=policy,
    )
    if not gate.allowed:
        raise GeminiBlocked(" ".join(gate.reasons))
    return _assemble_context(
        model_state=model_state,
        has_approved_covering_blur=has_approved_covering_blur,
        operator_confirmed=operator_confirmed,
        content_tags=content_tags,
        composition=composition,
        mood=mood,
        safe_description=safe_description,
        crop=crop,
    )


def build_prefetch_context(
    *,
    attested: bool,
    human_rating: str | None,
    model_state: str | None,
    classifier_clean: bool,
    has_any_blur: bool,
    skip_reason: str | None,
    policy: str,
    operator_confirmed: bool,
    content_tags: list[str],
    composition: str,
    mood: str,
    safe_description: str,
) -> GeminiContext:
    """Context for background prefetch of an unrated, classifier-clean REVIEW photo (``prefetch_gate``)."""
    gate = prefetch_gate(
        attested=attested,
        human_rating=human_rating,
        model_state=model_state,
        classifier_clean=classifier_clean,
        has_any_blur=has_any_blur,
        skip_reason=skip_reason,
        policy=policy,
        operator_confirmed=operator_confirmed,
    )
    if not gate.allowed:
        raise GeminiBlocked(" ".join(gate.reasons))
    return _assemble_context(
        model_state=model_state,
        has_approved_covering_blur=False,
        operator_confirmed=operator_confirmed,
        content_tags=content_tags,
        composition=composition,
        mood=mood,
        safe_description=safe_description,
        crop=None,
    )


def _assemble_context(
    *,
    model_state: str | None,
    has_approved_covering_blur: bool,
    operator_confirmed: bool,
    content_tags: list[str],
    composition: str,
    mood: str,
    safe_description: str,
    crop: dict[str, Any] | None,
) -> GeminiContext:
    description = _sanitize_prompt_text(safe_description, max_len=400)
    crop_payload = crop
    if model_state == "SAFE_AFTER_CROP":
        if crop is None and not has_approved_covering_blur:
            raise GeminiBlocked("SAFE_AFTER_CROP requires an approved crop or covering blur.")
        if crop is not None:
            description = (
                f"Describe only the approved {crop.get('aspect_ratio', 'crop')} crop. "
                "Do not infer what was excluded from the frame. "
                + description
            )
        else:
            description = (
                "Describe the photograph with approved covering blur applied. "
                "Do not infer what was covered. "
                + description
            )
            crop_payload = None
    elif model_state == "DO_NOT_POST":
        # Only reachable when the gate accepted a verified blur unlock with approved covers.
        description = (
            "Describe the photograph with approved covering blur applied. "
            "Do not infer what was covered. "
            + description
        )
        crop_payload = None
    return GeminiContext(
        content_tags=[_sanitize_prompt_text(tag, max_len=64) for tag in content_tags[:24]],
        composition=_sanitize_prompt_text(composition, max_len=120),
        mood=_sanitize_prompt_text(mood, max_len=120),
        safe_description=description,
        crop=crop_payload,
        historical_performance={"available": False},
        account_context={"goal": "organic audience growth from an existing photography archive"},
        recent_posts=[],
        operator_confirmed=operator_confirmed,
    )


def _sanitize_prompt_text(value: str, *, max_len: int) -> str:
    cleaned = "".join(ch for ch in value if ch.isprintable() and ch not in "\r\n\t")
    return cleaned.strip()[:max_len]


def assert_safe_gemini_payload(payload: dict[str, Any], *, policy: str) -> None:
    """Fail closed if a Gemini request references library paths or leaks originals under text_only."""
    blob = json.dumps(payload)
    lowered = blob.lower()
    if "originals" in lowered or "/photos/" in lowered or "\\photos\\" in lowered:
        raise GeminiBlocked("Gemini payload included a forbidden reference.")
    if "file://" in lowered or "file:\\" in lowered:
        raise GeminiBlocked("Gemini payload included a forbidden file URI.")
    if not gemini_allows_pixels(policy) and "inline_data" in lowered:
        raise GeminiBlocked("This GEMINI_IMAGE_POLICY forbids image bytes in Gemini requests.")


# Back-compat alias used by older tests / call sites.
def assert_no_original_reference(payload: dict[str, Any]) -> None:
    assert_safe_gemini_payload(payload, policy="text_only")
