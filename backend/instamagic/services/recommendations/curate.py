"""Gemini curation: rank SAFE/EXCELLENT frames, suggest tags + captions as draft picks.

Never auto-queues or publishes. Human review decides what enters the publish queue.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from instamagic.errors import GeminiBlocked, NotAllowed, NotConfigured, NotFound
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
    PublishingQueueItem,
    QueueState,
    SafetyState,
    TagSource,
)
from instamagic.models.enums import CurationBatchStatus, CurationPickStatus
from instamagic.services.gemini.captions import CaptionDraft
from instamagic.services.gemini.context import build_context
from instamagic.services.gemini.curation import (
    CURATION_CAPTION_STYLES,
    CurationCandidatePayload,
    CurationPickDraft,
    CurationProvider,
)
from instamagic.services.gemini.growth import append_voice_to_context
from instamagic.services.images.folders import escape_like, normalize_folder
from instamagic.services.pipeline import is_attested
from instamagic.services.recommendations.gates import gemini_gate, publishing_gate
from instamagic.services.recommendations.rank import CandidateInput, rank_candidates
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload


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


def collect_eligible(
    session: Session,
    *,
    library_id: UUID | None,
    folder: str | None,
    image_ids: list[UUID] | None,
    covering_blur_fn,
) -> list[EligibleCandidate]:
    stmt = (
        select(Image)
        .where(Image.skip_reason.is_(None))
        .where(Image.human_rating.in_([HumanRating.SAFE.value, HumanRating.EXCELLENT.value]))
        .where(Image.model_state.is_not(None))
        .where(Image.model_state != SafetyState.DO_NOT_POST.value)
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
        if image.library_id not in attested:
            attested[image.library_id] = is_attested(session, image.library_id)
        if not attested[image.library_id]:
            continue
        approved = next((crop for crop in image.crops if crop.approved), None)
        covering_blur = covering_blur_fn(session, image)
        if image.model_state == SafetyState.SAFE_AFTER_CROP.value and approved is None and not covering_blur:
            continue
        gate = gemini_gate(
            attested=True,
            human_rating=image.human_rating,
            model_state=image.model_state,
            needs_crop=image.model_state == SafetyState.SAFE_AFTER_CROP.value,
            has_approved_crop=approved is not None,
            has_approved_covering_blur=covering_blur,
            operator_confirmed=True,
            policy="text_only",
        )
        if not gate.allowed:
            continue
        tags = [link.tag.name for link in image.tags if link.tag is not None]
        mood_names = [tag for tag in tags if tag in {"soft", "dramatic", "playful", "elegant", "high key", "low key"}]
        composition = image.orientation or "unknown orientation"
        description = f"A {composition} photograph."
        if approved is not None and image.model_state == SafetyState.SAFE_AFTER_CROP.value:
            description = f"Approved {approved.aspect_ratio} crop of a {composition} photograph."
        elif covering_blur and image.model_state == SafetyState.SAFE_AFTER_CROP.value:
            description = f"A {composition} photograph with approved covering blur."
        score = image.score
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
        )
        local = rank_candidates([ranked_input], limit=1)[0]
        eligible.append(
            EligibleCandidate(
                image=image,
                local_score=local.score,
                local_reasons=local.reasons,
                tags=tags,
                composition=composition,
                mood=", ".join(mood_names) if mood_names else "unspecified",
                description=description,
                covering_blur=covering_blur,
                approved_crop_id=approved.id if approved else None,
                crop_aspect=approved.aspect_ratio if approved else None,
            )
        )
    return eligible


def run_curation(
    session: Session,
    provider: CurationProvider,
    *,
    library_id: UUID | None,
    folder: str | None,
    image_ids: list[UUID] | None,
    limit: int,
    account_voice: str,
    policy: str,
    covering_blur_fn,
) -> CurationBatch:
    """Build a draft curation batch. Fail-closed; never queues or publishes."""
    if policy == "manual_only":
        raise NotAllowed("GEMINI_IMAGE_POLICY=manual_only blocks batch curation.")
    eligible = collect_eligible(
        session,
        library_id=library_id,
        folder=folder,
        image_ids=image_ids,
        covering_blur_fn=covering_blur_fn,
    )
    if not eligible:
        raise NotAllowed("No SAFE/EXCELLENT attested candidates available for curation.")

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
        )
        for item in eligible
    ]
    ranked = rank_candidates(shortlist_inputs, limit=limit)
    by_id = {str(item.image.id): item for item in eligible}
    shortlist = [by_id[row.image_id] for row in ranked if row.image_id in by_id]
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
        )
        for item in shortlist
    ]

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
            policy="text_only",
            operator_confirmed=True,
            content_tags=sample.tags,
            composition=sample.composition,
            mood=sample.mood,
            safe_description=sample.description,
            crop=sample_crop if sample.image.model_state == SafetyState.SAFE_AFTER_CROP.value else None,
        ),
        account_voice=account_voice,
    )
    audit = GeminiRequest(
        image_id=sample.image.id,
        policy="text_only",
        context_sha256=context.sha256(),
        context_json=json.dumps(
            {
                "kind": "curation_batch",
                "candidate_ids": [p.image_id for p in payloads],
                "context": context.model_dump(),
            },
            sort_keys=True,
        ),
        status="pending",
    )
    session.add(audit)
    session.flush()

    batch = CurationBatch(
        library_id=library_id,
        folder=folder,
        status=CurationBatchStatus.READY.value,
        limit=limit,
        detail=f"shortlist={len(shortlist)}",
    )
    session.add(batch)
    session.flush()

    try:
        result = provider.curate_batch(payloads, account_voice=account_voice, policy="text_only")
    except (GeminiBlocked, NotConfigured):
        audit.status = "failed"
        batch.status = CurationBatchStatus.FAILED.value
        batch.error = "Gemini curation failed."
        batch.finished_at = datetime.now(UTC)
        session.commit()
        raise
    except Exception:
        audit.status = "failed"
        batch.status = CurationBatchStatus.FAILED.value
        batch.error = "Gemini curation failed."
        batch.finished_at = datetime.now(UTC)
        session.commit()
        raise NotConfigured("Gemini curation failed.") from None

    audit.status = "succeeded"
    allowed_ids = {p.image_id for p in payloads}
    seen: set[str] = set()
    ordered_drafts: list[CurationPickDraft] = []
    for draft in sorted(result.picks, key=lambda item: item.rank):
        if draft.image_id not in allowed_ids or draft.image_id in seen:
            continue
        seen.add(draft.image_id)
        ordered_drafts.append(draft)
    if not ordered_drafts:
        ordered_drafts = [
            CurationPickDraft(
                image_id=str(item.image.id),
                rank=index,
                reasons=item.local_reasons,
                tags=item.tags[:8],
                captions=[
                    CaptionDraft(style=style, language=language, text=f"{style}: quiet light, held for the feed.")
                    for style, language in CURATION_CAPTION_STYLES
                ],
            )
            for index, item in enumerate(shortlist, start=1)
        ]

    for index, draft in enumerate(ordered_drafts, start=1):
        item = by_id[draft.image_id]
        captions = draft.captions or [
            CaptionDraft(style=style, language=language, text=f"{style}: quiet light, held for the feed.")
            for style, language in CURATION_CAPTION_STYLES
        ]
        preferred: Caption | None = None
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
            suggested_tags_json=json.dumps(draft.tags[:12], ensure_ascii=False),
            status=CurationPickStatus.SUGGESTED.value,
            preferred_caption_id=preferred.id if preferred else None,
        )
        session.add(pick)

    batch.finished_at = datetime.now(UTC)
    batch.detail = f"picks={len(ordered_drafts)} shortlist={len(shortlist)}"
    session.commit()
    session.refresh(batch)
    return batch


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
        covering_blur = covering_blur_fn(session, image)
        gate = publishing_gate(
            attested=is_attested(session, image.library_id),
            human_rating=image.human_rating,
            model_state=image.model_state,
            needs_crop=image.model_state == SafetyState.SAFE_AFTER_CROP.value,
            has_approved_crop=approved is not None,
            has_approved_covering_blur=covering_blur,
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
            pick.status = CurationPickStatus.QUEUED.value
            queued.append(existing)
            continue
        item = PublishingQueueItem(
            image_id=image.id,
            crop_id=approved.id if approved else None,
            caption_id=pick.preferred_caption_id,
            state=QueueState.CANDIDATE.value,
        )
        session.add(item)
        pick.status = CurationPickStatus.QUEUED.value
        queued.append(item)
    session.commit()
    return queued
