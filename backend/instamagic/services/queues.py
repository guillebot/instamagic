"""Read-only RQ queue snapshots for the operator UI."""

from __future__ import annotations

from instamagic.schemas.dto import QueueJobOut, QueueOut, QueuesOut
from redis import Redis
from rq import Queue, Worker
from rq.job import Job
from rq.registry import FailedJobRegistry, StartedJobRegistry

QUEUE_IO = "io"
QUEUE_ML = "ml"
QUEUE_GEMINI = "gemini"
QUEUE_NAMES = (QUEUE_IO, QUEUE_ML, QUEUE_GEMINI)


def _describe_job(job: Job, status: str) -> QueueJobOut:
    args = list(job.args or ())
    library_id = str(args[0]) if len(args) >= 1 else None
    processing_job_id = str(args[1]) if len(args) >= 2 else None
    return QueueJobOut(
        id=job.id,
        func_name=job.func_name,
        library_id=library_id,
        job_id=processing_job_id,
        status=status,
    )


def _queue_snapshot(name: str, connection: Redis) -> QueueOut:
    queue = Queue(name, connection=connection)
    started_registry = StartedJobRegistry(queue=queue)
    failed_registry = FailedJobRegistry(queue=queue)
    workers = Worker.all(queue=queue, connection=connection)
    jobs: list[QueueJobOut] = []
    for job_id in list(queue.job_ids)[:5]:
        try:
            job = Job.fetch(job_id, connection=connection)
            jobs.append(_describe_job(job, "queued"))
        except Exception:
            jobs.append(QueueJobOut(id=job_id, status="queued"))
    for job_id in list(started_registry.get_job_ids())[:5]:
        try:
            job = Job.fetch(job_id, connection=connection)
            jobs.append(_describe_job(job, "started"))
        except Exception:
            jobs.append(QueueJobOut(id=job_id, status="started"))
    return QueueOut(
        name=name,
        queued=queue.count,
        started=started_registry.count,
        failed=failed_registry.count,
        workers=len(workers),
        jobs=jobs,
        available=True,
    )


def snapshot_queues(redis_url: str) -> QueuesOut:
    try:
        connection = Redis.from_url(redis_url)
        connection.ping()
        queues = [_queue_snapshot(name, connection) for name in QUEUE_NAMES]
        return QueuesOut(available=True, queues=queues)
    except Exception:
        return QueuesOut(
            available=False,
            detail="queue unavailable",
            queues=[
                QueueOut(name=name, available=False, detail="queue unavailable") for name in QUEUE_NAMES
            ],
        )
