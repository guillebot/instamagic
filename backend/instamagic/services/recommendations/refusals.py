"""Permanent Gemini-refusal flag and the prefetch circuit breaker.

A photo Gemini refused (PROHIBITED_CONTENT, blocked OTHER, safety finish) is flagged forever and never
sent to Gemini again, on any tier. The human rating is never changed. If Gemini refuses
``BREAKER_REFUSALS`` prefetch photos within the last hour or within the last ``BREAKER_SENDS``
prefetch photos, prefetch is switched OFF automatically and the reason is shown in the status bar.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime

from instamagic.logging_config import get_logger
from instamagic.models import Image

log = get_logger("enrichment.refusals")

BREAKER_REFUSALS = 2
BREAKER_WINDOW_SECONDS = 60 * 60
BREAKER_SENDS = 100
REFUSALS_KEY = "enrich:prefetch:refusals"  # sorted set of refusal timestamps
RECENT_KEY = "enrich:prefetch:recent"  # list of last N prefetch photo outcomes ("1" = refused)


def is_gemini_refused(image: Image) -> bool:
    return (
        image.gemini_refused_at is not None
        or (image.gemini_label_status or "").startswith("blocked:")
        or (image.enrich_status or "").startswith("blocked:")
    )


def is_provider_refusal(exc: BaseException) -> bool:
    """True for a refusal returned by Gemini itself (not a local gate)."""
    return bool(getattr(exc, "from_provider", False))


def mark_gemini_refused(image: Image, reason: str | None) -> None:
    if image.gemini_refused_at is None:
        image.gemini_refused_at = datetime.now(UTC)
        image.gemini_refused_reason = (reason or "refused")[:64]
        log.info("gemini_refused_marked", image_id=str(image.id))


def note_prefetch_outcome(redis, *, refused: bool) -> bool:
    """Record one prefetch photo outcome. Returns True when the breaker should trip."""
    if redis is None:
        return refused  # no window available: fail safe on any refusal
    now = time.time()
    try:
        pipe = redis.pipeline()
        pipe.lpush(RECENT_KEY, "1" if refused else "0")
        pipe.ltrim(RECENT_KEY, 0, BREAKER_SENDS - 1)
        if refused:
            pipe.zadd(REFUSALS_KEY, {f"{now:.6f}": now})
        pipe.zremrangebyscore(REFUSALS_KEY, 0, now - BREAKER_WINDOW_SECONDS)
        pipe.execute()
        if not refused:
            return False
        in_hour = int(redis.zcard(REFUSALS_KEY) or 0)
        recent = [
            item.decode() if isinstance(item, bytes) else str(item)
            for item in redis.lrange(RECENT_KEY, 0, -1)
        ]
        return in_hour >= BREAKER_REFUSALS or recent.count("1") >= BREAKER_REFUSALS
    except Exception:
        return refused


def reset_breaker(redis) -> None:
    if redis is None:
        return
    try:
        redis.delete(REFUSALS_KEY, RECENT_KEY)
    except Exception:
        pass


def trip_breaker(session_factory_fn) -> None:
    """Switch prefetch OFF with a visible reason (own session so it commits even if the caller rolls back)."""
    from instamagic.services.posting.schedule_config import get_or_create_config

    session = session_factory_fn()
    try:
        config = get_or_create_config(session)
        if config.enrichment_prefetch_enabled:
            config.enrichment_prefetch_enabled = False
            config.enrichment_prefetch_paused_at = datetime.now(UTC)
            config.enrichment_prefetch_paused_reason = (
                f"Prefetch turned off automatically: Gemini refused {BREAKER_REFUSALS} or more unrated photos "
                f"within an hour or within the last {BREAKER_SENDS} prefetch photos. Those photos are flagged and never resent."
            )
            session.commit()
            log.info("prefetch_breaker_tripped")
    finally:
        session.close()
