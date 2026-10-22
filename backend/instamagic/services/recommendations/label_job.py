"""Background Gemini labeling for eligible safe/excellent images."""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from uuid import UUID

from instamagic.db import session_factory
from instamagic.errors import GeminiBlocked, GeminiEmpty, GeminiTruncated, NotConfigured
from instamagic.logging_config import get_logger
from instamagic.models import (
    Caption,
    CaptionStatus,
    ContentTag,
    HumanRating,
    Image,
    ImageTag,
    JobStatus,
    ProcessingJob,
    SafetyState,
    TagSource,
)
from instamagic.models.enums import JobKind
from instamagic.services.gemini.context import build_context, gemini_allows_pixels
from instamagic.services.gemini.growth import append_voice_to_context
from instamagic.services.gemini.labeling import (
    LabelingProvider,
    format_blocked_status,
    label_status_is_terminal,
    should_retry_label,
)
from instamagic.services.images.presentation import normalize_human_rating_filter
from instamagic.services.pipeline import is_attested
from instamagic.services.recommendations.curate import collect_eligible
from instamagic.services.recommendations.gates import gemini_gate
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

__all__ = ["should_retry_label", "label_status_is_terminal"]

log = get_logger("gemini.label_job")


def gemini_label_counts(
    session: Session,
    *,
    library_id: UUID | None = None,
    covering_blur_fn,
) -> dict[str, int]:
    eligible = collect_eligible(
        session,
        library_id=library_id,
        folder=None,
        image_ids=None,
        covering_blur_fn=covering_blur_fn,
    )
    labeled = pending = blocked = 0
    for item in eligible:
        status = item.image.gemini_label_status
        if status == "ok":
            labeled += 1
        elif status and status.startswith("blocked:"):
            blocked += 1
        elif should_retry_label(status, item.image.gemini_label_attempts):
            pending += 1
    return {"eligible": len(eligible), "labeled": labeled, "pending": pending, "blocked": blocked}


def resolve_label_targets(
    session: Session,
    *,
    library_id: UUID | None,
    image_ids: list[UUID] | None,
    all_eligible: bool,
    covering_blur_fn,
) -> tuple[list[Image], dict[str, int]]:
    skip_counts: dict[str, int] = {}
    considered = 0

    def skip(reason: str) -> None:
        skip_counts[reason] = skip_counts.get(reason, 0) + 1

    if all_eligible or not image_ids:
        rows = collect_eligible(
            session,
            library_id=library_id,
            folder=None,
            image_ids=image_ids if image_ids and not all_eligible else None,
            covering_blur_fn=covering_blur_fn,
        )
        images = [item.image for item in rows]
    else:
        images = list(
            session.scalars(
                select(Image)
                .where(Image.id.in_(image_ids))
                .options(
                    selectinload(Image.score),
                    selectinload(Image.crops),
                    selectinload(Image.blurs),
                    selectinload(Image.tags).selectinload(ImageTag.tag),
                )
            ).all()
        )
    targets: list[Image] = []
    for image in images:
        considered += 1
        if image.skip_reason is not None:
            skip("skipped")
            continue
        if not is_attested(session, image.library_id):
            skip("not_attested")
            continue
        human_rating = normalize_human_rating_filter(image.human_rating) or image.human_rating
        if human_rating not in {HumanRating.SAFE.value, HumanRating.EXCELLENT.value}:
            skip("not_safe_or_excellent")
            continue
        if image.model_state == SafetyState.DO_NOT_POST.value:
            skip("do_not_post")
            continue
        approved = next((crop for crop in image.crops if crop.approved), None)
        covering = covering_blur_fn(session, image)
        gate = gemini_gate(
            attested=True,
            human_rating=human_rating,
            model_state=image.model_state,
            needs_crop=image.model_state == SafetyState.SAFE_AFTER_CROP.value,
            has_approved_crop=approved is not None,
            has_approved_covering_blur=covering,
            operator_confirmed=True,
            policy="text_only",
        )
        if not gate.allowed:
            skip("gate_blocked")
            continue
        if label_status_is_terminal(image.gemini_label_status):
            skip("already_labeled")
            continue
        if not should_retry_label(image.gemini_label_status, image.gemini_label_attempts):
            skip("not_retryable")
            continue
        targets.append(image)
    skip_counts["considered"] = considered
    skip_counts["targets"] = len(targets)
    return targets, skip_counts


