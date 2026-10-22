"""Assisted drafting for growth: insightful captions extras + comment drafts.

Comment drafts are never posted to Instagram by this module.
"""

from __future__ import annotations

from typing import Protocol

from instamagic.services.gemini.captions import CaptionDraft
from instamagic.services.gemini.context import BANNED_PHRASES, GeminiContext
from pydantic import BaseModel, Field, field_validator


class CommentDraftText(BaseModel):
    text: str = Field(min_length=1, max_length=500)

    @field_validator("text")
    @classmethod
    def reject_bait(cls, value: str) -> str:
        lowered = value.lower()
        for phrase in BANNED_PHRASES:
            if phrase in lowered:
                raise ValueError("Draft uses engagement bait.")
        return value.strip()


class CommentDraftBatch(BaseModel):
    drafts: list[CommentDraftText] = Field(min_length=1, max_length=6)


class HashtagSuggestion(BaseModel):
    tags: list[str] = Field(min_length=1, max_length=20)

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
        if not cleaned:
            raise ValueError("No valid hashtags.")
        return cleaned[:20]


class GrowthDraftProvider(Protocol):
    def generate_comment_drafts(
        self,
        context: GeminiContext,
        *,
        target_context: str,
        account_voice: str,
    ) -> CommentDraftBatch: ...

    def suggest_hashtags(self, context: GeminiContext, *, account_voice: str) -> HashtagSuggestion: ...


def comment_system_instruction() -> str:
    return (
        "You draft short, insightful Instagram comment replies for the account operator to post manually. "
        "Never ask for follows, tags, or YES comments. Stay specific to craft, light, or mood. "
        "Return JSON with a drafts array of objects with text."
    )


def comment_request_text(*, context: GeminiContext, target_context: str, account_voice: str) -> str:
    return (
        f"Account voice: {account_voice}\n"
        f"Target post or conversation notes: {target_context}\n"
        f"Our photo context (JSON): {context.canonical_json()}\n"
        "Write 3 distinct thoughtful reply drafts the operator can copy. No hashtags in comments."
    )


def hashtag_request_text(*, context: GeminiContext, account_voice: str) -> str:
    return (
        f"Account voice: {account_voice}\n"
        f"Photo context: {context.canonical_json()}\n"
        "Suggest up to 12 relevant photography hashtags (no spam stacks). "
        'Return JSON {"tags":["tag1","tag2"]} without # prefixes.'
    )


class FakeGrowthDraftProvider:
    def generate_comment_drafts(
        self,
        context: GeminiContext,
        *,
        target_context: str,
        account_voice: str,
    ) -> CommentDraftBatch:
        del account_voice
        mood = context.mood or "quiet"
        note = (target_context or "their frame")[:80]
        return CommentDraftBatch(
            drafts=[
                CommentDraftText(text=f"The {mood} light in this holds the same patience as {note}."),
                CommentDraftText(text="Beautiful restraint in the midtones — the shadow does real work here."),
                CommentDraftText(text="That edge of soft contrast is doing more storytelling than a harder grade would."),
            ]
        )

    def suggest_hashtags(self, context: GeminiContext, *, account_voice: str) -> HashtagSuggestion:
        del account_voice
        base = ["fineartphotography", "portraitmood", "availablelight", "filmlook", "quietphotography"]
        if context.mood and context.mood != "unspecified":
            base.append(context.mood.replace(" ", "")[:32])
        return HashtagSuggestion(tags=base)


def append_voice_to_context(context: GeminiContext, *, account_voice: str, research_notes: str = "") -> GeminiContext:
    account = dict(context.account_context)
    account["voice"] = account_voice[:400]
    if research_notes.strip():
        account["research_notes"] = research_notes.strip()[:800]
    return context.model_copy(update={"account_context": account})


def insightful_caption_addon(account_voice: str) -> str:
    return (
        f" Account voice to honor: {account_voice}. "
        "Prefer insightful, original observations about light and craft over generic praise."
    )


# Re-export for typing convenience in routes.
__all__ = [
    "CaptionDraft",
    "CommentDraftBatch",
    "CommentDraftText",
    "FakeGrowthDraftProvider",
    "GrowthDraftProvider",
    "HashtagSuggestion",
    "append_voice_to_context",
    "comment_request_text",
    "comment_system_instruction",
    "hashtag_request_text",
    "insightful_caption_addon",
]
