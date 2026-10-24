"""Background enrichment: labels, captions and hashtags for every safe photo, ahead of time.

Eligibility is the same central gate used by captions, queue and publish (``collect_eligible`` →
``gemini_gate`` + ``publishing_gate_kwargs``): human safe/excellent rating, attested library,
DO_NOT_POST only with a verified blur unlock (Gemini sees the blurred publish derivative),
SAFE_AFTER_CROP only with an approved crop or covering blur. Each image is re-checked right
before any Gemini call. Existing captions and hashtags are never overwritten.

Lower-priority "prefetch" tier (``prefetch.py``): unrated photos the local classifier left at REVIEW
with no detections get labels/captions/hashtags ahead of the operator's rating. Prefetch only fills
batch slots the rated tier did not need, has its own toggle and daily cap (inside the global cap), and
never grants queue/publish/curation eligibility: those still require a human safe/excellent rating.

Runs as small batches on the ``gemini`` RQ lane (never the ``io`` lane used by scans, insights
and comment syncs). Batches are kicked by worker-io's scheduler thread, so curation and
on-demand labeling jobs interleave between batches.
"""

from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from instamagic.errors import GeminiBlocked, GeminiEmpty, GeminiRateLimited, GeminiTruncated
from instamagic.logging_config import get_logger
from instamagic.models import Caption, Image, ImageTag, SafetyState
from instamagic.services.blurs.gate_context import publishing_gate_kwargs
from instamagic.services.gemini.context import build_context, build_prefetch_context
from instamagic.services.gemini.growth import append_voice_to_context
from instamagic.services.gemini.labeling import format_blocked_status, should_retry_label
from instamagic.services.instagram.hashtag_research import suggest_tags_from_research
from instamagic.services.pipeline import is_attested
from instamagic.services.posting.schedule_config import get_or_create_config
from instamagic.services.recommendations.curate import collect_eligible
from instamagic.services.recommendations.gemini_derivatives import has_approved_covering_blur
from instamagic.services.recommendations.prefetch import (
    PrefetchIneligible,
    assert_prefetch_safe,
    effective_max_score,
    is_prefetch_candidate,
    pending_prefetch_ids,
    prefetch_counts,
)
from instamagic.services.recommendations.refusals import (
    is_gemini_refused,
    is_provider_refusal,
    mark_gemini_refused,
    note_prefetch_outcome,
    trip_breaker,
)
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

log = get_logger("enrichment")

LOCK_KEY = "instamagic:enrich:lock"
IDLE_KEY = "instamagic:enrich:idle"
BACKOFF_KEY = "instamagic:enrich:backoff_until"
BACKOFF_LEVEL_KEY = "instamagic:enrich:backoff_level"
LAST_KEY = "instamagic:enrich:last"
CALLS_KEY_PREFIX = "instamagic:enrich:calls:"
PREFETCH_CALLS_KEY_PREFIX = "instamagic:enrich:prefetch_calls:"
TIER_RATED = "rated"
TIER_PREFETCH = "prefetch"
LOCK_TTL_SECONDS = 30 * 60
IDLE_TTL_SECONDS = 5 * 60
MAX_ATTEMPTS = 5
BATCH_FUNC = "instamagic.workers.tasks.run_enrichment_batch_job"


def stored_hashtags(image: Image) -> list[str]:
    if not image.enrich_hashtags_json:
        return []
    try:
        values = json.loads(image.enrich_hashtags_json)
    except json.JSONDecodeError:
        return []
    return [str(value) for value in values if str(value).strip()] if isinstance(values, list) else []


def needs_label(image: Image) -> bool:
    return should_retry_label(image.gemini_label_status, image.gemini_label_attempts or 0, max_attempts=MAX_ATTEMPTS)


def needs_hashtags(image: Image) -> bool:
    if image.enrich_hashtags_json:
        return False
    if (image.enrich_status or "").startswith("blocked:"):
        return False
    if image.enrich_status == "error" and (image.enrich_attempts or 0) >= MAX_ATTEMPTS:
        return False
    # Hashtags build on the label description; a Gemini-refused label means no hashtags either.
    return image.gemini_label_status == "ok" or needs_label(image)


