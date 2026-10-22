from __future__ import annotations

import json
from datetime import timedelta
from uuid import UUID

from instamagic.config import get_settings
from instamagic.db import session_factory
from instamagic.logging_config import get_logger
from instamagic.models import JobStatus, ProcessingJob
from instamagic.models.enums import JobKind
from instamagic.services.factory import (
    build_classifier,
    build_face_detector,
    build_gemini,
    build_instagram,
)
from instamagic.services.instagram.insights_sync import sync_instagram_insights
from instamagic.services.pipeline import run_scan
from instamagic.services.recommendations.curate import execute_curation_batch
from instamagic.services.recommendations.gemini_derivatives import (
    curation_derivative_bytes,
    has_approved_covering_blur,
)
from instamagic.services.recommendations.label_job import run_gemini_label_job
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
