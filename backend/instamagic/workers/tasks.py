from __future__ import annotations

from datetime import timedelta
from uuid import UUID

from instamagic.config import get_settings
from instamagic.db import session_factory
from instamagic.logging_config import get_logger
from instamagic.services.factory import build_classifier, build_face_detector, build_instagram
from instamagic.services.instagram.comment_inbox import sync_inbox_comments
from instamagic.services.instagram.insights_sync import sync_instagram_insights
from instamagic.services.pipeline import run_scan
from redis import Redis
from rq import Queue

log = get_logger("worker.tasks")

INSIGHTS_RESCHEDULE_SECONDS = int(timedelta(hours=6).total_seconds())


def run_scan_job(library_id: str, job_id: str) -> None:
    settings = get_settings()
    session = session_factory()()
    try:
        run_scan(
            session,
            UUID(library_id),
            settings.derivatives_root,
            build_classifier(settings),
            build_face_detector(settings),
            UUID(job_id),
        )
        log.info("scan_finished", library_id=library_id, job_id=job_id)
    except Exception:
        log.info("scan_failed", library_id=library_id, job_id=job_id)
        raise
    finally:
        session.close()


def sync_instagram_comments_job() -> None:
    settings = get_settings()
    session = session_factory()()
    try:
        client = build_instagram(settings, session)
        result = sync_inbox_comments(session, settings, client, force=True)
        log.info("comments_sync_finished", **{k: result.get(k) for k in ("ok", "synced", "error")})
    except Exception:
        log.info("comments_sync_failed")
        raise
    finally:
        session.close()
    try:
        connection = Redis.from_url(settings.redis_url)
        queue = Queue("io", connection=connection)
        queue.enqueue_in(timedelta(hours=2), sync_instagram_comments_job)
    except Exception:
        log.info("comments_sync_reschedule_failed")


def sync_instagram_insights_job() -> None:
    settings = get_settings()
    session = session_factory()()
    try:
        client = build_instagram(settings, session)
        result = sync_instagram_insights(session, settings, client)
        log.info("insights_sync_finished", **{k: result.get(k) for k in ("ok", "synced_media", "skipped", "error")})
    except Exception:
        log.info("insights_sync_failed")
        raise
    finally:
        session.close()
    try:
        connection = Redis.from_url(settings.redis_url)
        queue = Queue("io", connection=connection)
        queue.enqueue_in(timedelta(seconds=INSIGHTS_RESCHEDULE_SECONDS), sync_instagram_insights_job)
    except Exception:
        log.info("insights_sync_reschedule_failed")