def is_ready(image: Image) -> bool:
    return image.gemini_label_status == "ok" and bool(image.enrich_hashtags_json)


def is_error(image: Image) -> bool:
    label_err = image.gemini_label_status == "error"
    return label_err or image.enrich_status == "error"


def is_refused(image: Image) -> bool:
    status = image.gemini_label_status or ""
    return status == "empty" or status.startswith("blocked:") or (image.enrich_status or "").startswith("blocked:")


def eligible_images(session: Session, *, image_ids: list[UUID] | None = None) -> list[Image]:
    rows = collect_eligible(
        session,
        library_id=None,
        folder=None,
        image_ids=image_ids,
        covering_blur_fn=has_approved_covering_blur,
    )
    return [row.image for row in rows]


def pending_images(images: list[Image]) -> list[Image]:
    pending = [image for image in images if needs_label(image) or needs_hashtags(image)]
    # Fresh, never-tried images first; retries after.
    pending.sort(key=lambda image: ((image.gemini_label_attempts or 0) + (image.enrich_attempts or 0), str(image.id)))
    return pending


def _today_key(now: datetime | None = None, *, prefix: str = CALLS_KEY_PREFIX) -> str:
    return prefix + (now or datetime.now(UTC)).strftime("%Y%m%d")


def calls_today(redis, *, prefetch: bool = False) -> int:
    """Gemini calls today (UTC). Global count includes prefetch calls; ``prefetch=True`` counts only those."""
    if redis is None:
        return 0
    try:
        prefix = PREFETCH_CALLS_KEY_PREFIX if prefetch else CALLS_KEY_PREFIX
        return int(redis.get(_today_key(prefix=prefix)) or 0)
    except Exception:
        return 0


def _count_call(redis, *, prefetch: bool = False, n: int = 1) -> None:
    if redis is None:
        return
    try:
        prefixes = [CALLS_KEY_PREFIX] + ([PREFETCH_CALLS_KEY_PREFIX] if prefetch else [])
        for prefix in prefixes:
            key = _today_key(prefix=prefix)
            for _ in range(n):
                redis.incr(key)
            redis.expire(key, 2 * 24 * 3600)
    except Exception:
        pass


def backoff_until(redis) -> float:
    if redis is None:
        return 0.0
    try:
        return float(redis.get(BACKOFF_KEY) or 0)
    except Exception:
        return 0.0


def _enter_backoff(redis) -> float:
    if redis is None:
        return 0.0
    level = int(redis.incr(BACKOFF_LEVEL_KEY))
    redis.expire(BACKOFF_LEVEL_KEY, 6 * 3600)
    delay = min(60 * (2 ** (level - 1)), 30 * 60)
    until = time.time() + delay
    redis.set(BACKOFF_KEY, str(until), ex=int(delay) + 5)
    log.info("enrichment_backoff", seconds=delay, level=level)
    return until


class RateLimiter:
    """Thread-safe minimum spacing between Gemini calls (requests per minute)."""

    def __init__(self, rpm: int, *, clock=time.monotonic, sleep=time.sleep) -> None:
        self._interval = 60.0 / max(1, rpm)
        self._next = 0.0
        self._lock = threading.Lock()
        self._clock = clock
        self._sleep = sleep

    def acquire(self) -> None:
        with self._lock:
            now = self._clock()
            wait = self._next - now
            self._next = max(now, self._next) + self._interval
        if wait > 0:
            self._sleep(wait)


@dataclass
class EnrichmentDeps:
    session_factory: object
    label_provider: object
    growth_provider: object
    preview_bytes_fn: object  # (session, image) -> bytes | None
    policy: str
    account_voice: str
    thinking_budget: int
    max_output_tokens: int
    prefetch_max_score: float = 0.10


def _has_captions(session: Session, image: Image) -> bool:
    return bool(session.scalar(select(func.count()).select_from(Caption).where(Caption.image_id == image.id)))


def _load_one(session: Session, image_id: UUID) -> Image | None:
    return session.scalar(
        select(Image)
        .where(Image.id == image_id)
        .options(
            selectinload(Image.crops),
            selectinload(Image.blurs),
            selectinload(Image.tags).selectinload(ImageTag.tag),
        )
    )


