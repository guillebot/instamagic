"""Read-only operator dashboard snapshot (GET /api/status).

One cached payload that aggregates health, scan progress, photo counts, enrichment,
queue/schedule, publish history, Instagram connection, sync times, Engage and workers.
Never returns tokens, secrets or raw credentials. Every section is computed
independently so one failure never blanks the whole page.
"""

from __future__ import annotations

import re
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from instamagic.config import Settings
from instamagic.logging_config import get_logger
from instamagic.models import (
    BlurCover,
    Caption,
    CurationPick,
    EngageRun,
    EngageSettings,
    EngageTarget,
    Image,
    ImageDuplicate,
    InstagramAccountInsightSnapshot,
    InstagramInboxComment,
    InstagramPost,
    ProcessingJob,
    PublishAttempt,
    PublishingQueueItem,
    QueueState,
)
from sqlalchemy import func, select
from sqlalchemy.orm import Session

log = get_logger("app_status")

CACHE_SECONDS = 15
_cache_lock = threading.Lock()
_cache: tuple[float, dict[str, Any]] | None = None

# Sections too heavy to recompute on every refresh (enrichment_status loads every
# eligible image). Served from a longer cache; once stale, the previous value is
# returned immediately and refreshed in a background thread.
SLOW_SECTION_TTL: dict[str, int] = {"enrichment": 300}
_section_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_refreshing: set[str] = set()

_UNIQUENESS_RE = re.compile(r"(\d+)\s*/\s*(\d+)")
_SECRET_RE = re.compile(r"(access_token|token|secret|code|signature)=([^&\s\"']+)", re.IGNORECASE)


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()


def _scrub(text: str | None, limit: int = 240) -> str | None:
    if not text:
        return None
    return _SECRET_RE.sub(lambda m: f"{m.group(1)}=***", " ".join(text.split()))[:limit]


def _count(session: Session, stmt) -> int:
    return int(session.scalar(stmt) or 0)


def _grouped(session: Session, column, *where) -> dict[str, int]:
    stmt = select(column, func.count()).group_by(column)
    for clause in where:
        stmt = stmt.where(clause)
    return {("none" if key is None else str(key)): int(n) for key, n in session.execute(stmt).all()}


def _redis(settings: Settings):
    if settings.pipeline_mode == "inline":
        return None
    try:
        from redis import Redis

        connection = Redis.from_url(settings.redis_url, socket_connect_timeout=1, socket_timeout=2)
        connection.ping()
        return connection
    except Exception:
        return None


def _float_key(redis, key: str) -> float | None:
    if redis is None:
        return None
    try:
        raw = redis.get(key)
        return float(raw) if raw else None
    except Exception:
        return None


def _epoch_iso(value: float | None) -> str | None:
    return datetime.fromtimestamp(value, UTC).isoformat() if value else None


# ---------------------------------------------------------------- sections


def _section_health(settings: Settings, session: Session, redis) -> dict[str, Any]:
    from instamagic.services.health_status import build_health_status

    return build_health_status(settings)


def scan_progress(job: ProcessingJob | None, expected_total: int | None, now: datetime) -> dict[str, Any] | None:
    if job is None:
        return None
    started = job.started_at
    if started is not None and started.tzinfo is None:
        started = started.replace(tzinfo=UTC)
    elapsed = (now - started).total_seconds() if started else None
    total = job.files_total or expected_total
    seen = int(job.files_seen or 0)
    rate = (seen / elapsed) if elapsed and elapsed > 0 and seen else None
    eta_seconds: float | None = None
    stage = job.current_stage
    uniqueness: dict[str, int] | None = None
    if stage == "uniqueness":
        match = _UNIQUENESS_RE.search(job.detail or "")
        if match:
            done, of = int(match.group(1)), int(match.group(2))
            uniqueness = {"done": done, "total": of}
    elif job.status == "running" and rate and total and total > seen:
        eta_seconds = (total - seen) / rate
    return {
        "id": str(job.id),
        "status": job.status,
        "stage": stage,
        "detail": _scrub(job.detail, 160),
        "files_seen": seen,
        "files_processed": int(job.files_processed or 0),
        "files_skipped": int(job.files_skipped or 0),
        "files_failed": int(job.files_failed or 0),
        "files_total": job.files_total,
        "expected_total": total,
        "started_at": _iso(job.started_at),
        "finished_at": _iso(job.finished_at),
        "elapsed_seconds": int(elapsed) if elapsed is not None else None,
        "files_per_minute": round(rate * 60, 1) if rate else None,
        "eta_seconds": int(eta_seconds) if eta_seconds is not None else None,
        "uniqueness": uniqueness,
        "error": _scrub(job.error, 200),
    }


