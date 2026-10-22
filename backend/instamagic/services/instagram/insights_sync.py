"""Periodic Instagram insights sync — rate-limit friendly, never logs tokens."""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta
from uuid import UUID

from instamagic.config import Settings
from instamagic.errors import NotConfigured
from instamagic.logging_config import get_logger
from instamagic.models import (
    InstagramAccountInsightSnapshot,
    InstagramConnection,
    InstagramMetrics,
    InstagramPost,
)
from instamagic.services.instagram.client import InstagramClient, MediaSummary
from instamagic.services.instagram.permissions import insights_permission_ok, parse_scopes_json
from instamagic.services.instagram.store import get_connection, resolve_credentials
from sqlalchemy import select
from sqlalchemy.orm import Session

log = get_logger("instagram.insights")

SYNC_MIN_INTERVAL = timedelta(hours=6)
MEDIA_SLEEP_SECONDS = 0.35


class InsightsPermissionMissing(Exception):
    """Raised when the Page token lacks instagram_manage_insights."""


def granted_scopes_from_connection(session: Session) -> set[str]:
    row = get_connection(session)
    if row is None:
        return set()
    return parse_scopes_json(row.token_scopes_json)


def refresh_token_scopes(session: Session, client: InstagramClient) -> set[str]:
    if not client.configured():
        return set()
    fetch = getattr(client, "fetch_granted_permissions", None)
    if not callable(fetch):
        return granted_scopes_from_connection(session)
    try:
        scopes = fetch()
    except NotConfigured:
        return granted_scopes_from_connection(session)
    row = get_connection(session)
    if row is not None:
        row.token_scopes_json = json.dumps(sorted(scopes))
        session.commit()
    return scopes


def should_run_sync(row: InstagramConnection | None, *, force: bool = False) -> bool:
    if force:
        return True
    if row is None or row.insights_last_sync_at is None:
        return True
    return datetime.now(UTC) - row.insights_last_sync_at >= SYNC_MIN_INTERVAL


def _followers_at_post(session: Session, published_at: datetime | None) -> int | None:
    if published_at is None:
        return None
    snap = session.scalar(
        select(InstagramAccountInsightSnapshot)
        .where(InstagramAccountInsightSnapshot.captured_at <= published_at)
        .order_by(InstagramAccountInsightSnapshot.captured_at.desc())
        .limit(1)
    )
    if snap is not None and snap.follower_count:
        return snap.follower_count
    latest = session.scalar(
        select(InstagramAccountInsightSnapshot)
        .order_by(InstagramAccountInsightSnapshot.captured_at.desc())
        .limit(1)
    )
    return latest.follower_count if latest else None


def _engagement_rate(metrics: InstagramMetrics, followers: int | None) -> float | None:
    if not followers or followers <= 0:
        return None
    interactions = metrics.total_interactions
    if interactions is None:
        parts = [metrics.likes, metrics.comments, metrics.saves, metrics.shares]
        interactions = sum(part or 0 for part in parts)
    if interactions <= 0:
        return 0.0
    return min(1.0, interactions / float(followers))


def _upsert_post_for_media(session: Session, media: MediaSummary) -> InstagramPost | None:
    if not media.id:
        return None
    post = session.scalar(select(InstagramPost).where(InstagramPost.ig_media_id == media.id))
    if post is None:
        published = None
        if media.timestamp:
            try:
                published = datetime.fromisoformat(media.timestamp.replace("+0000", "+00:00"))
            except ValueError:
                published = None
        post = InstagramPost(
            ig_media_id=media.id,
            caption=media.caption,
            permalink=media.permalink,
            media_type=media.media_type,
            published_at=published,
            status="published",
        )
        session.add(post)
        session.flush()
    return post


def sync_instagram_insights(
    session: Session,
    settings: Settings,
    client: InstagramClient,
    *,
    force: bool = False,
) -> dict[str, object]:
    creds = resolve_credentials(settings, session)
    if creds is None and settings.instagram_provider != "fake":
        return {"ok": False, "error": "not_configured", "synced_media": 0}
    if not client.configured():
        return {"ok": False, "error": "not_configured", "synced_media": 0}

    row = get_connection(session)
    if not should_run_sync(row, force=force):
        return {"ok": True, "skipped": True, "reason": "cached", "synced_media": 0}

    scopes = refresh_token_scopes(session, client)
    if not insights_permission_ok(scopes):
        return {
            "ok": False,
            "error": "insights_permission_missing",
            "reconnect_hint": "Reconnect via Meta OAuth to grant instagram_manage_insights.",
            "synced_media": 0,
        }

    account_values = client.fetch_account_insight_snapshot()
    session.add(
        InstagramAccountInsightSnapshot(
            follower_count=account_values.get("follower_count"),
            reach=account_values.get("reach"),
            profile_views=account_values.get("profile_views"),
            impressions=account_values.get("impressions"),
            accounts_engaged=account_values.get("accounts_engaged"),
        )
    )

    synced = 0
    media_rows = client.list_recent_media(limit=40)
    known_ids = {
        str(post.ig_media_id)
        for post in session.scalars(
            select(InstagramPost).where(InstagramPost.ig_media_id.is_not(None))
        ).all()
        if post.ig_media_id
    }
    targets: list[MediaSummary] = []
    for media in media_rows:
        if media.id in known_ids or len(targets) < 25:
            targets.append(media)
    for media in targets:
        post = _upsert_post_for_media(session, media)
        if post is None:
            continue
        try:
            insights = client.fetch_media_insights(media.id, media_type=media.media_type)
        except NotConfigured as exc:
            log.info("media_insights_skipped", media_id=media.id, detail=str(exc.detail)[:120])
            time.sleep(MEDIA_SLEEP_SECONDS)
            continue
        followers = _followers_at_post(session, post.published_at)
        metric = InstagramMetrics(
            post_id=post.id,
            likes=insights.likes if insights.likes is not None else media.like_count,
            comments=insights.comments if insights.comments is not None else media.comments_count,
            reach=insights.reach,
            impressions=insights.impressions,
            shares=insights.shares,
            saves=insights.saved,
            views=insights.views,
            total_interactions=insights.total_interactions,
        )
        metric.engagement_rate = _engagement_rate(metric, followers)
        if metric.reach and followers:
            metric.reach_to_followers_ratio = min(10.0, metric.reach / float(followers))
        session.add(metric)
        synced += 1
        time.sleep(MEDIA_SLEEP_SECONDS)

    if row is not None:
        row.insights_last_sync_at = datetime.now(UTC)
    session.commit()
    return {"ok": True, "synced_media": synced, "skipped": False}


def link_post_image(session: Session, post_id: UUID, image_id: UUID) -> None:
    post = session.get(InstagramPost, post_id)
    if post is None:
        return
    if post.image_id is None:
        post.image_id = image_id
        session.commit()