def generate_hashtags(
    session: Session,
    growth_provider,
    image: Image,
    *,
    account_voice: str,
    prefetch: bool = False,
    max_score: float = 0.10,
) -> list[str]:
    """Text-only hashtag suggestion (no pixels), merged with cached hashtag research."""
    approved = next((crop for crop in image.crops if crop.approved), None)
    gate_kwargs = publishing_gate_kwargs(session, image)
    tags = [link.tag.name for link in image.tags if link.tag is not None]
    composition = image.orientation or "unknown orientation"
    if prefetch:
        context = append_voice_to_context(
            build_prefetch_context(
                attested=is_attested(session, image.library_id),
                human_rating=image.human_rating,
                model_state=image.model_state,
                classifier_clean=is_prefetch_candidate(session, image, max_score=max_score),
                has_any_blur=bool(image.blurs),
                skip_reason=image.skip_reason,
                policy="text_only",
                operator_confirmed=True,
                content_tags=tags,
                composition=composition,
                mood=image.gemini_mood or "unspecified",
                safe_description=image.gemini_description or f"A {composition} photograph.",
                gemini_refused=is_gemini_refused(image),
            ),
            account_voice=account_voice,
        )
        # Final pre-send re-check (raises PrefetchIneligible; nothing is sent).
        assert_prefetch_safe(session, image, max_score=max_score)
        suggestion = growth_provider.suggest_hashtags(context, account_voice=account_voice)
        return suggest_tags_from_research(session, suggestion.tags)
    context = append_voice_to_context(
        build_context(
            attested=is_attested(session, image.library_id),
            human_rating=image.human_rating,
            model_state=image.model_state,
            needs_crop=image.model_state == SafetyState.SAFE_AFTER_CROP.value,
            has_approved_crop=approved is not None,
            has_approved_covering_blur=bool(gate_kwargs["has_approved_covering_blur"]),
            has_approved_blur=bool(gate_kwargs["has_approved_blur"]),
            do_not_post_blur_unlocked=bool(gate_kwargs["do_not_post_blur_unlocked"]),
            policy="text_only",
            operator_confirmed=True,
            content_tags=tags,
            composition=composition,
            mood=image.gemini_mood or "unspecified",
            safe_description=image.gemini_description or f"A {composition} photograph.",
            crop=None,
            gemini_refused=is_gemini_refused(image),
        ),
        account_voice=account_voice,
    )
    suggestion = growth_provider.suggest_hashtags(context, account_voice=account_voice)
    return suggest_tags_from_research(session, suggestion.tags)


def _prefetch_refused(deps: EnrichmentDeps, redis, image_id: UUID) -> None:
    """A prefetch photo was refused by Gemini: feed the circuit breaker (auto-OFF on repeat refusals)."""
    log.info("prefetch_gemini_refused", image_id=str(image_id))
    if note_prefetch_outcome(redis, refused=True):
        trip_breaker(deps.session_factory())  # type: ignore[operator]


