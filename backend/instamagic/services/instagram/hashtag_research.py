"""Hashtag search + top/recent media with 30 unique hashtags / 7 days budget."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta

from instamagic.errors import NotAllowed, NotConfigured
from instamagic.models import HashtagResearchCache, HashtagResearchEvent
from instamagic.services.instagram.client import InstagramClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

HASHTAG_BUDGET_DAYS = 7
HASHTAG_BUDGET_LIMIT = 30
_HASHTAG_RE = re.compile(r"^[a-z0-9_]{1,64}$")


class HashtagResearchUnavailable(NotConfigured):
    """Instagram Public Content Access or permissions missing."""


def normalize_hashtag(raw: str) -> str:
    tag = raw.strip().lstrip("#").lower()
    if not tag or not _HASHTAG_RE.match(tag):
        raise NotAllowed("Hashtag must be 1–64 characters: letters, numbers, underscore.")
    return tag


def budget_status(session: Session) -> dict[str, int]:
    since = datetime.now(UTC) - timedelta(days=HASHTAG_BUDGET_DAYS)
    used = session.scalar(
        select(func.count(func.distinct(HashtagResearchEvent.hashtag))).where(
            HashtagResearchEvent.created_at >= since,
            HashtagResearchEvent.action == "search",
        )
    )
    used_int = int(used or 0)
    return {
        "used_unique_hashtags_7d": used_int,
        "remaining_unique_hashtags_7d": max(0, HASHTAG_BUDGET_LIMIT - used_int),
        "limit_unique_hashtags_7d": HASHTAG_BUDGET_LIMIT,
    }


def _record_search(session: Session, hashtag: str, hashtag_id: str | None) -> None:
    session.add(HashtagResearchEvent(hashtag=hashtag, hashtag_id=hashtag_id, action="search"))
    session.flush()


def _already_searched_recently(session: Session, hashtag: str) -> bool:
    since = datetime.now(UTC) - timedelta(days=HASHTAG_BUDGET_DAYS)
    return (
        session.scalar(
            select(HashtagResearchEvent.id)
            .where(
                HashtagResearchEvent.hashtag == hashtag,
                HashtagResearchEvent.action == "search",
                HashtagResearchEvent.created_at >= since,
            )
            .limit(1)
        )
        is not None
    )


def research_hashtag(session: Session, client: InstagramClient, raw_tag: str, *, force: bool = False) -> dict[str, object]:
    if not client.configured():
        raise NotConfigured("Instagram is not configured.")
    tag = normalize_hashtag(raw_tag)
    cached = session.get(HashtagResearchCache, tag)
    if cached and not force and cached.fetched_at >= datetime.now(UTC) - timedelta(hours=24):
        return _cache_out(cached, budget_status(session))

    budget = budget_status(session)
    if not force and not _already_searched_recently(session, tag) and budget["remaining_unique_hashtags_7d"] <= 0:
        raise NotAllowed(
            "Hashtag research budget exhausted (30 unique hashtags per 7 days). "
            "Reuse cached tags or wait for the rolling window."
        )

    search = getattr(client, "search_hashtag", None)
    top_media_fn = getattr(client, "fetch_hashtag_top_media", None)
    recent_media_fn = getattr(client, "fetch_hashtag_recent_media", None)
    if not callable(search):
        raise HashtagResearchUnavailable(
            "Hashtag search is not available. Instagram Public Content Access may be required."
        )

    try:
        hashtag_id = search(tag)
    except NotConfigured as exc:
        raise HashtagResearchUnavailable(str(exc.detail)) from exc

    # Meta counts the unique hashtag as soon as the search succeeds, even if an edge fails.
    if not _already_searched_recently(session, tag):
        _record_search(session, tag, hashtag_id)
        session.commit()

    edge_rows: dict[str, list[dict]] = {}
    edge_errors: list[str] = []
    for name, fn in (("top", top_media_fn), ("recent", recent_media_fn)):
        if not callable(fn):
            edge_rows[name] = []
            continue
        try:
            edge_rows[name] = fn(hashtag_id)
        except NotConfigured as exc:
            edge_rows[name] = []
            edge_errors.append(str(exc.detail))
    if edge_errors and not (edge_rows["top"] or edge_rows["recent"]):
        raise HashtagResearchUnavailable(edge_errors[0])
    top_rows = edge_rows["top"]
    recent_rows = edge_rows["recent"]

    payload_top = json.dumps(top_rows, ensure_ascii=False)
    payload_recent = json.dumps(recent_rows, ensure_ascii=False)
    if cached is None:
        cached = HashtagResearchCache(
            hashtag=tag,
            hashtag_id=hashtag_id,
            top_media_json=payload_top,
            recent_media_json=payload_recent,
        )
        session.add(cached)
    else:
        cached.hashtag_id = hashtag_id
        cached.top_media_json = payload_top
        cached.recent_media_json = payload_recent
        cached.fetched_at = datetime.now(UTC)
    session.commit()
    session.refresh(cached)
    return _cache_out(cached, budget_status(session))


def _cache_out(row: HashtagResearchCache, budget: dict[str, int]) -> dict[str, object]:
    return {
        "hashtag": row.hashtag,
        "hashtag_id": row.hashtag_id,
        "top_media": json.loads(row.top_media_json or "[]"),
        "recent_media": json.loads(row.recent_media_json or "[]"),
        "fetched_at": row.fetched_at.isoformat() if row.fetched_at else None,
        "budget": budget,
    }


def suggest_tags_from_research(session: Session, gemini_tags: list[str]) -> list[str]:
    """Merge Gemini tags with cached research hashtags that appear in top media captions."""
    suggestions = [t.lower() for t in gemini_tags]
    for tag in list(suggestions):
        cached = session.get(HashtagResearchCache, tag)
        if cached is None:
            continue
        for blob in (cached.top_media_json, cached.recent_media_json):
            if not blob:
                continue
            try:
                media = json.loads(blob)
            except json.JSONDecodeError:
                continue
            if isinstance(media, list) and media:
                if tag not in suggestions:
                    suggestions.append(tag)
    from instamagic.services.posting.hashtags import clean_tags

    return clean_tags(suggestions)[:15]
