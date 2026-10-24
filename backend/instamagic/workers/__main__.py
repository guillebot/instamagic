from __future__ import annotations

import sys
import threading

from instamagic.config import get_settings
from instamagic.logging_config import configure_logging, get_logger
from instamagic.services.worker_heartbeat import HEARTBEAT_INTERVAL_SECONDS, write_heartbeat
from redis import Redis
from redis.exceptions import ConnectionError as RedisConnectionError
from rq import Queue, Worker


def _start_heartbeat(connection: Redis, queue_name: str) -> threading.Event:
    stop = threading.Event()

    def _write_once() -> None:
        write_heartbeat(connection, queue_name)

    def loop() -> None:
        while not stop.wait(HEARTBEAT_INTERVAL_SECONDS):
            try:
                _write_once()
            except RedisConnectionError:
                try:
                    connection.connection_pool.disconnect()
                except Exception:
                    pass
                try:
                    _write_once()
                except Exception:
                    pass
            except Exception:
                pass
        try:
            _write_once()
        except RedisConnectionError:
            try:
                connection.connection_pool.disconnect()
            except Exception:
                pass
            try:
                _write_once()
            except Exception:
                pass
        except Exception:
            pass

    thread = threading.Thread(target=loop, name=f"heartbeat-{queue_name}", daemon=True)
    thread.start()
    return stop


SOCIAL_JOB_SUFFIXES = ("sync_instagram_insights_job", "sync_instagram_comments_job")


def move_social_jobs_off_io(connection: Redis) -> dict[str, int]:
    """Move leftover insights/comments sync jobs from the io queue to the social queue.

    Older builds enqueued these on io, where they waited behind long library scans.
    Duplicates are dropped (one of each kind is enough; the social loop keeps the cadence).
    """
    from instamagic.services.queues import QUEUE_IO, QUEUE_SOCIAL
    from rq.job import Job

    io_queue = Queue(QUEUE_IO, connection=connection)
    social_queue = Queue(QUEUE_SOCIAL, connection=connection)
    already = set()
    for job_id in social_queue.job_ids:
        try:
            already.add(Job.fetch(job_id, connection=connection).func_name)
        except Exception:
            continue
    moved = dropped = 0
    for job_id in list(io_queue.job_ids):
        try:
            job = Job.fetch(job_id, connection=connection)
        except Exception:
            continue
        func_name = job.func_name or ""
        if not func_name.endswith(SOCIAL_JOB_SUFFIXES):
            continue
        io_queue.remove(job_id)
        try:
            job.delete()
        except Exception:
            pass
        if func_name in already:
            dropped += 1
            continue
        social_queue.enqueue(func_name, job_timeout=1800)
        already.add(func_name)
        moved += 1
    return {"moved": moved, "dropped": dropped}


def main() -> None:
    from instamagic.services.queues import QUEUE_GEMINI, QUEUE_IO, QUEUE_ML, QUEUE_SOCIAL

    queue_name = sys.argv[1] if len(sys.argv) > 1 else QUEUE_IO
    if queue_name not in {QUEUE_IO, QUEUE_ML, QUEUE_GEMINI}:
        raise SystemExit("Worker queue must be io, ml, or gemini.")
    settings = get_settings()
    configure_logging(settings.log_level)
    log = get_logger("worker")
    if settings.require_external_library_mounts:
        from instamagic.services.images.paths import assert_configured_external_mounts

        assert_configured_external_mounts(settings.library_roots())
    # worker-gemini also serves the lightweight "social" lane (insights/comments syncs + Engage),
    # so a long library scan on worker-io never blocks them.
    listen = [queue_name, QUEUE_SOCIAL] if queue_name == QUEUE_GEMINI else [queue_name]
    log.info("worker_start", queue=queue_name, listen=listen)
    use_scheduler = False
    connection = Redis.from_url(settings.redis_url)
    if queue_name == QUEUE_IO:
        from instamagic.workers.resume import requeue_interrupted_scans
        from instamagic.workers.scheduler_loop import start_scheduler_thread

        try:
            count = requeue_interrupted_scans(settings.redis_url)
            if count:
                log.info("interrupted_scans_requeued", count=count)
        except Exception:
            log.info("interrupted_scan_requeue_failed")
            raise
        try:
            result = move_social_jobs_off_io(connection)
            if result["moved"] or result["dropped"]:
                log.info("social_jobs_moved_off_io", **result)
        except Exception as exc:
            log.info("social_jobs_move_failed", error_type=type(exc).__name__)
        start_scheduler_thread()
        use_scheduler = True
    if queue_name == QUEUE_GEMINI:
        # Engage list + insights/comment syncs run on their own thread in the social lane.
        from instamagic.workers.social_loop import start_social_thread

        start_social_thread(settings.redis_url)
    stops = []
    for name in listen:
        write_heartbeat(connection, name)
        stops.append(_start_heartbeat(connection, name))
    killed_handler = None
    if queue_name == QUEUE_IO:
        from instamagic.workers.resume import handle_killed_work_horse

        killed_handler = handle_killed_work_horse
    worker = Worker(
        [Queue(name, connection=connection) for name in listen],
        connection=connection,
        work_horse_killed_handler=killed_handler,
    )
    try:
        worker.work(with_scheduler=use_scheduler)
    finally:
        for stop in stops:
            stop.set()


if __name__ == "__main__":
    main()