def _section_scan(settings: Settings, session: Session, redis) -> dict[str, Any]:
    now = datetime.now(UTC)
    latest = session.scalars(
        select(ProcessingJob).where(ProcessingJob.kind == "scan").order_by(ProcessingJob.created_at.desc()).limit(1)
    ).first()
    last_done = session.scalars(
        select(ProcessingJob)
        .where(ProcessingJob.kind == "scan", ProcessingJob.status == "succeeded")
        .order_by(ProcessingJob.finished_at.desc())
        .limit(1)
    ).first()
    previous_total = None
    if last_done is not None:
        previous_total = last_done.files_total or last_done.files_seen
    elif latest is not None:
        # First full scan: the best estimate is the biggest listing seen so far.
        previous_total = session.scalar(
            select(func.max(ProcessingJob.files_seen)).where(ProcessingJob.kind == "scan")
        )
    jobs = _grouped(session, ProcessingJob.status, ProcessingJob.status.in_(["queued", "running"]))
    by_kind = session.execute(
        select(ProcessingJob.kind, ProcessingJob.status, func.count())
        .where(ProcessingJob.status.in_(["queued", "running"]))
        .group_by(ProcessingJob.kind, ProcessingJob.status)
    ).all()
    return {
        "current": scan_progress(latest, previous_total, now),
        "last_completed_at": _iso(last_done.finished_at) if last_done else None,
        "active_jobs": jobs,
        "active_by_kind": [{"kind": k, "status": s, "count": int(n)} for k, s, n in by_kind],
    }


def _section_photos(settings: Settings, session: Session, redis) -> dict[str, Any]:
    total = _count(session, select(func.count()).select_from(Image))
    active_where = Image.skip_reason.is_(None)
    active = _count(session, select(func.count()).select_from(Image).where(active_where))
    skipped = _grouped(session, Image.skip_reason, Image.skip_reason.is_not(None))
    verdicts = _grouped(session, Image.model_state, active_where)
    ratings = _grouped(session, Image.human_rating, active_where)
    scanned = active - verdicts.get("none", 0)
    picks = _grouped(session, CurationPick.status)
    blurred = _count(
        session,
        select(func.count(func.distinct(BlurCover.image_id))).where(BlurCover.approved.is_(True)),
    )
    blur_drafts = _count(session, select(func.count(func.distinct(BlurCover.image_id))))
    unlock = _grouped(session, Image.blur_unlock_status, Image.blur_unlock_status != "none")
    duplicates = _count(session, select(func.count()).select_from(ImageDuplicate))
    day_ago = datetime.now(UTC) - timedelta(hours=24)
    new_24h = _count(session, select(func.count()).select_from(Image).where(Image.ingested_at >= day_ago))
    return {
        "total": total,
        "active": active,
        "skipped": skipped,
        "locally_scanned": scanned,
        "local_verdicts": verdicts,
        "human_ratings": ratings,
        "rated": active - ratings.get("none", 0),
        "rejected": ratings.get("reject", 0),
        "curation_picks": picks,
        "curated": sum(picks.values()),
        "blurred_images": blurred,
        "blur_any_images": blur_drafts,
        "blur_unlock": unlock,
        "blur_verified": unlock.get("verified", 0),
        "duplicates_found": duplicates,
        "ingested_24h": new_24h,
    }


def _section_enrichment(settings: Settings, session: Session, redis) -> dict[str, Any]:
    from instamagic.services.recommendations.enrichment import enrichment_status

    status = enrichment_status(session, redis, prefetch_max_score=settings.enrich_prefetch_max_score)
    status["captioned_images"] = _count(session, select(func.count(func.distinct(Caption.image_id))))
    status["hashtagged_images"] = _count(
        session,
        select(func.count()).select_from(Image).where(Image.enrich_hashtags_json.is_not(None)),
    )
    status["label_status"] = _grouped(session, Image.gemini_label_status, Image.gemini_label_status.is_not(None))
    status["rpm"] = settings.enrich_rpm
    status["concurrency"] = settings.enrich_concurrency
    status.pop("last_batch", None)
    return status


