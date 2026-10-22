from __future__ import annotations

import sys
import threading

from instamagic.config import get_settings
from instamagic.logging_config import configure_logging, get_logger
from instamagic.services.worker_heartbeat import HEARTBEAT_INTERVAL_SECONDS, write_heartbeat
from redis import Redis
from rq import Queue, Worker


def _start_heartbeat(connection: Redis, queue_name: str) -> threading.Event:
    stop = threading.Event()

    def loop() -> None:
        while not stop.wait(HEARTBEAT_INTERVAL_SECONDS):
            try:
                write_heartbeat(connection, queue_name)
            except Exception:
                pass
        try:
            write_heartbeat(connection, queue_name)
        except Exception:
            pass

    thread = threading.Thread(target=loop, name=f"heartbeat-{queue_name}", daemon=True)
    thread.start()
    return stop


def main() -> None:
    queue_name = sys.argv[1] if len(sys.argv) > 1 else "io"
    if queue_name not in {"io", "ml"}:
        raise SystemExit("Worker queue must be io or ml.")
    settings = get_settings()
    configure_logging(settings.log_level)
    log = get_logger("worker")
    if settings.require_external_library_mounts:
        from instamagic.services.images.paths import assert_configured_external_mounts

        assert_configured_external_mounts(settings.library_roots())
    log.info("worker_start", queue=queue_name)
    if queue_name == "io":
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
    connection = Redis.from_url(settings.redis_url)
    write_heartbeat(connection, queue_name)
    stop_heartbeat = _start_heartbeat(connection, queue_name)
    worker = Worker([Queue(queue_name, connection=connection)], connection=connection)
    try:
        worker.work(with_scheduler=False)
    finally:
        stop_heartbeat.set()


if __name__ == "__main__":
    main()
