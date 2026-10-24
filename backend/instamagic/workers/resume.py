"""Resume scans that a worker restart left behind.

RQ does not continue an interrupted job and the DB row stays `running` or `queued`.
This runs when the single io worker starts, so any started RQ job is already dead;
only jobs still waiting in the io queue are live. Each stale row is marked
`interrupted` and one fresh scan is enqueued per library, unless that library
already has a scan waiting in the queue.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from instamagic.db import session_factory
from instamagic.logging_config import get_logger
from instamagic.models import JobStatus, ProcessingJob
from redis import Redis
from rq import Queue
from rq.job import Job
from sqlalchemy import select

log = get_logger("worker.resume")

SCAN_FUNC = "instamagic.workers.tasks.run_scan_job"
# A full library scan can run well past 12 hours (about 2 s per new photo). RQ kills
# the work horse at this limit, so keep it generous; resumed scans skip done files.
SCAN_TIMEOUT = 60 * 60 * 24 * 7

KILLED_SCAN_MESSAGE = (
    "Scan worker stopped unexpectedly (time limit or crash). "
    "Start the scan again to resume; finished photos are skipped."
)


def handle_killed_work_horse(job: Job, retpid: object, ret_val: object, rusage: object) -> None:
    """Mark the DB row of a scan whose RQ work horse was killed, so it is not left `running`."""
    try:
        args = list(job.args or ())
        if job.func_name != SCAN_FUNC or len(args) < 2:
            return
        from instamagic.workers.tasks import _mark_scan_job_failed

        _mark_scan_job_failed(str(args[1]), KILLED_SCAN_MESSAGE)
        log.info("scan_work_horse_killed", job_id=str(args[1]))
    except Exception:
        log.info("scan_work_horse_killed_mark_failed")


def _queued_scan_job_ids(queue: Queue, connection: Redis) -> set[str]:
    live: set[str] = set()
    for rq_id in queue.job_ids:
        try:
            job = Job.fetch(rq_id, connection=connection)
        except Exception:
            continue
        args = list(job.args or ())
        if job.func_name == SCAN_FUNC and len(args) >= 2:
            live.add(str(args[1]))
    return live


def requeue_interrupted_scans(redis_url: str) -> int:
    connection = Redis.from_url(redis_url)
    queue = Queue("io", connection=connection)
    waiting = _queued_scan_job_ids(queue, connection)
    session = session_factory()()
    requeued = 0
    try:
        stale = session.scalars(
            select(ProcessingJob)
            .where(
                ProcessingJob.kind == "scan",
                ProcessingJob.status.in_((JobStatus.RUNNING.value, JobStatus.QUEUED.value)),
                ProcessingJob.library_id.is_not(None),
            )
            .order_by(ProcessingJob.created_at.desc())
        ).all()
        by_library: dict[UUID, list[ProcessingJob]] = {}
        for job in stale:
            if job.library_id is not None:
                by_library.setdefault(job.library_id, []).append(job)
        for library_id, jobs in by_library.items():
            keep = next((job for job in jobs if str(job.id) in waiting), None)
            for job in jobs:
                if keep is not None and job.id == keep.id:
                    continue
                job.status = JobStatus.INTERRUPTED.value
                job.finished_at = datetime.now(UTC)
                job.current_stage = None
                job.detail = "Interrupted by worker restart"
            if keep is not None:
                session.commit()
                log.info("scan_resume_not_needed", library_id=str(library_id), job_id=str(keep.id))
                continue
            fresh = ProcessingJob(
                kind="scan",
                status=JobStatus.QUEUED.value,
                library_id=library_id,
                detail="Resuming after worker restart",
            )
            session.add(fresh)
            session.commit()
            queue.enqueue(SCAN_FUNC, str(library_id), str(fresh.id), job_timeout=SCAN_TIMEOUT)
            requeued += 1
            log.info(
                "scan_resumed",
                library_id=str(library_id),
                job_id=str(fresh.id),
                interrupted=[str(job.id) for job in jobs],
            )
    finally:
        session.close()
    return requeued
