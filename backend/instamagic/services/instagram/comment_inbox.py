"""Poll comments on own media; replies are human-gated only."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

from instamagic.config import Settings
from instamagic.errors import NotAllowed, NotConfigured
from instamagic.models import InstagramInboxComment, InstagramPost
from instamagic.services.gemini.context import GeminiContext
from instamagic.services.gemini.growth import GrowthDraftProvider, append_voice_to_context
from instamagic.services.instagram.client import InstagramClient
from instamagic.services.instagram.insights_sync import refresh_token_scopes
from instamagic.services.instagram.permissions import (
    graph_permission_missing_detail,
    skip_comments_for_known_scopes,
)
from instamagic.services.instagram.store import get_connection
from sqlalchemy import select
from sqlalchemy.orm import Session

_SYNC_INTERVAL = timedelta(hours=2)
_SPANISH_HINT = re.compile(r"[áéíóúñ¿¡]", re.IGNORECASE)


def _detect_language(text: str) -> str:
    return "es" if _SPANISH_HINT.search(text) else "en"


def sync_inbox_comments(
    session: Session, settings: Settings, client: InstagramClient, *, force: bool = False
) -> dict[str, object]:
    if not client.configured():
        return {"ok": False, "error": "not_configured", "synced": 0}
    row = get_connection(session)
    if not force and row and row.insights_last_sync_at:
        # Reuse insights timestamp slot is wrong - use separate - skip for MVP use force from worker always
        pass
    fetched_scopes = refresh_token_scopes(session, client)
    if skip_comments_for_known_scopes(fetched_scopes):
        return {
            "ok": False,
            "error": "comments_permission_missing",
            "reconnect_hint": "Reconnect to grant instagram_manage_comments.",
            "synced": 0,
        }
    list_fn = getattr(client, "list_media_comments", None)
    if not callable(list_fn):
        return {"ok": False, "error": "comments_unavailable", "synced": 0}
    posts = session.scalars(
        select(InstagramPost)
        .where(InstagramPost.ig_media_id.is_not(None))
        .order_by(InstagramPost.published_at.desc())
        .limit(20)
    ).all()
    synced = 0
    for post in posts:
        if not post.ig_media_id:
            continue
        try:
            rows = list_fn(post.ig_media_id)
        except NotConfigured as exc:
            if graph_permission_missing_detail(exc.detail):
                return {
                    "ok": False,
                    "error": "comments_permission_missing",
                    "reconnect_hint": "Reconnect to grant instagram_manage_comments.",
                    "synced": 0,
                }
            continue
        for item in rows:
            ig_id = str(item.get("id") or "")
            if not ig_id:
                continue
            existing = session.scalar(
                select(InstagramInboxComment).where(InstagramInboxComment.ig_comment_id == ig_id)
            )
            if existing is not None:
                continue
            commented_at = None
            ts = item.get("timestamp")
            if isinstance(ts, str):
                try:
                    commented_at = datetime.fromisoformat(ts.replace("+0000", "+00:00"))
                except ValueError:
                    commented_at = None
            session.add(
                InstagramInboxComment(
                    ig_comment_id=ig_id,
                    ig_media_id=post.ig_media_id,
                    post_id=post.id,
                    author_username=str(item.get("username") or "") or None,
                    text=str(item.get("text") or ""),
                    commented_at=commented_at,
                    status="open",
                )
            )
            synced += 1
    session.commit()
    return {"ok": True, "synced": synced}


def suggest_reply(
    session: Session,
    growth: GrowthDraftProvider,
    comment: InstagramInboxComment,
    *,
    account_voice: str,
    post_caption: str | None,
) -> str:
    lang = _detect_language(comment.text)
    target = (
        f"Commenter wrote ({lang}): {comment.text[:500]}\n"
        f"Reply in the same language ({lang}). Post caption context: {(post_caption or '')[:300]}"
    )
    context = GeminiContext(
        attested=True,
        human_rating="safe",
        model_state="SAFE",
        needs_crop=False,
        has_approved_crop=False,
        policy="text_only",
        operator_confirmed=True,
        content_tags=[],
        composition="unspecified",
        mood="unspecified",
        safe_description="Replying to a comment on our Instagram post.",
        account_context={},
    )
    context = append_voice_to_context(context, account_voice=account_voice)
    batch = growth.generate_comment_drafts(
        context, target_context=target, account_voice=account_voice
    )
    reply = batch.drafts[0].text
    comment.suggested_reply = reply
    session.commit()
    return reply


def post_reply(
    session: Session, client: InstagramClient, comment: InstagramInboxComment, message: str
) -> None:
    if not message.strip():
        raise NotAllowed("Reply text is empty.")
    post_fn = getattr(client, "post_comment_reply", None)
    if not callable(post_fn):
        raise NotConfigured("Comment replies are not available.")
    post_fn(comment.ig_comment_id, message.strip())
    comment.status = "replied"
    comment.replied_at = datetime.now(UTC)
    session.commit()
