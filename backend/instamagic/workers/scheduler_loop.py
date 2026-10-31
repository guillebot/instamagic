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
_REEL_PROPOSE_EVERY_SECONDS = 3600
_last_reel_propose = 0.0


def _run_scheduler_tick() -> int:
    try:
        return run_scheduler_job()
    except Exception as exc:
        if not is_db_connection_error(exc):
            raise
        dispose_engine(close=False)
        return run_scheduler_job()


def _kick_enrichment() -> None:
    """Queue the next background-enrichment batch on the gemini lane (never blocks this thread)."""
    from instamagic.config import get_settings
    from instamagic.services.recommendations.enrichment import kick_enrichment

    if kick_enrichment(get_settings().redis_url):
        log.info("enrichment_kicked")


def _maybe_propose_reels(now: float | None = None) -> int:
    """Hourly: top up Reel slideshow candidates (CANDIDATE only; the user approves each one)."""
    global _last_reel_propose
    now = time.monotonic() if now is None else now
    if _last_reel_propose and now - _last_reel_propose < _REEL_PROPOSE_EVERY_SECONDS:
        return 0
    _last_reel_propose = now
    from instamagic.db import session_factory
    from instamagic.services.posting.reel_candidates import propose_reel_candidates

    with session_factory()() as session:
        made = propose_reel_candidates(session)
    if made:
        log.info("reel_candidates_proposed", count=len(made))
    return len(made)


def _run_forever() -> None:
    while True:
        try:
            count = _run_scheduler_tick()
            if count:
                log.info("scheduled_publish_tick", published=count)
        except Exception:
            log.exception("scheduled_publish_tick_failed")
        try:
            _maybe_propose_reels()
        except Exception:
            log.info("reel_candidates_propose_failed")
        try:
            _kick_enrichment()
        except Exception:
            log.info("enrichment_kick_failed")
        time.sleep(_INTERVAL_SECONDS)


def start_scheduler_thread() -> None:
    thread = threading.Thread(target=_run_forever, name="posting-scheduler", daemon=True)
    thread.start()
    log.info("scheduler_thread_started", interval_seconds=_INTERVAL_SECONDS)
