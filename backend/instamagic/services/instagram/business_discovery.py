"""Business discovery for related accounts (cached daily)."""

from __future__ import annotations

import json
import re
from collections import Counter
from datetime import UTC, datetime, timedelta

from instamagic.errors import NotConfigured, NotFound
from instamagic.models import RelatedAccount, RelatedAccountDiscovery
from instamagic.services.instagram.client import InstagramClient
from sqlalchemy.orm import Session

_HANDLE_RE = re.compile(r"^[a-z0-9._]{1,30}$")
_HASHTAG_IN_CAPTION = re.compile(r"#([a-z0-9_]+)")


def normalize_handle(raw: str) -> str:
    handle = raw.strip().lstrip("@").lower()
    if not handle or not _HANDLE_RE.match(handle):
        raise NotFound("Instagram handle is not valid.")
    return handle


def sync_related_account_discovery(
    session: Session,
    client: InstagramClient,
    account: RelatedAccount,
    *,
    force: bool = False,
) -> dict[str, object]:
    if not client.configured():
        raise NotConfigured("Instagram is not configured.")
    handle = normalize_handle(account.handle)
    row = session.get(RelatedAccountDiscovery, account.id)
    if row and not force and row.fetched_at >= datetime.now(UTC) - timedelta(days=1):
        return discovery_out(row, handle)

    fetch = getattr(client, "fetch_business_discovery", None)
    if not callable(fetch):
        raise NotConfigured("Business discovery is not available on this client.")
    try:
        payload = fetch(handle)
    except NotConfigured as exc:
        raise NotConfigured(str(exc.detail)) from exc

    media = payload.get("media") or []
    if isinstance(media, dict):
        media = media.get("data") or []
    hashtags: Counter[str] = Counter()
    likes: list[int] = []
    timestamps: list[datetime] = []
    for item in media if isinstance(media, list) else []:
        if not isinstance(item, dict):
            continue
        caption = str(item.get("caption") or "")
        hashtags.update(_HASHTAG_IN_CAPTION.findall(caption.lower()))
        like = item.get("like_count")
        if isinstance(like, int):
            likes.append(like)
        ts = item.get("timestamp")
        if isinstance(ts, str):
            try:
                timestamps.append(datetime.fromisoformat(ts.replace("+0000", "+00:00")))
            except ValueError:
                pass

    posts_per_week = None
    if len(timestamps) >= 2:
        timestamps.sort()
        span_days = max(1.0, (timestamps[-1] - timestamps[0]).total_seconds() / 86400.0)
        posts_per_week = len(timestamps) / span_days * 7.0

    common = [tag for tag, _ in hashtags.most_common(12)]
    media_json = json.dumps(media[:25], ensure_ascii=False)
    common_json = json.dumps(common, ensure_ascii=False)
    if row is None:
        row = RelatedAccountDiscovery(related_account_id=account.id)
        session.add(row)
    row.followers_count = _int(payload.get("followers_count"))
    row.media_count = _int(payload.get("media_count"))
    row.media_json = media_json
    row.common_hashtags_json = common_json
    row.avg_likes = sum(likes) / len(likes) if likes else None
    row.posts_per_week = posts_per_week
    row.fetched_at = datetime.now(UTC)
    session.commit()
    session.refresh(row)
    return discovery_out(row, handle)


def discovery_out(row: RelatedAccountDiscovery, handle: str) -> dict[str, object]:
    return {
        "handle": handle,
        "followers_count": row.followers_count,
        "media_count": row.media_count,
        "avg_likes": row.avg_likes,
        "posts_per_week": row.posts_per_week,
        "common_hashtags": json.loads(row.common_hashtags_json or "[]"),
        "media": json.loads(row.media_json or "[]"),
        "fetched_at": row.fetched_at.isoformat() if row.fetched_at else None,
    }


def _int(value: object) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
