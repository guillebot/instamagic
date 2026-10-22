from __future__ import annotations

import sys

from instamagic.config import get_settings
from instamagic.logging_config import configure_logging, get_logger
from redis import Redis
from rq import Queue, Worker


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
    use_scheduler = False
    if queue_name == "io":
        from instamagic.workers.periodic import (
            ensure_comments_sync_scheduled,
            ensure_insights_sync_scheduled,
        )
        from instamagic.workers.resume import requeue_interrupted_scans

        try:
            count = requeue_interrupted_scans(settings.redis_url)
            if count:
                log.info("interrupted_scans_requeued", count=count)
        except Exception:
            log.info("interrupted_scan_requeue_failed")
            raise
        try:
            ensure_insights_sync_scheduled(settings.redis_url)
            ensure_comments_sync_scheduled(settings.redis_url)
        except Exception:
            log.info("insights_sync_schedule_failed")
        use_scheduler = True
    connection = Redis.from_url(settings.redis_url)
    worker = Worker([Queue(queue_name, connection=connection)], connection=connection)
    worker.work(with_scheduler=use_scheduler)


if __name__ == "__main__":
    main()