def enrich_one(
    image_id: UUID,
    deps: EnrichmentDeps,
    *,
    limiter: RateLimiter,
    redis,
    budget,
    stop: threading.Event,
    tier: str = TIER_RATED,
) -> str:
    """Label (if needed) then hashtag (if needed) one image. Returns ok|skipped|refused|error|rate_limited|capped."""
    from instamagic.services.recommendations.label_job import label_one_image

    session: Session = deps.session_factory()()  # type: ignore[operator]
    try:
        # Re-check eligibility right before spending anything; rating/state may have changed.
        # A photo rated safe meanwhile is handled by the central gate (rated tier) even if queued as prefetch.
        prefetch = False
        if not eligible_images(session, image_ids=[image_id]):
            if tier != TIER_PREFETCH:
                return "skipped"
            image = _load_one(session, image_id)
            if image is None or not is_prefetch_candidate(session, image, max_score=deps.prefetch_max_score):
                return "skipped"
            prefetch = True
        image = _load_one(session, image_id)
        if image is None:
            return "skipped"
        outcome = "skipped"
        label_needed = needs_label(image)
        if prefetch and label_needed and _has_captions(session, image):
            label_needed = False  # prefetch skips photos that already have captions
        reserved = 0
        if prefetch:
            # Reserve the whole photo up front (label + hashtags) so a cap never leaves photos half done.
            reserved = int(label_needed) + int(label_needed or needs_hashtags(image))
            if reserved and (stop.is_set() or not budget(True, reserved)):
                return "capped"
        if label_needed:
            if not prefetch and (stop.is_set() or not budget(False, 1)):
                return "capped"
            limiter.acquire()
            try:
                result = label_one_image(
                    session,
                    deps.label_provider,  # type: ignore[arg-type]
                    image,
                    policy=deps.policy,
                    account_voice=deps.account_voice,
                    preview_bytes_fn=lambda img: deps.preview_bytes_fn(session, img),  # type: ignore[operator]
                    thinking_budget=deps.thinking_budget,
                    max_output_tokens=deps.max_output_tokens,
                    covering_blur_fn=has_approved_covering_blur,
                    prefetch=prefetch,
                )
            except PrefetchIneligible:
                session.rollback()
                log.info("prefetch_recheck_blocked", image_id=str(image_id))
                return "skipped"
            except GeminiRateLimited:
                session.rollback()
                return "rate_limited"
            session.commit()
            if result == "ok":
                outcome = "ok"
            elif result in {"blocked", "empty"}:
                if prefetch and result == "blocked" and image.gemini_refused_at is not None:
                    _prefetch_refused(deps, redis, image_id)
                return "refused"
            else:
                return "error"
            image = _load_one(session, image_id)
            if image is None:
                return "error"
        if image.gemini_label_status == "ok" and needs_hashtags(image):
            if not prefetch and (stop.is_set() or not budget(False, 1)):
                return "capped"
            if prefetch and stop.is_set():
                return "capped"
            limiter.acquire()
            image.enrich_attempts = (image.enrich_attempts or 0) + 1
            try:
                hashtags = generate_hashtags(
                    session,
                    deps.growth_provider,
                    image,
                    account_voice=deps.account_voice,
                    prefetch=prefetch,
                    max_score=deps.prefetch_max_score,
                )
            except PrefetchIneligible:
                session.rollback()
                log.info("prefetch_recheck_blocked", image_id=str(image_id))
                return "skipped"
            except GeminiRateLimited:
                session.rollback()
                return "rate_limited"
            except GeminiBlocked as exc:
                image.enrich_status = format_blocked_status(exc.reason)
                image.enrich_at = datetime.now(UTC)
                if is_provider_refusal(exc):
                    mark_gemini_refused(image, image.enrich_status)
                session.commit()
                if prefetch and is_provider_refusal(exc):
                    _prefetch_refused(deps, redis, image_id)
                return "refused"
            except (GeminiEmpty, GeminiTruncated, ValueError, Exception):
                image.enrich_status = "error"
                image.enrich_at = datetime.now(UTC)
                session.commit()
                return "error"
            if image.enrich_hashtags_json:  # filled meanwhile (e.g. by the operator); never overwrite
                session.rollback()
                return "skipped"
            image.enrich_hashtags_json = json.dumps(hashtags[:15], ensure_ascii=False)
            image.enrich_status = "ok"
            image.enrich_at = datetime.now(UTC)
            session.commit()
            outcome = "ok"
        if prefetch and outcome == "ok":
            note_prefetch_outcome(redis, refused=False)
        return outcome
    except Exception:
        session.rollback()
        log.info("enrichment_image_failed", image_id=str(image_id))
        return "error"
    finally:
        session.close()


