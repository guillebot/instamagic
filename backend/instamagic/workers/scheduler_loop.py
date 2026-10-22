"""Background scheduler tick for worker-io (60s interval)."""

from __future__ import annotations

import threading
import time

from instamagic.logging_config import get_logger
from instamagic.services.posting.scheduler import run_scheduler_job

log = get_logger("worker.scheduler")
_INTERVAL_SECONDS = 60


def _run_forever() -> None:
    while True:
        try:
            count = run_scheduler_job()
            if count:
                log.info("scheduled_publish_tick", published=count)
        except Exception:
            log.exception("scheduled_publish_tick_failed")
        time.sleep(_INTERVAL_SECONDS)


def start_scheduler_thread() -> None:
    thread = threading.Thread(target=_run_forever, name="posting-scheduler", daemon=True)
    thread.start()
    log.info("scheduler_thread_started", interval_seconds=_INTERVAL_SECONDS)
