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


def main() -> None:
    from instamagic.services.queues import QUEUE_GEMINI, QUEUE_IO, QUEUE_ML

    queue_name = sys.argv[1] if len(sys.argv) > 1 else QUEUE_IO
    if queue_name not in {QUEUE_IO, QUEUE_ML, QUEUE_GEMINI}:
        raise SystemExit("Worker queue must be io, ml, or gemini.")
    settings = get_settings()
    configure_logging(settings.log_level)
    log = get_logger("worker")
    if settings.require_external_library_mounts:
        from instamagic.services.images.paths import assert_configured_external_mounts

        assert_configured_external_mounts(settings.library_roots())
    log.info("worker_start", queue=queue_name)
    use_scheduler = False
    if queue_name == QUEUE_IO:
        from instamagic.workers.periodic import (
            ensure_comments_sync_scheduled,
            ensure_insights_sync_scheduled,
        )
        from instamagic.workers.resume import requeue_interrupted_scans
        from instamagic.workers.scheduler_loop import start_scheduler_thread

        try:
            count = requeue_interrupted_scans(settings.redis_url)
            if count:
                log.info("interrupted_scans_requeued", count=count)
        except Exception:
            log.info("interrupted_scan_requeue_failed")
            raise
        start_scheduler_thread()
        try:
            ensure_insights_sync_scheduled(settings.redis_url)
            ensure_comments_sync_scheduled(settings.redis_url)
        except Exception:
            log.info("insights_sync_schedule_failed")
        use_scheduler = True
    connection = Redis.from_url(settings.redis_url)
    write_heartbeat(connection, queue_name)
    stop_heartbeat = _start_heartbeat(connection, queue_name)
    worker = Worker([Queue(queue_name, connection=connection)], connection=connection)
    try:
        worker.work(with_scheduler=use_scheduler)
    finally:
        stop_heartbeat.set()


if __name__ == "__main__":
    main()