def run_enrichment_batch(
    session: Session,
    deps: EnrichmentDeps,
    *,
    redis,
    concurrency: int,
    rpm: int,
    batch_size: int,
    limiter: RateLimiter | None = None,
) -> dict[str, object]:
    """One small batch. Returns a summary; the scheduler kicks the next batch."""
    config = get_or_create_config(session)
    summary: dict[str, object] = {"at": datetime.now(UTC).isoformat(), "state": "idle"}
    if not config.enrichment_enabled:
        summary["state"] = "disabled"
        _mark_idle(redis)
        return summary
    if config.enrichment_paused:
        summary["state"] = "paused"
        _mark_idle(redis)
        return summary
    if backoff_until(redis) > time.time():
        summary["state"] = "backoff"
        return summary
    cap = max(0, int(config.enrichment_daily_cap or 0))
    prefetch_cap = min(cap, max(0, int(config.enrichment_prefetch_daily_cap or 0)))
    if calls_today(redis) >= cap:
        summary["state"] = "capped"
        _mark_idle(redis)
        return summary
    size = max(1, batch_size)
    targets: list[tuple[UUID, str]] = [
        (image.id, TIER_RATED) for image in pending_images(eligible_images(session))
    ][:size]
    # Prefetch fills only the slots the rated tier did not need (rated always first).
    prefetch_capped = False
    if len(targets) < size and config.enrichment_prefetch_enabled:
        if calls_today(redis, prefetch=True) >= prefetch_cap:
            prefetch_capped = True
        else:
            taken = {image_id for image_id, _tier in targets}
            for image_id in pending_prefetch_ids(
                session, max_score=deps.prefetch_max_score, limit=size - len(targets) + len(taken)
            ):
                if image_id not in taken and len(targets) < size:
                    targets.append((image_id, TIER_PREFETCH))
    session.commit()
    summary["rated"] = sum(1 for _id, tier in targets if tier == TIER_RATED)
    summary["prefetch"] = sum(1 for _id, tier in targets if tier == TIER_PREFETCH)
    if not targets:
        if prefetch_capped:
            summary["state"] = "prefetch_capped"
        _mark_idle(redis)
        return summary

    limiter = limiter or RateLimiter(rpm)
    stop = threading.Event()
    budget_lock = threading.Lock()

    def budget(prefetch: bool = False, n: int = 1) -> bool:
        """Atomically check and reserve ``n`` calls (global cap; prefetch also within its own cap)."""
        with budget_lock:
            if calls_today(redis) + n > cap:
                return False
            if prefetch and calls_today(redis, prefetch=True) + n > prefetch_cap:
                return False
            _count_call(redis, prefetch=prefetch, n=n)
            return True

    outcomes: dict[str, int] = {}
    with ThreadPoolExecutor(max_workers=max(1, min(concurrency, len(targets)))) as pool:
        futures = [
            pool.submit(
                enrich_one, image_id, deps, limiter=limiter, redis=redis, budget=budget, stop=stop, tier=tier
            )
            for image_id, tier in targets
        ]
        for future in as_completed(futures):
            outcome = future.result()
            outcomes[outcome] = outcomes.get(outcome, 0) + 1
            if outcome == "rate_limited":
                stop.set()
    if outcomes.get("rate_limited"):
        _enter_backoff(redis)
        summary["state"] = "backoff"
    elif outcomes.get("capped"):
        summary["state"] = "capped" if calls_today(redis) >= cap else "prefetch_capped"
    else:
        summary["state"] = "running"
        if redis is not None:
            try:
                redis.delete(BACKOFF_LEVEL_KEY)
            except Exception:
                pass
    summary["outcomes"] = outcomes
    log.info("enrichment_batch", targets=len(targets), rated=summary["rated"], prefetch=summary["prefetch"], **outcomes)
    return summary


def _mark_idle(redis) -> None:
    if redis is not None:
        try:
            redis.set(IDLE_KEY, "1", ex=IDLE_TTL_SECONDS)
        except Exception:
            pass


def kick_enrichment(redis_url: str, *, force: bool = False) -> bool:
    """Enqueue one batch on the gemini lane unless one is already queued/running or we are idle."""
    from instamagic.services.queues import QUEUE_GEMINI
    from redis import Redis
    from rq import Queue

    connection = Redis.from_url(redis_url)
    if force:
        connection.delete(IDLE_KEY)
    elif connection.exists(IDLE_KEY):
        return False
    if backoff_until(connection) > time.time():
        return False
    if not connection.set(LOCK_KEY, "1", nx=True, ex=LOCK_TTL_SECONDS):
        return False
    try:
        Queue(QUEUE_GEMINI, connection=connection).enqueue(BATCH_FUNC, job_timeout=LOCK_TTL_SECONDS)
    except Exception:
        connection.delete(LOCK_KEY)
        raise
    return True


