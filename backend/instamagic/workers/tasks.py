from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from uuid import UUID

from instamagic.config import get_settings
from instamagic.db import dispose_engine, session_factory
from instamagic.logging_config import get_logger
from instamagic.models import JobStatus, ProcessingJob
from instamagic.models.enums import JobKind
from instamagic.services.factory import (
    build_classifier,
    build_face_detector,
    build_gemini,
    build_growth,
    build_instagram,
)
from instamagic.services.instagram.comment_inbox import sync_inbox_comments
from instamagic.services.instagram.insights_sync import sync_instagram_insights
from instamagic.services.pipeline import run_scan
from instamagic.services.recommendations.curate import execute_curation_batch
from instamagic.services.recommendations.gemini_derivatives import (
    curation_derivative_bytes,
    has_approved_covering_blur,
)
from instamagic.services.recommendations.label_job import run_gemini_label_job
from redis import Redis

log = get_logger("worker.tasks")

INSIGHTS_RESCHEDULE_SECONDS = int(timedelta(hours=6).total_seconds())

_SCAN_DB_FAILURE_MESSAGE = (
    "Library scan failed after a database connection error. "
    "Start a new scan to continue; files already ingested are kept."
)


def _scan_failure_message(exc: BaseException) -> str:
    from instamagic.services.db_errors import is_db_connection_error

    if is_db_connection_error(exc):
        return _SCAN_DB_FAILURE_MESSAGE
    return "Library scan failed."


def _mark_scan_job_failed(job_id: str, message: str, *, allow_failed_overwrite: bool = False) -> None:
    dispose_engine(close=False)
    session = session_factory()()
    try:
        job = session.get(ProcessingJob, UUID(job_id))
        if job is None:
            return
        if job.status not in (JobStatus.RUNNING.value, JobStatus.QUEUED.value):
            if not (allow_failed_overwrite and job.status == JobStatus.FAILED.value):
                return
        job.status = JobStatus.FAILED.value
        job.error = message
        job.detail = message[:512]
        job.finished_at = datetime.now(UTC)
        job.current_stage = None
        session.commit()
    except Exception:
        session.rollback()
    finally:
        session.close()


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
    except Exception as exc:
        log.info("scan_failed", library_id=library_id, job_id=job_id)
        try:
            session.rollback()
        except Exception:
            pass
        from instamagic.services.db_errors import is_db_connection_error

        db_error = is_db_connection_error(exc)
        _mark_scan_job_failed(
            job_id,
            _scan_failure_message(exc),
            allow_failed_overwrite=db_error,
        )
        raise
    finally:
        session.close()


def run_curation_job(processing_job_id: str) -> None:
    settings = get_settings()
    session = session_factory()()
    try:
        job = session.get(ProcessingJob, UUID(processing_job_id))
        if job is None or job.kind != JobKind.CURATE.value:
            return
        params = json.loads(job.detail or "{}")
        batch_id = UUID(params["batch_id"])
        image_ids = [UUID(value) for value in params.get("image_ids", []) if value]
        job.status = JobStatus.RUNNING.value
        session.commit()
        execute_curation_batch(
            session,
            batch_id,
            build_gemini(settings),
            account_voice=settings.account_voice,
            policy=settings.gemini_image_policy,
            covering_blur_fn=has_approved_covering_blur,
            include_images=params.get("include_images"),
            preview_bytes_fn=lambda image: curation_derivative_bytes(session, settings, image),
            thinking_budget=settings.gemini_curation_thinking_budget,
            image_ids=image_ids or None,
            performance_weight=settings.curate_performance_weight,
        )
        job.status = JobStatus.SUCCEEDED.value
        session.commit()
        log.info("curation_finished", job_id=processing_job_id, batch_id=str(batch_id))
    except Exception:
        job = session.get(ProcessingJob, UUID(processing_job_id))
        if job is not None:
            job.status = JobStatus.FAILED.value
            job.error = "Gemini curation failed."
            session.commit()
        log.info("curation_failed", job_id=processing_job_id)
        raise
    finally:
        session.close()


def run_gemini_label_job_entry(processing_job_id: str) -> None:
    settings = get_settings()
    session = session_factory()()
    try:
        run_gemini_label_job(
            session,
            build_gemini(settings),
            UUID(processing_job_id),
            policy=settings.gemini_image_policy,
            account_voice=settings.account_voice,
            preview_bytes_fn=lambda image: curation_derivative_bytes(session, settings, image),
            covering_blur_fn=has_approved_covering_blur,
            concurrency=settings.gemini_label_concurrency,
            thinking_budget=settings.gemini_label_thinking_budget,
            max_output_tokens=settings.gemini_label_max_output_tokens,
        )
        log.info("gemini_label_finished", job_id=processing_job_id)
    except Exception:
        job = session.get(ProcessingJob, UUID(processing_job_id))
        if job is not None:
            job.status = JobStatus.FAILED.value
            job.error = "Gemini labeling failed."
            session.commit()
        log.info("gemini_label_failed", job_id=processing_job_id)
        raise
    finally:
        session.close()


def run_enrichment_batch_job() -> None:
    """One background-enrichment batch on the gemini lane; always releases the kick lock."""
    from instamagic.services.recommendations import enrichment

    settings = get_settings()
    connection = Redis.from_url(settings.redis_url)
    session = session_factory()()
    try:
        deps = enrichment.EnrichmentDeps(
            session_factory=session_factory,
            label_provider=build_gemini(settings),
            growth_provider=build_growth(settings),
            preview_bytes_fn=lambda s, image: curation_derivative_bytes(s, settings, image),
            policy=settings.gemini_image_policy,
            account_voice=settings.account_voice,
            thinking_budget=settings.gemini_label_thinking_budget,
            max_output_tokens=settings.gemini_label_max_output_tokens,
            prefetch_max_score=settings.enrich_prefetch_max_score,
        )
        summary = enrichment.run_enrichment_batch(
            session,
            deps,
            redis=connection,
            concurrency=settings.enrich_concurrency,
            rpm=settings.enrich_rpm,
            batch_size=settings.enrich_batch_size,
        )
        connection.set(enrichment.LAST_KEY, json.dumps(summary), ex=7 * 24 * 3600)
    except Exception:
        log.info("enrichment_batch_failed")
        raise
    finally:
        session.close()
        try:
            connection.delete(enrichment.LOCK_KEY)
        except Exception:
            pass


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
    # Recurring cadence now lives in workers.social_loop (not blocked by scans); no reschedule here.


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
    # Recurring cadence now lives in workers.social_loop (not blocked by scans); no reschedule here.