def _apply_label_result(session: Session, image: Image, result) -> None:
    for raw in result.tags:
        name = str(raw).strip().lstrip("#").lower().replace(" ", "")[:64]
        if not name:
            continue
        tag = session.scalar(select(ContentTag).where(ContentTag.name == name))
        if tag is None:
            tag = ContentTag(name=name)
            session.add(tag)
            session.flush()
        exists = session.scalar(
            select(ImageTag).where(ImageTag.image_id == image.id, ImageTag.tag_id == tag.id)
        )
        if exists is None:
            session.add(ImageTag(image_id=image.id, tag_id=tag.id, source=TagSource.GEMINI.value))
    for draft in result.captions:
        session.add(
            Caption(
                image_id=image.id,
                style=draft.style,
                language=draft.language,
                text=draft.text,
                original_text=draft.text,
                status=CaptionStatus.GENERATED.value,
            )
        )
    image.gemini_description = result.description[:2000]
    image.gemini_mood = (result.mood or "unspecified")[:128]
    image.gemini_label_status = "ok"
    image.gemini_label_at = datetime.now(UTC)


def _record_label_error(session: Session, image_id: UUID) -> None:
    """Persist a terminal error after the labeling savepoint was rolled back."""
    image = session.get(Image, image_id)
    if image is None:
        return
    image.gemini_label_attempts = (image.gemini_label_attempts or 0) + 1
    image.gemini_label_status = "error"
    image.gemini_label_at = datetime.now(UTC)


def label_one_image(
    session: Session,
    provider: LabelingProvider,
    image: Image,
    *,
    policy: str,
    account_voice: str,
    preview_bytes_fn,
    thinking_budget: int,
    max_output_tokens: int,
    covering_blur_fn,
) -> str:
    """Label a single image. Returns outcome token: ok, blocked, empty, error."""
    image.gemini_label_attempts = (image.gemini_label_attempts or 0) + 1
    approved = next((crop for crop in image.crops if crop.approved), None)
    covering = covering_blur_fn(session, image)
    tags = [link.tag.name for link in image.tags if link.tag is not None]
    mood_names = [tag for tag in tags if tag in {"soft", "dramatic", "playful", "elegant", "high key", "low key"}]
    composition = image.orientation or "unknown orientation"
    description = image.gemini_description or f"A {composition} photograph."
    crop_payload = None
    if approved is not None:
        crop_payload = {
            "aspect_ratio": approved.aspect_ratio,
            "reason": approved.reason,
            "x": approved.x,
            "y": approved.y,
            "width": approved.width,
            "height": approved.height,
        }
    context = append_voice_to_context(
        build_context(
            attested=True,
            human_rating=image.human_rating,
            model_state=image.model_state,
            needs_crop=image.model_state == SafetyState.SAFE_AFTER_CROP.value,
            has_approved_crop=approved is not None,
            has_approved_covering_blur=covering,
            policy=policy,
            operator_confirmed=True,
            content_tags=tags,
            composition=composition,
            mood=image.gemini_mood or (", ".join(mood_names) if mood_names else "unspecified"),
            safe_description=description,
            crop=crop_payload if image.model_state == SafetyState.SAFE_AFTER_CROP.value and approved else None,
        ),
        account_voice=account_voice,
    )
    pixels = preview_bytes_fn(image) if gemini_allows_pixels(policy) else None
    try:
        result = provider.label_image(
            context,
            policy=policy,
            image_bytes=pixels,
            thinking_budget=thinking_budget,
            max_output_tokens=max_output_tokens,
        )
    except GeminiBlocked as exc:
        image.gemini_label_status = format_blocked_status(exc.reason)
        image.gemini_label_at = datetime.now(UTC)
        return "blocked"
    except (GeminiEmpty, GeminiTruncated):
        image.gemini_label_status = "empty"
        image.gemini_label_at = datetime.now(UTC)
        return "empty"
    except NotConfigured:
        image.gemini_label_status = "error"
        return "error"
    except Exception:
        image.gemini_label_status = "error"
        return "error"
    if not result.captions:
        image.gemini_label_status = "empty"
        image.gemini_label_at = datetime.now(UTC)
        return "empty"
    try:
        _apply_label_result(session, image, result)
    except Exception:
        session.rollback()
        _record_label_error(session, image.id)
        return "error"
    return "ok"


