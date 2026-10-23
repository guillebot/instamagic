"""Background scheduler tick for worker-io (60s interval)."""

from __future__ import annotations

import threading
import time

from instamagic.db import dispose_engine
from instamagic.logging_config import get_logger
from instamagic.services.db_errors import is_db_connection_error
from instamagic.services.posting.scheduler import run_scheduler_job

log = get_logger("worker.scheduler")
_INTERVAL_SECONDS = 60


def _run_scheduler_tick() -> int:
    try:
        return run_scheduler_job()
    except Exception as exc:
        if not is_db_connection_error(exc):
            raise
        dispose_engine(close=False)
        return run_scheduler_job()


def _run_forever() -> None:
    while True:
        try:
            count = _run_scheduler_tick()
            if count:
                log.info("scheduled_publish_tick", published=count)
        except Exception:
            log.exception("scheduled_publish_tick_failed")
        time.sleep(_INTERVAL_SECONDS)


def start_scheduler_thread() -> None:
    thread = threading.Thread(target=_run_forever, name="posting-scheduler", daemon=True)
    thread.start()
    log.info("scheduler_thread_started", interval_seconds=_INTERVAL_SECONDS)
