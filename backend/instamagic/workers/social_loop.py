"""Social lane for worker-io: daily Engage list + insights/comment-inbox syncs.

Runs as a daemon thread in the worker-io main process (next to the posting scheduler), so long
library scans in the RQ work horse never block it. Read-only toward other accounts: it never likes,
follows, comments, or publishes.
"""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime

from instamagic.logging_config import get_logger

log = get_logger("worker.social")

TICK_SECONDS = 60
KICK_KEY = "instamagic:social:kick"  # set after reconnect → run syncs (and engage if today has none)
ENGAGE_REFRESH_KEY = "instamagic:engage:refresh"  # "Refresh now"
ENGAGE_LOCK_KEY = "instamagic:engage:lock"
INSIGHTS_LAST_KEY = "instamagic:social:insights_last"
COMMENTS_LAST_KEY = "instamagic:social:comments_last"
INSIGHTS_EVERY_SECONDS = 6 * 3600
COMMENTS_EVERY_SECONDS = 2 * 3600


def request_engage_refresh(redis_url: str) -> bool:
    try:
        from redis import Redis

        Redis.from_url(redis_url, socket_connect_timeout=2, socket_timeout=2).set(ENGAGE_REFRESH_KEY, "1", ex=3600)
        return True
    except Exception:
        return False


def kick_social(redis_url: str) -> None:
    """Best effort: ask the social lane to sync insights/comments and build today's list now."""
    try:
        from redis import Redis

        Redis.from_url(redis_url, socket_connect_timeout=2, socket_timeout=2).set(KICK_KEY, "1", ex=3600)
    except Exception:
        pass


def _session_and_client():
    from instamagic.config import get_settings
    from instamagic.db import session_factory
    from instamagic.services.factory import build_instagram

    settings = get_settings()
    session = session_factory()()
    return settings, session, build_instagram(settings, session)


def _due(connection, key: str, every: int, now: float) -> bool:
    raw = connection.get(key)
    try:
        last = float(raw) if raw else 0.0
    except ValueError:
        last = 0.0
    return now - last >= every


def _run_insights() -> None:
    from instamagic.services.instagram.insights_sync import sync_instagram_insights

    settings, session, client = _session_and_client()
    try:
        result = sync_instagram_insights(session, settings, client)
        log.info("insights_sync_finished", **{k: result.get(k) for k in ("ok", "synced_media", "skipped", "error")})
    finally:
        session.close()


def _run_comments() -> None:
    from instamagic.services.instagram.comment_inbox import sync_inbox_comments

    settings, session, client = _session_and_client()
    try:
        result = sync_inbox_comments(session, settings, client, force=True)
        log.info("comments_sync_finished", **{k: result.get(k) for k in ("ok", "synced", "error")})
    finally:
        session.close()


def engage_due(session, now: datetime) -> bool:
    from instamagic.models import EngageRun
    from instamagic.services.engage.service import get_or_create_settings, local_today, local_tz
    from sqlalchemy import select

    row = get_or_create_settings(session)
    if not row.enabled:
        return False
    if now.astimezone(local_tz(session)).hour < int(row.run_hour_local):
        return False
    today = local_today(session, now)
    done = session.scalars(
        select(EngageRun.id).where(EngageRun.run_date == today, EngageRun.trigger == "schedule").limit(1)
    ).first()
    return done is None


def _run_engage(*, trigger: str, only_if_due: bool) -> None:
    from instamagic.services.engage.service import run_engage, running_run
    from instamagic.services.factory import build_growth

    settings, session, client = _session_and_client()
    try:
        now = datetime.now(UTC)
        if running_run(session, now) is not None:
            return
        if only_if_due and not engage_due(session, now):
            return
        run_engage(
            session, client, build_growth(settings), account_voice=settings.account_voice, trigger=trigger, now=now
        )
    finally:
        session.close()


def _take(connection, key: str) -> bool:
    pipe = connection.pipeline()
    pipe.get(key)
    pipe.delete(key)
    value, _deleted = pipe.execute()
    return bool(value)


def social_tick(connection) -> None:
    now = time.time()
    kicked = _take(connection, KICK_KEY)
    refresh = _take(connection, ENGAGE_REFRESH_KEY)

    if connection.set(ENGAGE_LOCK_KEY, "1", nx=True, ex=1800):
        try:
            if refresh:
                _run_engage(trigger="manual", only_if_due=False)
            elif kicked:
                # After a reconnect: fill today's list now (no-op when already full).
                _run_engage(trigger="reconnect", only_if_due=False)
            else:
                _run_engage(trigger="schedule", only_if_due=True)
        except Exception:
            log.exception("engage_tick_failed")
        finally:
            connection.delete(ENGAGE_LOCK_KEY)

    if kicked or _due(connection, INSIGHTS_LAST_KEY, INSIGHTS_EVERY_SECONDS, now):
        connection.set(INSIGHTS_LAST_KEY, str(now))
        try:
            _run_insights()
        except Exception:
            log.info("insights_sync_failed")
    if kicked or _due(connection, COMMENTS_LAST_KEY, COMMENTS_EVERY_SECONDS, now):
        connection.set(COMMENTS_LAST_KEY, str(now))
        try:
            _run_comments()
        except Exception:
            log.info("comments_sync_failed")


def _run_forever(redis_url: str) -> None:
    from redis import Redis

    connection = Redis.from_url(redis_url, socket_connect_timeout=5, socket_timeout=10)
    time.sleep(15)  # let the worker register first
    while True:
        try:
            social_tick(connection)
        except Exception:
            log.exception("social_tick_failed")
            try:
                connection.connection_pool.disconnect()
            except Exception:
                pass
        # Wake early when the UI asks for a refresh or a reconnect happened.
        for _ in range(TICK_SECONDS // 5):
            try:
                if connection.exists(ENGAGE_REFRESH_KEY) or connection.exists(KICK_KEY):
                    break
            except Exception:
                pass
            time.sleep(5)


def start_social_thread(redis_url: str) -> None:
    thread = threading.Thread(target=_run_forever, args=(redis_url,), name="social-lane", daemon=True)
    thread.start()
    log.info("social_thread_started", interval_seconds=TICK_SECONDS)