def run_gemini_label_job(
    session: Session,
    provider: LabelingProvider,
    job_id: UUID,
    *,
    policy: str,
    account_voice: str,
    preview_bytes_fn,
    covering_blur_fn,
    concurrency: int,
    thinking_budget: int,
    max_output_tokens: int,
    min_delay_seconds: float = 0.35,
) -> None:
    job = session.get(ProcessingJob, job_id)
    if job is None:
        return
    payload = json.loads(job.detail or "{}")
    library_id = UUID(payload["library_id"]) if payload.get("library_id") else None
    image_ids = [UUID(value) for value in payload.get("image_ids", [])]
    all_eligible = bool(payload.get("all_eligible"))
    targets, skip_counts = resolve_label_targets(
        session,
        library_id=library_id,
        image_ids=image_ids or None,
        all_eligible=all_eligible,
        covering_blur_fn=covering_blur_fn,
    )
    job.status = JobStatus.RUNNING.value
    job.started_at = datetime.now(UTC)
    job.files_total = len(targets)
    job.files_seen = 0
    job.files_processed = 0
    job.files_skipped = 0
    job.files_failed = 0
    session.commit()

    if not targets:
        job.status = JobStatus.SUCCEEDED.value
        job.detail = json.dumps(
            {
                "labeled": 0,
                "blocked": 0,
                "empty": 0,
                "error": 0,
                "skipped": skip_counts,
            },
            sort_keys=True,
        )
        job.finished_at = datetime.now(UTC)
        session.commit()
        log.info("gemini_label_finished", job_id=str(job_id), **skip_counts, labeled=0)
        return

    outcomes = {"ok": 0, "blocked": 0, "empty": 0, "error": 0}

    def work(image_id: UUID) -> tuple[UUID, str]:
        inner = session_factory()()
        outcome = "error"
        try:
            image = inner.scalar(
                select(Image)
                .where(Image.id == image_id)
                .options(
                    selectinload(Image.crops),
                    selectinload(Image.blurs),
                    selectinload(Image.tags).selectinload(ImageTag.tag),
                )
            )
            if image is None:
                return image_id, "error"
            try:
                with inner.begin_nested():
                    outcome = label_one_image(
                        inner,
                        provider,
                        image,
                        policy=policy,
                        account_voice=account_voice,
                        preview_bytes_fn=preview_bytes_fn,
                        thinking_budget=thinking_budget,
                        max_output_tokens=max_output_tokens,
                        covering_blur_fn=covering_blur_fn,
                    )
                inner.commit()
            except Exception:
                inner.rollback()
                _record_label_error(inner, image_id)
                try:
                    inner.commit()
                except Exception:
                    inner.rollback()
                outcome = "error"
            time.sleep(min_delay_seconds)
            return image_id, outcome
        except Exception:
            inner.rollback()
            return image_id, "error"
        finally:
            inner.close()

    workers = max(1, min(concurrency, len(targets)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(work, image.id): image.id for image in targets}
        for future in as_completed(futures):
            _image_id, outcome = future.result()
            outcomes[outcome] = outcomes.get(outcome, 0) + 1
            try:
                job = session.get(ProcessingJob, job_id)
                if job is None:
                    continue
                job.files_seen = (job.files_seen or 0) + 1
                if outcome == "ok":
                    job.files_processed = (job.files_processed or 0) + 1
                elif outcome == "blocked":
                    job.files_skipped = (job.files_skipped or 0) + 1
                elif outcome == "error":
                    job.files_failed = (job.files_failed or 0) + 1
                else:
                    job.files_skipped = (job.files_skipped or 0) + 1
                job.current_path = str(_image_id)
                session.commit()
            except Exception:
                session.rollback()

    job = session.get(ProcessingJob, job_id)
    if job is None:
        return
    job.status = JobStatus.SUCCEEDED.value
    job.detail = json.dumps(
        {
            **outcomes,
            "labeled": outcomes.get("ok", 0),
            "skipped": skip_counts,
        },
        sort_keys=True,
    )
    job.finished_at = datetime.now(UTC)
    job.current_path = None
    session.commit()
    log.info(
        "gemini_label_finished",
        job_id=str(job_id),
        labeled=outcomes.get("ok", 0),
        blocked=outcomes.get("blocked", 0),
        empty=outcomes.get("empty", 0),
        error=outcomes.get("error", 0),
        **skip_counts,
    )


def create_gemini_label_job(
    session: Session,
    *,
    library_id: UUID | None,
    image_ids: list[UUID] | None,
    all_eligible: bool,
) -> ProcessingJob:
    detail = json.dumps(
        {
            "library_id": str(library_id) if library_id else None,
            "image_ids": [str(value) for value in image_ids or []],
            "all_eligible": all_eligible,
        },
        sort_keys=True,
    )
    job = ProcessingJob(
        kind=JobKind.GEMINI_LABEL.value,
        status=JobStatus.QUEUED.value,
        library_id=library_id,
        detail=detail,
    )
    session.add(job)
    session.commit()
    session.refresh(job)
    return job