def open_slots(session: Session, view, now: datetime, *, hours: int = 72, limit: int = 12) -> list[datetime]:
    """Slots in the next `hours` a new approved post could take, filled greedily in order."""
    from instamagic.services.posting.slots import _busy_times, best_slots

    horizon = now + timedelta(hours=hours)
    candidates = [s for s in best_slots(view, from_day=now, days=(hours // 24) + 2) if now < s <= horizon]
    if not candidates:
        return []
    gap = timedelta(hours=view.min_gap_hours)
    busy = _busy_times(session, candidates[0] - gap, candidates[-1] + gap)
    tz = ZoneInfo(view.timezone)
    per_day: dict[str, int] = {}
    for value in busy:
        key = value.astimezone(tz).date().isoformat()
        per_day[key] = per_day.get(key, 0) + 1
    out: list[datetime] = []
    for slot in candidates:
        key = slot.astimezone(tz).date().isoformat()
        if any(abs(slot - b) < gap for b in busy):
            continue
        if per_day.get(key, 0) >= view.max_posts_per_day:
            continue
        out.append(slot)
        busy.append(slot)
        per_day[key] = per_day.get(key, 0) + 1
        if len(out) >= limit:
            break
    return out


def _section_queue(settings: Settings, session: Session, redis) -> dict[str, Any]:
    from instamagic.services.posting.schedule_config import config_view

    now = datetime.now(UTC)
    states = _grouped(session, PublishingQueueItem.state)
    view = config_view(session)
    upcoming_rows = session.execute(
        select(PublishingQueueItem, Image.filename)
        .join(Image, Image.id == PublishingQueueItem.image_id)
        .where(
            PublishingQueueItem.state.in_([QueueState.APPROVED.value, QueueState.PUBLISHING.value]),
            PublishingQueueItem.scheduled_at.is_not(None),
        )
        .order_by(PublishingQueueItem.scheduled_at)
        .limit(12)
    ).all()
    unscheduled = _count(
        session,
        select(func.count())
        .select_from(PublishingQueueItem)
        .where(PublishingQueueItem.state == QueueState.APPROVED.value, PublishingQueueItem.scheduled_at.is_(None)),
    )
    slots = open_slots(session, view, now)
    return {
        "states": states,
        "approved_unscheduled": unscheduled,
        "upcoming": [
            {
                "id": str(item.id),
                "filename": filename,
                "state": item.state,
                "media_type": item.media_type,
                "scheduled_at": _iso(item.scheduled_at),
                "story": bool(item.story_share_enabled),
            }
            for item, filename in upcoming_rows
        ],
        "open_slots": [_iso(s) for s in slots],
        "schedule": {
            "timezone": view.timezone,
            "paused": view.paused,
            "max_posts_per_day": view.max_posts_per_day,
            "min_gap_hours": view.min_gap_hours,
            "weekday_slots": view.weekday_slots,
        },
    }


def _section_publish(settings: Settings, session: Session, redis) -> dict[str, Any]:
    from instamagic.services.posting.schedule_config import config_view

    tz = ZoneInfo(config_view(session).timezone)
    attempts = session.execute(
        select(PublishAttempt.kind, PublishAttempt.success, func.count()).group_by(
            PublishAttempt.kind, PublishAttempt.success
        )
    ).all()
    by_kind: dict[str, dict[str, int]] = {}
    for kind, success, n in attempts:
        bucket = by_kind.setdefault(str(kind), {"attempts": 0, "succeeded": 0, "failed": 0})
        bucket["attempts"] += int(n)
        bucket["succeeded" if success else "failed"] += int(n)
    last_attempt = session.scalars(select(PublishAttempt).order_by(PublishAttempt.started_at.desc()).limit(1)).first()
    last_failure = session.scalars(
        select(PublishAttempt)
        .where(PublishAttempt.success.is_(False), PublishAttempt.finished_at.is_not(None))
        .order_by(PublishAttempt.started_at.desc())
        .limit(1)
    ).first()
    last_post = session.scalars(
        select(InstagramPost)
        .where(InstagramPost.published_at.is_not(None))
        .order_by(InstagramPost.published_at.desc())
        .limit(1)
    ).first()
    posts_total = _count(session, select(func.count()).select_from(InstagramPost))
    since = datetime.now(UTC) - timedelta(days=14)
    stamps = session.scalars(
        select(PublishingQueueItem.published_at).where(PublishingQueueItem.published_at >= since)
    ).all()
    today = datetime.now(UTC).astimezone(tz).date()
    days = {(today - timedelta(days=offset)).isoformat(): 0 for offset in range(13, -1, -1)}
    for stamp in stamps:
        if stamp is None:
            continue
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=UTC)
        key = stamp.astimezone(tz).date().isoformat()
        if key in days:
            days[key] += 1
    return {
        "by_kind": by_kind,
        "attempts": sum(b["attempts"] for b in by_kind.values()),
        "succeeded": sum(b["succeeded"] for b in by_kind.values()),
        "failed": sum(b["failed"] for b in by_kind.values()),
        "posts_total": posts_total,
        "last_attempt_at": _iso(last_attempt.started_at) if last_attempt else None,
        "last_failure": (
            {"at": _iso(last_failure.started_at), "error": _scrub(last_failure.error)} if last_failure else None
        ),
        "last_published": (
            {
                "at": _iso(last_post.published_at),
                "permalink": last_post.permalink,
                "media_type": last_post.media_type,
            }
            if last_post
            else None
        ),
        "per_day": [{"date": key, "count": value} for key, value in days.items()],
    }


def _section_instagram(settings: Settings, session: Session, redis) -> dict[str, Any]:
    from instamagic.services.instagram.store import (
        connection_status_fields,
        get_connection,
        resolve_credentials,
    )

    creds = resolve_credentials(settings, session)
    configured = creds is not None and creds.source != "none"
    fields = connection_status_fields(settings, session, configured=configured)
    account = fields.get("account") if isinstance(fields.get("account"), dict) else {}
    snapshot = session.scalars(
        select(InstagramAccountInsightSnapshot).order_by(InstagramAccountInsightSnapshot.captured_at.desc()).limit(1)
    ).first()
    row = get_connection(session)
    granted = fields.get("granted_permissions") or []
    required = fields.get("required_permissions") or []
    return {
        "configured": configured,
        "provider": fields.get("provider"),
        "credential_source": fields.get("credential_source"),
        "username": (account or {}).get("username"),
        "expected_username": fields.get("instagram_expected_username"),
        "followers": snapshot.follower_count if snapshot and snapshot.follower_count is not None
        else (account or {}).get("followers_count"),
        "media_count": (account or {}).get("media_count"),
        "account_fetched_at": fields.get("account_fetched_at"),
        "permissions_granted": len(granted),
        "permissions_missing": sorted(set(required) - set(granted)) if granted else list(required),
        "insights_permission": fields.get("insights_permission_granted"),
        "comments_permission": fields.get("comments_permission_granted"),
        "auto_publish": fields.get("auto_publish"),
        "insights_last_sync_at": _iso(row.insights_last_sync_at) if row else None,
    }


def _section_sync(settings: Settings, session: Session, redis) -> dict[str, Any]:
    from instamagic.workers.social_loop import COMMENTS_LAST_KEY, INSIGHTS_LAST_KEY

    open_comments = _count(
        session,
        select(func.count()).select_from(InstagramInboxComment).where(InstagramInboxComment.status == "open"),
    )
    last_comment_sync = session.scalar(select(func.max(InstagramInboxComment.synced_at)))
    return {
        "insights_tick_at": _epoch_iso(_float_key(redis, INSIGHTS_LAST_KEY)),
        "comments_tick_at": _epoch_iso(_float_key(redis, COMMENTS_LAST_KEY)),
        "last_comment_synced_at": _iso(last_comment_sync),
        "inbox_open": open_comments,
    }


def _section_engage(settings: Settings, session: Session, redis) -> dict[str, Any]:
    from instamagic.services.engage.service import local_today

    today = local_today(session)
    counts = _grouped(session, EngageTarget.status, EngageTarget.run_date == today)
    last = session.scalars(select(EngageRun).order_by(EngageRun.started_at.desc()).limit(1)).first()
    from instamagic.services.engage.service import SETTINGS_ID

    row = session.get(EngageSettings, SETTINGS_ID)
    return {
        "today": today,
        "counts": counts,
        "total": sum(counts.values()),
        "enabled": bool(row.enabled) if row else None,
        "daily_target": row.daily_target if row else None,
        "run_hour_local": row.run_hour_local if row else None,
        "last_run": (
            {
                "status": last.status,
                "run_date": last.run_date,
                "targets_created": last.targets_created,
                "error_code": last.error_code,
                "finished_at": _iso(last.finished_at),
            }
            if last
            else None
        ),
    }


def _section_workers(settings: Settings, session: Session, redis) -> dict[str, Any]:
    from instamagic.services.queues import QUEUE_NAMES
    from instamagic.services.worker_heartbeat import heartbeat_key

    out: list[dict[str, Any]] = []
    if redis is None:
        return {"available": False, "queues": out, "version": settings.app_version}
    from rq import Queue, Worker
    from rq.registry import FailedJobRegistry, StartedJobRegistry

    now = time.time()
    for name in QUEUE_NAMES:
        entry: dict[str, Any] = {"name": name}
        try:
            raw = redis.get(heartbeat_key(name))
            stamp = int(raw.decode() if isinstance(raw, bytes) else raw) if raw else None
            entry["heartbeat_age_seconds"] = int(now - stamp) if stamp else None
            queue = Queue(name, connection=redis)
            entry["queued"] = queue.count
            entry["started"] = StartedJobRegistry(queue=queue).count
            entry["failed"] = FailedJobRegistry(queue=queue).count
            entry["workers"] = len(Worker.all(queue=queue, connection=redis))
        except Exception as exc:
            entry["error"] = type(exc).__name__
        out.append(entry)
    return {"available": True, "queues": out, "version": settings.app_version}


SECTIONS: dict[str, Callable[[Settings, Session, Any], dict[str, Any]]] = {
    "health": _section_health,
    "scan": _section_scan,
    "photos": _section_photos,
    "enrichment": _section_enrichment,
    "queue": _section_queue,
    "publish": _section_publish,
    "instagram": _section_instagram,
    "sync": _section_sync,
    "engage": _section_engage,
    "workers": _section_workers,
}


def _run_section(name: str, fn, settings: Settings, session: Session, redis) -> dict[str, Any]:
    try:
        return fn(settings, session, redis)
    except Exception as exc:
        session.rollback()
        log.info("status_section_failed", section=name, error_type=type(exc).__name__)
        return {"error": f"unavailable ({type(exc).__name__})"}


def _refresh_in_background(name: str, fn, settings: Settings) -> None:
    def worker() -> None:
        try:
            from instamagic.db import session_factory

            with session_factory()() as bg_session:
                value = _run_section(name, fn, settings, bg_session, _redis(settings))
            if "error" not in value:
                with _cache_lock:
                    _section_cache[name] = (time.time(), {**value, "as_of": datetime.now(UTC).isoformat()})
        except Exception as exc:  # pragma: no cover - defensive
            log.info("status_section_refresh_failed", section=name, error_type=type(exc).__name__)
        finally:
            with _cache_lock:
                _refreshing.discard(name)

    threading.Thread(target=worker, name=f"status-{name}", daemon=True).start()


def _slow_section(name: str, fn, settings: Settings, session: Session, redis, now: float) -> dict[str, Any]:
    ttl = SLOW_SECTION_TTL[name]
    with _cache_lock:
        cached = _section_cache.get(name)
        if cached is not None and now - cached[0] < ttl:
            return cached[1]
        if cached is not None:
            if name not in _refreshing:
                _refreshing.add(name)
                start = True
            else:
                start = False
            stale = cached[1]
        else:
            stale = None
    if stale is not None:
        if start:
            _refresh_in_background(name, fn, settings)
        return stale
    value = _run_section(name, fn, settings, session, redis)
    if "error" not in value:
        value = {**value, "as_of": datetime.now(UTC).isoformat()}
        with _cache_lock:
            _section_cache[name] = (now, value)
    return value


def build_app_status(settings: Settings, session: Session, *, bypass_cache: bool = False) -> dict[str, Any]:
    global _cache
    now = time.time()
    with _cache_lock:
        if not bypass_cache and _cache is not None and now - _cache[0] < CACHE_SECONDS:
            return _cache[1]
    redis = _redis(settings)
    payload: dict[str, Any] = {}
    timings: dict[str, int] = {}
    for name, fn in SECTIONS.items():
        started = time.perf_counter()
        if name in SLOW_SECTION_TTL:
            payload[name] = _slow_section(name, fn, settings, session, redis, now)
        else:
            payload[name] = _run_section(name, fn, settings, session, redis)
        timings[name] = int((time.perf_counter() - started) * 1000)
    payload["generated_at"] = datetime.now(UTC).isoformat()
    payload["cached_seconds"] = CACHE_SECONDS
    payload["timings_ms"] = timings
    with _cache_lock:
        _cache = (now, payload)
    return payload


def reset_app_status_cache() -> None:
    global _cache
    with _cache_lock:
        _cache = None
        _section_cache.clear()
        _refreshing.clear()