def wake_enrichment(redis_url: str) -> None:
    """Clear the idle marker so the next scheduler tick re-checks for work (cheap, best effort)."""
    try:
        from redis import Redis

        Redis.from_url(redis_url).delete(IDLE_KEY)
    except Exception:
        pass


def enrichment_status(session: Session, redis, *, prefetch_max_score: float = 0.10) -> dict[str, object]:
    config = get_or_create_config(session)
    prefetch = prefetch_counts(session, max_score=prefetch_max_score)
    refused_total = int(
        session.scalar(select(func.count()).select_from(Image).where(Image.gemini_refused_at.is_not(None))) or 0
    )
    prefetch_enabled = bool(config.enrichment_prefetch_enabled)
    prefetch_cap = min(int(config.enrichment_daily_cap or 0), int(config.enrichment_prefetch_daily_cap or 0))
    prefetch_used = calls_today(redis, prefetch=True)
    images = eligible_images(session)
    ready = sum(1 for image in images if is_ready(image))
    pending = len(pending_images(images))
    errors = sum(1 for image in images if is_error(image))
    retryable = sum(
        1
        for image in images
        if (image.gemini_label_status == "error" and (image.gemini_label_attempts or 0) >= MAX_ATTEMPTS)
        or (image.enrich_status == "error" and (image.enrich_attempts or 0) >= MAX_ATTEMPTS)
    )
    refused = sum(1 for image in images if is_refused(image))
    cap = int(config.enrichment_daily_cap or 0)
    used = calls_today(redis)
    until = backoff_until(redis)
    running = False
    last: object = None
    if redis is not None:
        try:
            running = bool(redis.exists(LOCK_KEY))
            raw = redis.get(LAST_KEY)
            last = json.loads(raw) if raw else None
        except Exception:
            running = False
    if not config.enrichment_enabled:
        state = "disabled"
    elif config.enrichment_paused:
        state = "paused"
    elif until > time.time():
        state = "backoff"
    elif pending and used >= cap:
        state = "capped"
    elif pending:
        state = "running"
    elif prefetch_enabled and prefetch["pending"] and (used >= cap or prefetch_used >= prefetch_cap):
        state = "prefetch_capped"
    elif prefetch_enabled and prefetch["pending"]:
        state = "prefetching"
    else:
        state = "idle"
    return {
        "enabled": bool(config.enrichment_enabled),
        "paused": bool(config.enrichment_paused),
        "daily_cap": cap,
        "calls_today": used,
        "eligible": len(images),
        "ready": ready,
        "pending": pending,
        "errors": errors,
        "errors_exhausted": retryable,
        "refused": refused,
        "state": state,
        "batch_active": running,
        "backoff_until": datetime.fromtimestamp(until, UTC).isoformat() if until > time.time() else None,
        "last_batch": last,
        "prefetch_enabled": prefetch_enabled,
        "prefetch_paused_reason": None
        if prefetch_enabled
        else config.enrichment_prefetch_paused_reason,
        "prefetch_paused_at": (
            None
            if prefetch_enabled or config.enrichment_prefetch_paused_at is None
            else config.enrichment_prefetch_paused_at.isoformat()
        ),
        "gemini_refused": refused_total,
        "prefetch_daily_cap": prefetch_cap,
        "prefetch_calls_today": prefetch_used,
        "prefetch_eligible": prefetch["eligible"],
        "prefetch_max_score": effective_max_score(prefetch_max_score),
        "prefetched": prefetch["ready"],
        "prefetch_pending": prefetch["pending"],
    }


def retry_errors(session: Session) -> int:
    """Make errored eligible images pending again (does not touch refused/blocked or ok ones)."""
    count = 0
    for image in eligible_images(session):
        touched = False
        if image.gemini_label_status == "error":
            image.gemini_label_attempts = 0
            touched = True
        if image.enrich_status == "error":
            image.enrich_status = None
            image.enrich_attempts = 0
            touched = True
        count += int(touched)
    session.commit()
    return count
