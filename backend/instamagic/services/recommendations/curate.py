"""Gemini curation: rank SAFE/EXCELLENT frames, suggest tags + captions as draft picks.

Never auto-queues or publishes. Human review decides what enters the publish queue.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from instamagic.errors import GeminiBlocked, GeminiTruncated, NotAllowed, NotConfigured, NotFound
from instamagic.logging_config import get_logger
from instamagic.models import (
    Caption,
    CaptionStatus,
    ContentTag,
    CurationBatch,
    CurationPick,
    GeminiRequest,
    HumanRating,
    Image,
    ImageTag,
    InstagramPost,
    ProcessingJob,
    PublishAttempt,
    PublishingQueueItem,
    QueueState,
    SafetyState,
    TagSource,
)
from instamagic.models.enums import CurationBatchStatus, CurationPickStatus, JobKind, JobStatus
from instamagic.services.blurs.unlock import blur_gate_passes
from instamagic.services.gemini.captions import CaptionDraft
from instamagic.services.gemini.context import (
    build_context,
    gemini_allows_pixels,
    resolve_curation_send_policy,
)
from instamagic.services.gemini.curation import (
    CURATION_CAPTION_STYLES,
    CurationCandidatePayload,
    CurationPickDraft,
    CurationProvider,
    CurationResult,
    curation_chunk_size,
    curation_max_output_tokens,
)
from instamagic.services.gemini.growth import append_voice_to_context
from instamagic.services.images.folders import escape_like, normalize_folder
from instamagic.services.images.presentation import normalize_human_rating_filter
from instamagic.services.pipeline import is_attested
from instamagic.services.posting.content_mix import classify_image
from instamagic.services.recommendations.gates import gemini_gate, publishing_gate
from instamagic.services.recommendations.performance import (
    image_performance_boost,
    load_performance_signals,
)
from instamagic.services.recommendations.rank import CandidateInput, rank_candidates
from instamagic.services.recommendations.refusals import is_gemini_refused
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

log = get_logger("curate")


def held_back_near_duplicate_count(eligible_count: int, shortlist_count: int) -> int:
    """Near-duplicate frames suppressed by the local ranker (not in the shortlist)."""
    return max(0, eligible_count - shortlist_count)


def parse_curation_detail_held_back(detail: str | None) -> int:
    if not detail:
        return 0
    for part in detail.replace(";", " ").split():
        if part.startswith("held_back="):
            try:
                return int(part.split("=", 1)[1])
            except ValueError:
                return 0
    return 0


@dataclass
class ResilientCurationOutcome:
    chunk_results: list[CurationResult]
    blocked_ids: list[str]


@dataclass
class EligibleCandidate:
    image: Image
    local_score: int
    local_reasons: list[str]
    tags: list[str]
    composition: str
    mood: str
    description: str
    covering_blur: bool
    approved_crop_id: UUID | None
    crop_aspect: str | None


def _existing_captions(session: Session, image_ids: list[UUID]) -> dict[UUID, list[Caption]]:
    if not image_ids:
        return {}
    rows = session.scalars(
        select(Caption)
        .where(Caption.image_id.in_(image_ids))
        .where(Caption.status != CaptionStatus.REJECTED.value)
        .order_by(Caption.created_at.asc())
    ).all()
    out: dict[UUID, list[Caption]] = {}
    for row in rows:
        out.setdefault(row.image_id, []).append(row)
    # Prefer operator-touched captions (used/modified) over freshly generated ones.
    rank = {CaptionStatus.MODIFIED.value: 0, CaptionStatus.USED.value: 1}
    for rows_for_image in out.values():
        rows_for_image.sort(key=lambda caption: rank.get(caption.status, 2))
    return out


def _images_with_captions(session: Session, image_ids: list[UUID]) -> set[UUID]:
    return set(_existing_captions(session, image_ids))


def _stored_hashtags(image: Image) -> list[str]:
    from instamagic.services.recommendations.enrichment import stored_hashtags

    return stored_hashtags(image)


def collect_eligible(
    session: Session,
    *,
    library_id: UUID | None,
    folder: str | None,
    image_ids: list[UUID] | None,
    covering_blur_fn,
    performance_weight: float = 0.0,
) -> list[EligibleCandidate]:
    perf_signals = load_performance_signals(session) if performance_weight > 0 else None
    stmt = (
        select(Image)
        .where(Image.skip_reason.is_(None))
        .where(
            func.lower(Image.human_rating).in_([HumanRating.SAFE.value, HumanRating.EXCELLENT.value])
        )
        .where(
            or_(
                Image.model_state.is_(None),
                Image.model_state != SafetyState.DO_NOT_POST.value,
                Image.blur_unlock_status == "verified",
            )
        )
        .options(
            selectinload(Image.score),
            selectinload(Image.crops),
            selectinload(Image.blurs),
            selectinload(Image.queue_items),
            selectinload(Image.tags).selectinload(ImageTag.tag),
        )
    )
    if library_id is not None:
        stmt = stmt.where(Image.library_id == library_id)
    if image_ids:
        stmt = stmt.where(Image.id.in_(image_ids))
    if folder:
        normalized = normalize_folder(folder)
        if normalized:
            stmt = stmt.where(Image.relative_path.like(escape_like(f"{normalized}/%"), escape="\\"))
    rows = session.scalars(stmt).all()
    attested: dict[UUID, bool] = {}
    eligible: list[EligibleCandidate] = []
    for image in rows:
        if is_gemini_refused(image):
            continue  # Gemini refused this photo once: never auto-eligible, never resent
        if image.library_id not in attested:
            attested[image.library_id] = is_attested(session, image.library_id)
        if not attested[image.library_id]:
            continue
        human_rating = normalize_human_rating_filter(image.human_rating) or image.human_rating
        if human_rating not in {HumanRating.SAFE.value, HumanRating.EXCELLENT.value}:
            continue
        approved = next((crop for crop in image.crops if crop.approved), None)
        covering_blur = covering_blur_fn(session, image)
        if image.model_state == SafetyState.SAFE_AFTER_CROP.value and approved is None and not covering_blur:
            continue
        from instamagic.services.blurs.gate_context import publishing_gate_kwargs

        gate = gemini_gate(
            attested=True,
            operator_confirmed=True,
            policy="text_only",
            **publishing_gate_kwargs(session, image),
        )
        if not gate.allowed:
            continue
        tags = [link.tag.name for link in image.tags if link.tag is not None]
        gemini_tags = [
            link.tag.name for link in image.tags if link.tag is not None and link.source == TagSource.GEMINI.value
        ]
        if gemini_tags:
            tags = list(dict.fromkeys([*gemini_tags, *tags]))
        mood_names = [tag for tag in tags if tag in {"soft", "dramatic", "playful", "elegant", "high key", "low key"}]
        composition = image.orientation or "unknown orientation"
        if image.gemini_description:
            description = image.gemini_description
        elif approved is not None and image.model_state == SafetyState.SAFE_AFTER_CROP.value:
            description = f"Approved {approved.aspect_ratio} crop of a {composition} photograph."
        elif covering_blur and image.model_state == SafetyState.SAFE_AFTER_CROP.value:
            description = f"A {composition} photograph with approved covering blur."
        else:
            description = f"A {composition} photograph."
        mood = image.gemini_mood or (", ".join(mood_names) if mood_names else "unspecified")
        score = image.score
        perf_score = (
            image_performance_boost(perf_signals, str(image.id), tags) if perf_signals is not None else 0.0
        )
        ranked_input = CandidateInput(
            image_id=str(image.id),
            aesthetic_score=score.aesthetic_score if score else 0,
            human_rating=image.human_rating,
            uniqueness=score.uniqueness if score else 0,
            phash=image.phash,
            has_approved_crop=approved is not None or covering_blur,
            crop_aspect=approved.aspect_ratio if approved else None,
            model_state=image.model_state,
            sharpness=score.sharpness if score else 0,
            composition=score.composition if score else 0,
            performance_score=perf_score,
            content_boost=classify_image(image).boost,
        )
        local = rank_candidates([ranked_input], limit=1, performance_weight=performance_weight)[0]
        eligible.append(
            EligibleCandidate(
                image=image,
                local_score=local.score,
                local_reasons=local.reasons,
                tags=tags,
                composition=composition,
                mood=mood,
                description=description,
                covering_blur=covering_blur,
                approved_crop_id=approved.id if approved else None,
                crop_aspect=approved.aspect_ratio if approved else None,
            )
        )
    return eligible


def resolve_curation_limit(limit: int | None, eligible_count: int) -> int:
    """null/0 means all eligible; otherwise clamp to eligible count."""
    if eligible_count <= 0:
        return 0
    if limit is None or limit <= 0:
        return eligible_count
    return min(limit, eligible_count)


def chunk_payloads(
    payloads: list[CurationCandidatePayload],
    *,
    chunk_size: int,
) -> list[list[CurationCandidatePayload]]:
    if chunk_size < 1:
        raise ValueError("chunk_size must be >= 1")
    if not payloads:
        return []
    return [payloads[i : i + chunk_size] for i in range(0, len(payloads), chunk_size)]


def merge_chunked_drafts(
    chunk_results: list[CurationResult],
    *,
    payloads: list[CurationCandidatePayload],
) -> list[CurationPickDraft]:
    """Merge per-chunk Gemini ranks into one ordered list (local_score, then rank)."""
    by_id = {item.image_id: item for item in payloads}
    allowed_ids = set(by_id)
    seen: set[str] = set()
    collected: list[CurationPickDraft] = []
    for result in chunk_results:
        for draft in sorted(result.picks, key=lambda item: item.rank):
            if draft.image_id not in allowed_ids or draft.image_id in seen:
                continue
            seen.add(draft.image_id)
            collected.append(draft)
    if not collected:
        return [
            CurationPickDraft(
                image_id=item.image_id,
                rank=index,
                reasons=item.local_reasons,
                tags=item.content_tags[:8],
                captions=[
                    CaptionDraft(style=style, language=language, text=f"{style}: quiet light, held for the feed.")
                    for style, language in CURATION_CAPTION_STYLES
                ],
            )
            for index, item in enumerate(payloads, start=1)
        ]
    present = {draft.image_id for draft in collected}
    for item in payloads:
        if item.image_id in present:
            continue
        collected.append(
            CurationPickDraft(
                image_id=item.image_id,
                rank=10_000 + len(collected),
                reasons=item.local_reasons[:4] or [f"Local score {item.local_score}."],
                tags=item.content_tags[:8],
                captions=[
                    CaptionDraft(style=style, language=language, text=f"{style}: quiet light, held for the feed.")
                    for style, language in CURATION_CAPTION_STYLES
                ],
            )
        )
    collected.sort(
        key=lambda draft: (
            -(by_id[draft.image_id].local_score if draft.image_id in by_id else 0),
            draft.rank,
        )
    )
    return [
        CurationPickDraft(
            image_id=draft.image_id,
            rank=index,
            reasons=draft.reasons,
            tags=draft.tags,
            captions=draft.captions,
        )
        for index, draft in enumerate(collected, start=1)
    ]


def curate_chunk_resilient(
    provider: CurationProvider,
    chunk: list[CurationCandidatePayload],
    *,
    account_voice: str,
    policy: str,
    image_bytes_by_id: dict[str, bytes] | None,
    thinking_budget: int,
    max_output_tokens: int | None = None,
) -> ResilientCurationOutcome:
    chunk_pixels = None
    if image_bytes_by_id:
        chunk_pixels = {
            item.image_id: image_bytes_by_id[item.image_id]
            for item in chunk
            if item.image_id in image_bytes_by_id
        } or None
    token_budget = max_output_tokens or curation_max_output_tokens(len(chunk))

    def _bisect() -> ResilientCurationOutcome:
        mid = len(chunk) // 2
        left = curate_chunk_resilient(
            provider,
            chunk[:mid],
            account_voice=account_voice,
            policy=policy,
            image_bytes_by_id=image_bytes_by_id,
            thinking_budget=thinking_budget,
        )
        right = curate_chunk_resilient(
            provider,
            chunk[mid:],
            account_voice=account_voice,
            policy=policy,
            image_bytes_by_id=image_bytes_by_id,
            thinking_budget=thinking_budget,
        )
        return ResilientCurationOutcome(
            left.chunk_results + right.chunk_results,
            left.blocked_ids + right.blocked_ids,
        )

    try:
        result = provider.curate_batch(
            chunk,
            account_voice=account_voice,
            policy=policy,
            image_bytes_by_id=chunk_pixels,
            thinking_budget=thinking_budget,
            max_output_tokens=token_budget,
        )
        return ResilientCurationOutcome([result], [])
    except GeminiTruncated:
        if len(chunk) <= 1:
            image_id = chunk[0].image_id if chunk else "unknown"
            log.info("curation_image_truncated", image_id=image_id)
            return ResilientCurationOutcome([], [image_id])
        return _bisect()
    except GeminiBlocked as exc:
        if len(chunk) <= 1:
            image_id = chunk[0].image_id if chunk else "unknown"
            log.info("curation_image_blocked", image_id=image_id, reason=exc.reason)
            return ResilientCurationOutcome([], [image_id])
        return _bisect()


def execute_curation_batch(
    session: Session,
    batch_id: UUID,
    provider: CurationProvider,
    *,
    account_voice: str,
    policy: str,
    covering_blur_fn,
    include_images: bool | None = None,
    preview_bytes_fn=None,
    thinking_budget: int = 512,
    image_ids: list[UUID] | None = None,
    performance_weight: float = 0.0,
) -> CurationBatch:
    """Resume Gemini ranking for a RUNNING batch (worker entrypoint)."""
    batch = session.get(CurationBatch, batch_id)
    if batch is None:
        raise NotFound("Curation batch was not found.")
    if batch.status != CurationBatchStatus.RUNNING.value:
        return batch
    send_policy = resolve_curation_send_policy(env_policy=policy, include_images=include_images)
    eligible = collect_eligible(
        session,
        library_id=batch.library_id,
        folder=batch.folder,
        image_ids=image_ids,
        covering_blur_fn=covering_blur_fn,
        performance_weight=performance_weight,
    )
    if not eligible:
        batch.status = CurationBatchStatus.FAILED.value
        batch.error = "No eligible candidates."
        batch.finished_at = datetime.now(UTC)
        session.commit()
        raise NotAllowed("No SAFE/EXCELLENT attested candidates available for curation.")
    effective_limit = resolve_curation_limit(batch.limit or None, len(eligible))
    perf_signals = load_performance_signals(session) if performance_weight > 0 else None
    shortlist_inputs = [
        CandidateInput(
            image_id=str(item.image.id),
            aesthetic_score=item.image.score.aesthetic_score if item.image.score else 0,
            human_rating=item.image.human_rating,
            uniqueness=item.image.score.uniqueness if item.image.score else 0,
            phash=item.image.phash,
            has_approved_crop=item.approved_crop_id is not None or item.covering_blur,
            crop_aspect=item.crop_aspect,
            model_state=item.image.model_state,
            sharpness=item.image.score.sharpness if item.image.score else 0,
            composition=item.image.score.composition if item.image.score else 0,
            performance_score=(
                image_performance_boost(perf_signals, str(item.image.id), item.tags) if perf_signals else 0.0
            ),
        )
        for item in eligible
    ]
    ranked = rank_candidates(shortlist_inputs, limit=effective_limit, performance_weight=performance_weight)
    by_id = {str(item.image.id): item for item in eligible}
    shortlist = [by_id[row.image_id] for row in ranked if row.image_id in by_id]
    held_back = held_back_near_duplicate_count(len(eligible), len(shortlist))
    ready_caption_ids = _images_with_captions(session, [item.image.id for item in shortlist])
    payloads = [
        CurationCandidatePayload(
            image_id=str(item.image.id),
            local_score=item.local_score,
            human_rating=item.image.human_rating,
            model_state=item.image.model_state,
            aesthetic_score=item.image.score.aesthetic_score if item.image.score else 0,
            sharpness=item.image.score.sharpness if item.image.score else 0,
            composition=item.image.score.composition if item.image.score else 0,
            uniqueness=item.image.score.uniqueness if item.image.score else 0,
            content_tags=item.tags,
            composition_label=item.composition,
            mood=item.mood,
            safe_description=item.description,
            local_reasons=item.local_reasons,
            captions_ready=item.image.id in ready_caption_ids,
        )
        for item in shortlist
    ]
    chunk_size = curation_chunk_size(send_policy)
    chunks = chunk_payloads(payloads, chunk_size=chunk_size)
    pixels_by_id: dict[str, bytes] = {}
    if gemini_allows_pixels(send_policy) and preview_bytes_fn is not None:
        for item in shortlist:
            if item.image.gemini_label_status != "ok":
                continue
            raw = preview_bytes_fn(item.image)
            if raw:
                pixels_by_id[str(item.image.id)] = raw
    sample = shortlist[0]
    sample_crop = None
    if sample.approved_crop_id is not None and sample.crop_aspect:
        sample_crop = {"aspect_ratio": sample.crop_aspect, "reason": "approved"}
    context = append_voice_to_context(
        build_context(
            attested=True,
            human_rating=sample.image.human_rating,
            model_state=sample.image.model_state,
            needs_crop=sample.image.model_state == SafetyState.SAFE_AFTER_CROP.value,
            has_approved_crop=sample.approved_crop_id is not None,
            has_approved_covering_blur=sample.covering_blur,
            policy=send_policy,
            operator_confirmed=True,
            content_tags=sample.tags,
            composition=sample.composition,
            mood=sample.mood,
            safe_description=sample.description,
            crop=sample_crop if sample.image.model_state == SafetyState.SAFE_AFTER_CROP.value else None,
            gemini_refused=any(is_gemini_refused(item.image) for item in shortlist),
            blur_verification_pending=any(not blur_gate_passes(session, item.image) for item in shortlist),
        ),
        account_voice=account_voice,
    )
    audit = GeminiRequest(
        image_id=sample.image.id,
        policy=send_policy,
        context_sha256=context.sha256(),
        context_json=json.dumps(
            {
                "kind": "curation_batch",
                "batch_id": str(batch.id),
                "candidate_ids": [p.image_id for p in payloads],
                "chunks": len(chunks),
                "chunk_size": chunk_size,
                "include_images": bool(pixels_by_id),
                "pixels_attached": len(pixels_by_id),
                "context": context.model_dump(),
            },
            sort_keys=True,
        ),
        status="pending",
    )
    session.add(audit)
    session.flush()
    return _execute_curation_gemini(
        session,
        batch=batch,
        audit=audit,
        provider=provider,
        by_id=by_id,
        payloads=payloads,
        chunks=chunks,
        shortlist=shortlist,
        send_policy=send_policy,
        account_voice=account_voice,
        pixels_by_id=pixels_by_id,
        thinking_budget=thinking_budget,
        held_back=held_back,
        eligible_ids=list(by_id),
        covering_blur_fn=covering_blur_fn,
        scope_image_ids=image_ids,
    )


def _execute_curation_gemini(
    session: Session,
    *,
    batch: CurationBatch,
    audit: GeminiRequest,
    provider: CurationProvider,
    by_id: dict[str, EligibleCandidate],
    payloads: list[CurationCandidatePayload],
    chunks: list[list[CurationCandidatePayload]],
    shortlist: list[EligibleCandidate],
    send_policy: str,
    account_voice: str,
    pixels_by_id: dict[str, bytes],
    thinking_budget: int,
    held_back: int = 0,
    eligible_ids: list[str] | None = None,
    covering_blur_fn=None,
    scope_image_ids: list[UUID] | None = None,
) -> CurationBatch:
    chunk_results: list[CurationResult] = []
    blocked_ids: list[str] = []
    try:
        for index, chunk in enumerate(chunks, start=1):
            batch.detail = f"shortlist={len(shortlist)} held_back={held_back} chunks={index - 1}/{len(chunks)}"
            session.commit()
            outcome = curate_chunk_resilient(
                provider,
                chunk,
                account_voice=account_voice,
                policy=send_policy,
                image_bytes_by_id=pixels_by_id or None,
                thinking_budget=thinking_budget,
            )
            chunk_results.extend(outcome.chunk_results)
            blocked_ids.extend(outcome.blocked_ids)
            batch.detail = (
                f"shortlist={len(shortlist)} held_back={held_back} chunks={index}/{len(chunks)} "
                f"blocked={len(blocked_ids)}"
            )
            session.commit()
    except NotConfigured:
        audit.status = "failed"
        batch.status = CurationBatchStatus.FAILED.value
        batch.error = "Gemini curation failed."
        batch.finished_at = datetime.now(UTC)
        session.commit()
        raise

    audit.status = "succeeded"
    ordered_drafts = merge_chunked_drafts(chunk_results, payloads=payloads)

    existing_by_image = _existing_captions(session, [UUID(d.image_id) for d in ordered_drafts])
    for index, draft in enumerate(ordered_drafts, start=1):
        item = by_id[draft.image_id]
        existing = existing_by_image.get(item.image.id) or []
        captions = [] if existing else draft.captions or [
            CaptionDraft(style=style, language=language, text=f"{style}: quiet light, held for the feed.")
            for style, language in CURATION_CAPTION_STYLES
        ]
        # Reuse pre-generated/user captions (background enrichment) instead of stacking new rows.
        preferred: Caption | None = existing[0] if existing else None
        for caption_draft in captions[:6]:
            row = Caption(
                image_id=item.image.id,
                style=caption_draft.style,
                language=caption_draft.language,
                text=caption_draft.text,
                original_text=caption_draft.text,
                status=CaptionStatus.GENERATED.value,
            )
            session.add(row)
            session.flush()
            if preferred is None:
                preferred = row
        pick = CurationPick(
            batch_id=batch.id,
            image_id=item.image.id,
            sort_order=index,
            gemini_rank=draft.rank,
            local_score=item.local_score,
            reasons_json=json.dumps(draft.reasons[:8], ensure_ascii=False),
            suggested_tags_json=json.dumps(
                (_stored_hashtags(item.image) or draft.tags)[:15], ensure_ascii=False
            ),
            status=CurationPickStatus.SUGGESTED.value,
            preferred_caption_id=preferred.id if preferred else None,
        )
        session.add(pick)

    if covering_blur_fn is not None:
        from instamagic.services.recommendations.curate_skipped import record_skipped

        record_skipped(
            session,
            batch,
            eligible_ids=eligible_ids if eligible_ids is not None else list(by_id),
            shortlist_ids=[str(item.image.id) for item in shortlist],
            picked_ids=[draft.image_id for draft in ordered_drafts],
            blocked_ids=blocked_ids,
            covering_blur_fn=covering_blur_fn,
            image_ids=scope_image_ids,
        )

    batch.status = CurationBatchStatus.READY.value
    batch.finished_at = datetime.now(UTC)
    mode = "preview" if pixels_by_id else "text_only"
    chunk_size = curation_chunk_size(send_policy)
    blocked_note = f" blocked={len(blocked_ids)}"
    if blocked_ids:
        blocked_note += f" ids={','.join(blocked_ids[:12])}"
    batch.detail = (
        f"picks={len(ordered_drafts)} shortlist={len(shortlist)} held_back={held_back} "
        f"chunks={len(chunks)} chunk_size={chunk_size} mode={mode} pixels={len(pixels_by_id)}{blocked_note}"
    )
    session.commit()
    session.refresh(batch)
    return batch


def run_curation(
    session: Session,
    provider: CurationProvider,
    *,
    library_id: UUID | None,
    folder: str | None,
    image_ids: list[UUID] | None,
    limit: int | None,
    account_voice: str,
    policy: str,
    covering_blur_fn,
    include_images: bool | None = None,
    preview_bytes_fn=None,
    defer_execution: bool = False,
    thinking_budget: int = 512,
    performance_weight: float = 0.0,
) -> CurationBatch:
    """Build a draft curation batch. Fail-closed; never queues or publishes.

    Large shortlists are chunked for Gemini, then merged into one CurationBatch.
    When the env ceiling allows and include_images is on, derivative preview/thumb
    bytes ride along (never originals / paths).
    """
    send_policy = resolve_curation_send_policy(env_policy=policy, include_images=include_images)
    if send_policy == "manual_only" or policy == "manual_only":
        raise NotAllowed("GEMINI_IMAGE_POLICY=manual_only blocks batch curation.")
    eligible = collect_eligible(
        session,
        library_id=library_id,
        folder=folder,
        image_ids=image_ids,
        covering_blur_fn=covering_blur_fn,
        performance_weight=performance_weight,
    )
    if not eligible:
        raise NotAllowed("No SAFE/EXCELLENT attested candidates available for curation.")

    effective_limit = resolve_curation_limit(limit, len(eligible))
    perf_signals = load_performance_signals(session) if performance_weight > 0 else None
    shortlist_inputs = [
        CandidateInput(
            image_id=str(item.image.id),
            aesthetic_score=item.image.score.aesthetic_score if item.image.score else 0,
            human_rating=item.image.human_rating,
            uniqueness=item.image.score.uniqueness if item.image.score else 0,
            phash=item.image.phash,
            has_approved_crop=item.approved_crop_id is not None or item.covering_blur,
            crop_aspect=item.crop_aspect,
            model_state=item.image.model_state,
            sharpness=item.image.score.sharpness if item.image.score else 0,
            composition=item.image.score.composition if item.image.score else 0,
            performance_score=(
                image_performance_boost(perf_signals, str(item.image.id), item.tags) if perf_signals else 0.0
            ),
        )
        for item in eligible
    ]
    ranked = rank_candidates(shortlist_inputs, limit=effective_limit, performance_weight=performance_weight)
    by_id = {str(item.image.id): item for item in eligible}
    shortlist = [by_id[row.image_id] for row in ranked if row.image_id in by_id]
    held_back = held_back_near_duplicate_count(len(eligible), len(shortlist))
    ready_caption_ids = _images_with_captions(session, [item.image.id for item in shortlist])
    payloads = [
        CurationCandidatePayload(
            image_id=str(item.image.id),
            local_score=item.local_score,
            human_rating=item.image.human_rating,
            model_state=item.image.model_state,
            aesthetic_score=item.image.score.aesthetic_score if item.image.score else 0,
            sharpness=item.image.score.sharpness if item.image.score else 0,
            composition=item.image.score.composition if item.image.score else 0,
            uniqueness=item.image.score.uniqueness if item.image.score else 0,
            content_tags=item.tags,
            composition_label=item.composition,
            mood=item.mood,
            safe_description=item.description,
            local_reasons=item.local_reasons,
            captions_ready=item.image.id in ready_caption_ids,
        )
        for item in shortlist
    ]
    chunk_size = curation_chunk_size(send_policy)
    chunks = chunk_payloads(payloads, chunk_size=chunk_size)

    pixels_by_id: dict[str, bytes] = {}
    if gemini_allows_pixels(send_policy) and preview_bytes_fn is not None:
        for item in shortlist:
            if item.image.gemini_label_status != "ok":
                continue
            raw = preview_bytes_fn(item.image)
            if raw:
                pixels_by_id[str(item.image.id)] = raw

    sample = shortlist[0]
    sample_crop = None
    if sample.approved_crop_id is not None and sample.crop_aspect:
        sample_crop = {"aspect_ratio": sample.crop_aspect, "reason": "approved"}
    context = append_voice_to_context(
        build_context(
            attested=True,
            human_rating=sample.image.human_rating,
            model_state=sample.image.model_state,
            needs_crop=sample.image.model_state == SafetyState.SAFE_AFTER_CROP.value,
            has_approved_crop=sample.approved_crop_id is not None,
            has_approved_covering_blur=sample.covering_blur,
            policy=send_policy,
            operator_confirmed=True,
            content_tags=sample.tags,
            composition=sample.composition,
            mood=sample.mood,
            safe_description=sample.description,
            crop=sample_crop if sample.image.model_state == SafetyState.SAFE_AFTER_CROP.value else None,
            gemini_refused=any(is_gemini_refused(item.image) for item in shortlist),
            blur_verification_pending=any(not blur_gate_passes(session, item.image) for item in shortlist),
        ),
        account_voice=account_voice,
    )
    audit = GeminiRequest(
        image_id=sample.image.id,
        policy=send_policy,
        context_sha256=context.sha256(),
        context_json=json.dumps(
            {
                "kind": "curation_batch",
                "candidate_ids": [p.image_id for p in payloads],
                "chunks": len(chunks),
                "chunk_size": chunk_size,
                "include_images": bool(pixels_by_id),
                "pixels_attached": len(pixels_by_id),
                "context": context.model_dump(),
            },
            sort_keys=True,
        ),
        status="pending",
    )
    session.add(audit)
    session.flush()

    # Store requested limit: 0 means "all eligible" at request time.
    stored_limit = 0 if limit is None or limit <= 0 else int(limit)
    batch = CurationBatch(
        library_id=library_id,
        folder=folder,
        status=CurationBatchStatus.RUNNING.value,
        limit=stored_limit,
        detail=f"shortlist={len(shortlist)} held_back={held_back} chunks=0/{len(chunks)}",
    )
    session.add(batch)
    session.flush()
    session.commit()
    if defer_execution:
        session.refresh(batch)
        return batch
    return _execute_curation_gemini(
        session,
        batch=batch,
        audit=audit,
        provider=provider,
        by_id=by_id,
        payloads=payloads,
        chunks=chunks,
        shortlist=shortlist,
        send_policy=send_policy,
        account_voice=account_voice,
        pixels_by_id=pixels_by_id,
        thinking_budget=thinking_budget,
        held_back=held_back,
        eligible_ids=list(by_id),
        covering_blur_fn=covering_blur_fn,
        scope_image_ids=image_ids,
    )


def apply_suggested_tags(session: Session, pick: CurationPick) -> list[str]:
    try:
        tags = json.loads(pick.suggested_tags_json or "[]")
    except json.JSONDecodeError:
        tags = []
    applied: list[str] = []
    for raw in tags:
        name = str(raw).strip().lstrip("#").lower().replace(" ", "")[:64]
        if not name or any(ch in name for ch in "/\\\x00\n\r\t"):
            continue
        tag = session.scalar(select(ContentTag).where(ContentTag.name == name))
        if tag is None:
            tag = ContentTag(name=name)
            session.add(tag)
            session.flush()
        exists = session.scalar(
            select(ImageTag).where(ImageTag.image_id == pick.image_id, ImageTag.tag_id == tag.id)
        )
        if exists is None:
            session.add(ImageTag(image_id=pick.image_id, tag_id=tag.id, source=TagSource.GEMINI.value))
        applied.append(name)
    return applied


def queue_picks(
    session: Session,
    *,
    pick_ids: list[UUID],
    covering_blur_fn,
    story_share_enabled: bool | None = None,
    story_render_mode: str | None = None,
    story_border_color: str | None = None,
    story_timing_mode: str | None = None,
    story_delay_hours: int | None = None,
) -> list[PublishingQueueItem]:
    """Human-approved send to publish queue. Re-checks gates; never auto-publishes."""
    if not pick_ids:
        raise NotAllowed("Select at least one curation pick.")
    picks = session.scalars(
        select(CurationPick)
        .where(CurationPick.id.in_(pick_ids))
        .options(selectinload(CurationPick.image).selectinload(Image.crops))
        .order_by(CurationPick.sort_order.asc())
    ).all()
    if len(picks) != len(set(pick_ids)):
        raise NotFound("One or more curation picks were not found.")
    queued: list[PublishingQueueItem] = []
    for pick in picks:
        if pick.status == CurationPickStatus.REJECTED.value:
            raise NotAllowed("Rejected picks cannot enter the publish queue.")
        if pick.status == CurationPickStatus.QUEUED.value:
            existing = session.scalar(
                select(PublishingQueueItem)
                .where(PublishingQueueItem.image_id == pick.image_id)
                .order_by(PublishingQueueItem.created_at.desc())
            )
            if existing is not None:
                queued.append(existing)
            continue
        image = pick.image
        approved = next((crop for crop in image.crops if crop.approved), None)
        from instamagic.services.blurs.gate_context import publishing_gate_kwargs

        gate = publishing_gate(
            attested=is_attested(session, image.library_id),
            **publishing_gate_kwargs(session, image),
        )
        if not gate.allowed:
            raise NotAllowed(" ".join(gate.reasons))
        apply_suggested_tags(session, pick)
        existing = session.scalar(
            select(PublishingQueueItem)
            .where(PublishingQueueItem.image_id == image.id)
            .order_by(PublishingQueueItem.created_at.desc())
        )
        if existing is not None:
            if pick.preferred_caption_id and existing.caption_id is None:
                existing.caption_id = pick.preferred_caption_id
            from instamagic.services.posting.story_fields import apply_story_fields_from_payload

            apply_story_fields_from_payload(
                session,
                existing,
                story_share_enabled=story_share_enabled,
                story_render_mode=story_render_mode,
                story_border_color=story_border_color,
                story_timing_mode=story_timing_mode,
                story_delay_hours=story_delay_hours,
                is_new=False,
            )
            pick.status = CurationPickStatus.QUEUED.value
            queued.append(existing)
            continue
        item = PublishingQueueItem(
            image_id=image.id,
            crop_id=approved.id if approved else None,
            caption_id=pick.preferred_caption_id,
            state=QueueState.CANDIDATE.value,
        )
        from instamagic.services.posting.story_fields import apply_story_fields_from_payload

        apply_story_fields_from_payload(
            session,
            item,
            story_share_enabled=story_share_enabled,
            story_render_mode=story_render_mode,
            story_border_color=story_border_color,
            story_timing_mode=story_timing_mode,
            story_delay_hours=story_delay_hours,
            is_new=True,
        )
        session.add(item)
        pick.status = CurationPickStatus.QUEUED.value
        queued.append(item)
    session.commit()
    return queued


def undo_queued_pick(session: Session, pick: CurationPick) -> CurationPick:
    """Remove a still-candidate queue row and reopen the curation pick (toast undo)."""
    if pick.status != CurationPickStatus.QUEUED.value:
        raise NotAllowed("Only queued picks can be undone here.")
    item = session.scalar(
        select(PublishingQueueItem)
        .where(PublishingQueueItem.image_id == pick.image_id)
        .order_by(PublishingQueueItem.created_at.desc())
    )
    if item is None:
        pick.status = CurationPickStatus.SUGGESTED.value
        session.commit()
        return pick
    if item.state != QueueState.CANDIDATE.value:
        raise NotAllowed("Queue item is already approved or published; cannot undo send.")
    if item.scheduled_at is not None or item.approved_at is not None or item.published_at is not None:
        raise NotAllowed("Queue item was already scheduled or approved; cannot undo send.")
    has_attempts = session.scalar(
        select(func.count()).select_from(PublishAttempt).where(PublishAttempt.queue_item_id == item.id)
    )
    has_post = session.scalar(
        select(func.count()).select_from(InstagramPost).where(InstagramPost.queue_item_id == item.id)
    )
    if has_attempts or has_post:
        raise NotAllowed("Queue item has publish history; cannot undo send.")
    session.delete(item)
    pick.status = CurationPickStatus.ACCEPTED.value
    session.commit()
    return pick


def processing_job_for_batch(session: Session, batch_id: UUID) -> ProcessingJob | None:
    jobs = session.scalars(
        select(ProcessingJob)
        .where(ProcessingJob.kind == JobKind.CURATE.value)
        .order_by(ProcessingJob.created_at.desc())
        .limit(50)
    ).all()
    for job in jobs:
        try:
            params = json.loads(job.detail or "{}")
        except json.JSONDecodeError:
            continue
        if params.get("batch_id") == str(batch_id):
            return job
    return None


def _rq_has_curation_job(redis_url: str, processing_job_id: str) -> bool:
    from instamagic.services.queues import QUEUE_GEMINI, QUEUE_IO
    from redis import Redis
    from rq import Queue
    from rq.job import Job
    from rq.registry import StartedJobRegistry

    try:
        connection = Redis.from_url(redis_url)
        connection.ping()
    except Exception:
        return False
    try:
        for queue_name in (QUEUE_GEMINI, QUEUE_IO):
            queue = Queue(queue_name, connection=connection)
            job_id_sets = [list(queue.job_ids), StartedJobRegistry(queue=queue).get_job_ids()]
            for job_ids in job_id_sets:
                for job_id in job_ids:
                    try:
                        job = Job.fetch(job_id, connection=connection)
                    except Exception:
                        continue
                    if job.args and str(job.args[0]) == processing_job_id:
                        return True
    except Exception:
        return False
    return False


def reconcile_stale_curation_batches(session: Session, redis_url: str) -> int:
    """Mark RUNNING batches failed when their queue worker job is gone."""
    running = session.scalars(
        select(CurationBatch).where(CurationBatch.status == CurationBatchStatus.RUNNING.value)
    ).all()
    if not running:
        return 0
    updated = 0
    now = datetime.now(UTC)
    for batch in running:
        job = processing_job_for_batch(session, batch.id)
        if job is None:
            batch.status = CurationBatchStatus.FAILED.value
            batch.error = "No curation worker job was found for this batch."
            batch.finished_at = now
            updated += 1
            continue
        if job.status in {JobStatus.SUCCEEDED.value, JobStatus.FAILED.value}:
            if batch.status == CurationBatchStatus.RUNNING.value:
                batch.status = CurationBatchStatus.FAILED.value
                batch.error = batch.error or "Curation worker finished without updating the batch."
                batch.finished_at = now
                updated += 1
            continue
        if job.status in {JobStatus.QUEUED.value, JobStatus.RUNNING.value} and not _rq_has_curation_job(
            redis_url, str(job.id)
        ):
            job.status = JobStatus.FAILED.value
            job.error = "Curation worker job expired or was lost from the queue."
            job.finished_at = now
            batch.status = CurationBatchStatus.FAILED.value
            batch.error = "Curation worker job expired or was lost from the queue."
            batch.finished_at = now
            updated += 1
    if updated:
        session.commit()
    return updated


def active_curation_in_flight(session: Session, library_id: UUID | None) -> bool:
    batch_stmt = select(CurationBatch).where(CurationBatch.status == CurationBatchStatus.RUNNING.value)
    if library_id is None:
        batch_stmt = batch_stmt.where(CurationBatch.library_id.is_(None))
    else:
        batch_stmt = batch_stmt.where(CurationBatch.library_id == library_id)
    if session.scalar(batch_stmt.limit(1)) is not None:
        return True
    job_stmt = select(ProcessingJob).where(
        ProcessingJob.kind == JobKind.CURATE.value,
        ProcessingJob.status.in_((JobStatus.QUEUED.value, JobStatus.RUNNING.value)),
    )
    if library_id is None:
        job_stmt = job_stmt.where(ProcessingJob.library_id.is_(None))
    else:
        job_stmt = job_stmt.where(ProcessingJob.library_id == library_id)
    return session.scalar(job_stmt.limit(1)) is not None
