from __future__ import annotations

import base64
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, RedirectResponse
from instamagic.api.deps import db_dep, require_lan_session, require_session, settings_dep
from instamagic.auth import (
    COOKIE_NAME,
    OAUTH_NONCE_COOKIE,
    check_password,
    clear_login_failures,
    client_ip,
    login_is_blocked,
    record_login_failure,
    set_session_cookie,
)
from instamagic.config import ATTESTATION_STATEMENT, Settings, get_settings
from instamagic.errors import GeminiBlocked, NotAllowed, NotConfigured, NotFound
from instamagic.models import (
    AdultAttestation,
    BlurCover,
    Caption,
    CaptionStatus,
    CommentDraft,
    CommentDraftStatus,
    ContentTag,
    CropSuggestion,
    CurationBatch,
    CurationPick,
    CurationPickStatus,
    GeminiRequest,
    HumanRating,
    Image,
    ImageTag,
    InstagramInboxComment,
    InstagramPost,
    JobStatus,
    Library,
    ProcessingJob,
    PublishAttempt,
    PublishingQueueItem,
    QueueState,
    RelatedAccount,
    RelatedAccountDiscovery,
    SafetyState,
    TagSource,
)
from instamagic.models.enums import CurationBatchStatus, JobKind
from instamagic.schemas.dto import (
    AttestIn,
    BlurCreateIn,
    BlurOut,
    BlurStrengthIn,
    BlurSuggestIn,
    BlurUpdateIn,
    CandidateBulkIn,
    CandidateBulkItemResult,
    CandidateBulkQueueResult,
    CandidateOut,
    CandidatePage,
    CaptionIn,
    CaptionUpdate,
    CarouselQueueIn,
    CarouselReorderIn,
    CommentDraftIn,
    CommentDraftUpdate,
    CropApproveIn,
    CropOut,
    CurationIn,
    CurationPickUpdate,
    CurationQueueIn,
    CurationReorderIn,
    EnrichmentSettingsIn,
    FolderListing,
    GeminiLabelIn,
    HashtagIn,
    HealthStatusOut,
    ImageOut,
    ImagePage,
    ImagePublishReadinessOut,
    InstagramCredentialsIn,
    InstagramOAuthSelectIn,
    JobOut,
    LibraryIn,
    LoginIn,
    MetaAppCredentialsIn,
    PostingScheduleConfigIn,
    PublishIn,
    QueueIn,
    QueuesOut,
    QueueStoryUpdateIn,
    RatingIn,
    RelatedAccountIn,
    ScheduleIn,
    TagIn,
)
from instamagic.services.blurs.coverage_report import blur_coverage_report
from instamagic.services.blurs.coverage_settings import blur_coverage_settings
from instamagic.services.blurs.detector_probe import probe_blurred_detector
from instamagic.services.blurs.gate_context import publishing_gate_kwargs
from instamagic.services.blurs.generator import (
    BlurWindow,
    covers_unsafe_boxes,
    manual_blur,
    with_strength,
)
from instamagic.services.blurs.rating_gate import (
    BLUR_NOT_VERIFIED_GEMINI,
    BLUR_VERIFY_FIRST,
    demote_if_unverified,
    fresh_blur_gate_passes,
    rating_blocked_reason,
)
from instamagic.services.blurs.unlock import (
    apply_unlock_result,
    blur_covers_verified,
    blur_gate_passes,
    requires_blur_verification,
    reset_blur_unlock,
    unlock_summary_message,
    verify_blur_unlock,
)
from instamagic.services.blurs.version import blur_render_version, file_stamp
from instamagic.services.crops.generator import validate_crop_window, window_excludes_unsafe
from instamagic.services.factory import build_instagram
from instamagic.services.gemini.context import build_context, gemini_allows_pixels
from instamagic.services.gemini.growth import append_voice_to_context
from instamagic.services.geometry import Box
from instamagic.services.health_status import build_health_status
from instamagic.services.images.derivatives import (
    PREVIEW_FILENAME,
    THUMB_FILENAME,
    oriented_rgb,
    read_image,
)
from instamagic.services.images.folders import escape_like, list_folders, normalize_folder
from instamagic.services.images.paths import (
    assert_library_root,
    assert_library_root_mounted,
    safe_join,
)
from instamagic.services.images.presentation import (
    effective_state,
    effective_state_filter_clause,
    normalize_human_rating_filter,
    publish_variant,
    publish_variant_label,
)
from instamagic.services.instagram.assets import parse_publish_token, resolve_publish_file
from instamagic.services.instagram.business_discovery import (
    discovery_out,
    sync_related_account_discovery,
)
from instamagic.services.instagram.comment_inbox import (
    post_reply,
    suggest_reply,
    sync_inbox_comments,
)
from instamagic.services.instagram.hashtag_research import (
    HashtagResearchUnavailable,
    budget_status,
    research_hashtag,
)
from instamagic.services.instagram.insights_sync import (
    refresh_token_scopes,
    sync_instagram_insights,
)
from instamagic.services.instagram.oauth import (
    build_authorize_url,
    complete_oauth_with_page,
    exchange_code_for_credentials_with_pages,
    oauth_available,
    oauth_redirect_uri,
    open_oauth_pending,
    seal_oauth_pending,
    verify_oauth_state,
)
from instamagic.services.instagram.publish import publish_queue_item
from instamagic.services.instagram.publish_derivative import (
    blur_drawn_not_approved_reason,
    ensure_publish_derivative,
)
from instamagic.services.instagram.store import (
    clear_credentials,
    clear_meta_app_credentials,
    connection_status_fields,
    resolve_meta_app_credentials,
    save_account_profile,
    save_credentials,
    save_meta_app_credentials,
)
from instamagic.services.pipeline import (
    clear_exposed_boxes,
    duplicate_count,
    is_attested,
    rate_image,
    recompute_model_states,
    render_blur_derivative,
    replace_suggested_blurs,
    run_scan,
)
from instamagic.services.posting.carousel_queue import (
    create_carousel_queue_item,
    reorder_carousel_slides,
    slides_for_item,
)
from instamagic.services.posting.hashtags import clean_tags as clean_hashtags
from instamagic.services.posting.queue_schedule import (
    fill_slots_from_approved,
    list_upcoming,
    schedule_queue_item,
    unschedule_queue_item,
)
from instamagic.services.posting.readiness import build_image_publish_readiness
from instamagic.services.posting.schedule_config import config_as_dict, config_view, update_config
from instamagic.services.posting.schedule_config import (
    get_or_create_config as get_or_create_schedule_config,
)
from instamagic.services.queues import QUEUE_GEMINI, QUEUE_IO, snapshot_queues
from instamagic.services.recommendations.candidates_bulk import bulk_enqueue, bulk_reject
from instamagic.services.recommendations.curate import (
    active_curation_in_flight,
    collect_eligible,
    queue_picks,
    reconcile_stale_curation_batches,
    run_curation,
    undo_queued_pick,
)
from instamagic.services.recommendations.enrichment import (
    enrichment_status,
    stored_hashtags,
    wake_enrichment,
)
from instamagic.services.recommendations.enrichment import (
    generate_hashtags as generate_enrichment_hashtags,
)
from instamagic.services.recommendations.enrichment import retry_errors as retry_enrichment_errors
from instamagic.services.recommendations.gates import publishing_gate
from instamagic.services.recommendations.gemini_derivatives import (
    curation_derivative_bytes as _curation_derivative_bytes_impl,
)
from instamagic.services.recommendations.gemini_derivatives import (
    derivative_bytes_for_gemini,
)
from instamagic.services.recommendations.gemini_derivatives import (
    has_approved_covering_blur as _has_approved_covering_blur_impl,
)
from instamagic.services.recommendations.label_job import (
    create_gemini_label_job,
    gemini_label_counts,
    should_retry_label,
)
from instamagic.services.recommendations.performance import (
    best_posting_times,
    image_performance_boost,
    insights_dashboard,
    load_performance_signals,
)
from instamagic.services.recommendations.rank import CandidateInput, rank_candidates
from instamagic.services.recommendations.refusals import (
    is_gemini_refused,
    is_provider_refusal,
    mark_gemini_refused,
)
from instamagic.services.recommendations.refusals import reset_breaker as reset_prefetch_breaker
from instamagic.workers.resume import SCAN_TIMEOUT
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session, selectinload

router = APIRouter()


def _enrich_redis(settings: Settings):
    """Redis for enrichment counters/locks; None in inline mode or when Redis is unreachable."""
    if settings.pipeline_mode == "inline":
        return None
    try:
        from redis import Redis

        connection = Redis.from_url(settings.redis_url, socket_connect_timeout=2, socket_timeout=2)
        connection.ping()
        return connection
    except Exception:
        return None


def _wake_enrichment(settings: Settings) -> None:
    if settings.pipeline_mode != "inline":
        wake_enrichment(settings.redis_url)


def _enqueue_worker_job(
    settings: Settings,
    func_name: str,
    processing_job_id: str,
    *,
    job_timeout: int,
    queue_name: str = QUEUE_IO,
) -> None:
    if settings.pipeline_mode == "inline":
        from instamagic.workers.tasks import run_curation_job, run_gemini_label_job_entry

        if func_name.endswith("run_curation_job"):
            run_curation_job(processing_job_id)
        elif func_name.endswith("run_gemini_label_job_entry"):
            run_gemini_label_job_entry(processing_job_id)
        return
    try:
        from redis import Redis
        from rq import Queue

        Queue(queue_name, connection=Redis.from_url(settings.redis_url)).enqueue(
            func_name,
            processing_job_id,
            job_timeout=job_timeout,
        )
    except Exception as exc:
        raise NotConfigured("Could not reach the worker queue.") from exc


def _enqueue_gemini_label_work(
    session: Session,
    settings: Settings,
    *,
    library_id: UUID | None,
    image_ids: list[UUID] | None,
    all_eligible: bool,
    existing_job: ProcessingJob | None = None,
) -> ProcessingJob:
    job = existing_job or create_gemini_label_job(
        session,
        library_id=library_id,
        image_ids=image_ids,
        all_eligible=all_eligible,
    )
    _enqueue_worker_job(
        settings,
        "instamagic.workers.tasks.run_gemini_label_job_entry",
        str(job.id),
        job_timeout=60 * 60 * 6,
        queue_name=QUEUE_GEMINI,
    )
    return job


def _image_out(session: Session, image: Image, *, settings: Settings | None = None) -> ImageOut:
    approved_queue = next(iter(image.queue_items), None) if image.queue_items else None
    has_approved_crop = any(crop.approved for crop in image.crops) if image.crops is not None else False
    has_approved_blur = any(blur.approved for blur in image.blurs) if image.blurs is not None else False
    covering_blur = _has_approved_covering_blur(session, image)
    original_preview = (
        f"/api/derivatives/{image.id}/preview" if image.preview_path else None
    )
    use_blurred_previews = has_approved_blur or (
        image.model_state == SafetyState.DO_NOT_POST.value and bool(image.blurs)
    )
    blur_version = _image_blur_version(image) if image.blurs else None
    approved_crop = next((crop for crop in image.crops if crop.approved), None) if image.crops else None
    crop_key = (
        f"{approved_crop.id}:{approved_crop.x},{approved_crop.y},{approved_crop.width},{approved_crop.height}"
        if approved_crop
        else "-"
    )
    publish_v = hashlib.sha1(f"{blur_version or '-'}|{crop_key}".encode()).hexdigest()[:12]
    if use_blurred_previews:
        thumb_v = _image_blur_version(image, "blurs/thumb.jpg")
        thumbnail_url = f"/api/derivatives/{image.id}/blurred-thumbnail?v={thumb_v}"
        preview_url = f"/api/derivatives/{image.id}/blurred-preview?v={blur_version}"
        publish_preview_url = f"/api/images/{image.id}/publish-preview?v={publish_v}"
    else:
        thumbnail_url = f"/api/derivatives/{image.id}/thumbnail" if image.thumbnail_path else None
        preview_url = original_preview
        publish_preview_url = (
            f"/api/images/{image.id}/publish-preview?v={publish_v}"
            if image.preview_path or has_approved_crop
            else None
        )
    variant = publish_variant(
        image.human_rating,
        has_approved_crop=has_approved_crop,
        has_approved_blur=has_approved_blur,
    )
    variant_label = publish_variant_label(variant)
    unlock_message = unlock_summary_message(session, image)
    if unlock_message and unlock_message.startswith("SAFE"):
        variant_label = unlock_message
    highlights: list[dict[str, object]] = []
    if image.blur_unlock_details_json:
        try:
            details = json.loads(image.blur_unlock_details_json)
            for row in details.get("uncovered_regions") or []:
                highlights.append({**row, "kind": "uncovered"})
            for row in details.get("classifier_detections") or []:
                highlights.append({**row, "kind": "detection"})
        except json.JSONDecodeError:
            pass
    return ImageOut(
        id=image.id,
        filename=image.filename,
        relative_path=image.relative_path,
        width=image.width,
        height=image.height,
        orientation=image.orientation,
        captured_at=image.captured_at,
        ingested_at=image.ingested_at,
        camera=image.camera,
        lens=image.lens,
        focal_length_mm=image.focal_length_mm,
        iso=image.iso,
        shutter=image.shutter,
        aperture=image.aperture,
        has_gps=image.has_gps,
        session_name=image.session_name,
        model_state=image.model_state,
        human_rating=image.human_rating,
        effective_state=effective_state(
            image.human_rating,
            image.model_state,
            has_approved_crop=has_approved_crop,
            has_approved_covering_blur=has_approved_blur,
        ),
        aesthetic_score=image.score.aesthetic_score if image.score else None,
        queued=bool(image.queue_items),
        queue_state=approved_queue.state if approved_queue else None,
        duplicate_count=duplicate_count(session, image.id),
        has_approved_crop=has_approved_crop,
        has_approved_covering_blur=covering_blur,
        has_approved_blur=has_approved_blur,
        publish_variant=variant,
        publish_variant_label=variant_label,
        blur_unlock_status=image.blur_unlock_status,
        blur_unlock_message=unlock_message,
        blur_verification_required=requires_blur_verification(image),
        blur_verified=requires_blur_verification(image) and blur_covers_verified(session, image),
        blur_unlock_highlights=highlights,
        thumbnail_url=thumbnail_url,
        preview_url=preview_url,
        original_preview_url=original_preview,
        publish_preview_url=publish_preview_url,
        gemini_label_status=image.gemini_label_status,
        gemini_label_at=image.gemini_label_at,
        gemini_description=image.gemini_description,
        gemini_mood=image.gemini_mood,
        enrich_hashtags=stored_hashtags(image),
        gemini_refused=is_gemini_refused(image),
        gemini_refused_reason=image.gemini_refused_reason,
    )


def _load_image(session: Session, image_id: UUID) -> Image:
    image = session.scalar(
        select(Image)
        .where(Image.id == image_id)
        .options(
            selectinload(Image.score),
            selectinload(Image.crops),
            selectinload(Image.blurs),
            selectinload(Image.queue_items),
        )
    )
    if image is None or image.skip_reason is not None:
        raise NotFound("Image was not found.")
    return image


@router.get("/health")
def health(settings: Settings = Depends(settings_dep)) -> dict[str, str]:
    return {"status": "ok", "version": settings.app_version}


@router.get("/api/health/status", response_model=HealthStatusOut)
def health_status(
    _auth: None = Depends(require_session),
    settings: Settings = Depends(settings_dep),
) -> dict[str, object]:
    """Operator health summary (session required). Never returns secrets or tokens."""
    return build_health_status(settings)


@router.get("/api/status")
def app_status(
    _auth: None = Depends(require_session),
    settings: Settings = Depends(settings_dep),
    session: Session = db_dep,
) -> dict[str, object]:
    """Read-only operator dashboard snapshot (session required, cached ~15s). No secrets."""
    from instamagic.services.app_status import build_app_status

    return build_app_status(settings, session)


@router.get("/ready")
def ready(session: Session = db_dep) -> dict[str, str]:
    session.execute(select(1))
    return {"status": "ready"}


@router.post("/api/auth/login")
def login(body: LoginIn, request: Request, response: Response, settings: Settings = Depends(settings_dep)) -> dict[str, bool]:
    ip = client_ip(request)
    failures: dict[str, list[float]] = request.app.state.login_failures
    if login_is_blocked(failures, ip):
        raise HTTPException(status_code=429, detail="Too many login attempts.")
    if not check_password(settings, body.password):
        record_login_failure(failures, ip)
        raise HTTPException(status_code=401, detail="Incorrect password.")
    clear_login_failures(failures, ip)
    set_session_cookie(response, settings)
    return {"authenticated": True}


@router.post("/api/auth/logout")
def logout(response: Response, settings: Settings = Depends(settings_dep)) -> dict[str, bool]:
    response.delete_cookie(
        COOKIE_NAME,
        path="/",
        httponly=True,
        samesite="lax",
        secure=settings.session_cookie_secure(),
    )
    return {"authenticated": False}


@router.get("/api/auth/session")
def auth_session(_auth: None = Depends(require_session)) -> dict[str, bool]:
    return {"authenticated": True}


@router.get("/api/config")
def config(request: Request, _auth: None = Depends(require_session), settings: Settings = Depends(settings_dep)) -> dict[str, object]:
    classifier = request.app.state.classifier
    ig = getattr(request.app.state, "instagram", None)
    return {
        "version": settings.app_version,
        "gemini_policy": settings.gemini_image_policy,
        "gemini_pixels_allowed": gemini_allows_pixels(settings.gemini_image_policy),
        "gemini_configured": settings.gemini_provider == "fake" or bool(settings.gemini_api_key),
        "auto_publish": settings.auto_publish,
        "instagram_configured": bool(ig.configured()) if ig is not None else settings.instagram_configured(),
        "instagram_provider": settings.instagram_provider,
        "account_voice": settings.account_voice,
        "detector_available": bool(classifier.available()),
        "suggested_library_root": settings.suggested_library_root,
        "attestation_statement": ATTESTATION_STATEMENT,
        "safety_classifier": settings.safety_classifier,
    }


@router.get("/api/libraries")
def list_libraries(_auth: None = Depends(require_session), session: Session = db_dep) -> list[dict[str, object]]:
    rows = session.scalars(select(Library).order_by(Library.created_at.desc())).all()
    payload = []
    for library in rows:
        count = session.scalar(
            select(func.count()).select_from(Image).where(Image.library_id == library.id, Image.skip_reason.is_(None))
        )
        payload.append(
            {
                "id": library.id,
                "name": library.name,
                "root_path": library.root_path,
                "attested": is_attested(session, library.id),
                "image_count": int(count or 0),
            }
        )
    return payload


@router.post("/api/libraries", status_code=201)
def create_library(
    body: LibraryIn,
    _auth: None = Depends(require_session),
    session: Session = db_dep,
    settings: Settings = Depends(settings_dep),
) -> dict[str, object]:
    try:
        if settings.require_external_library_mounts:
            root = assert_library_root_mounted(body.root_path, settings.library_roots())
        else:
            root = assert_library_root(body.root_path, settings.library_roots())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    library = Library(name=body.name.strip(), root_path=str(root))
    session.add(library)
    session.flush()
    if body.confirmed:
        if body.statement.strip() != ATTESTATION_STATEMENT:
            raise HTTPException(status_code=400, detail="Attestation statement does not match.")
        session.add(
            AdultAttestation(
                library_id=library.id,
                confirmed=True,
                statement=ATTESTATION_STATEMENT,
            )
        )
    session.commit()
    return {"id": library.id, "name": library.name, "root_path": library.root_path, "attested": body.confirmed}


@router.post("/api/libraries/{library_id}/attest")
def attest_library(
    library_id: UUID,
    body: AttestIn,
    _auth: None = Depends(require_session),
    session: Session = db_dep,
) -> dict[str, bool]:
    library = session.get(Library, library_id)
    if library is None:
        raise NotFound("Library was not found.")
    if not body.confirmed or body.statement.strip() != ATTESTATION_STATEMENT:
        raise HTTPException(status_code=400, detail="Attestation statement does not match.")
    session.add(AdultAttestation(library_id=library.id, confirmed=True, statement=ATTESTATION_STATEMENT))
    session.commit()
    recompute_model_states(session, library.id)
    return {"attested": True}


@router.post("/api/libraries/{library_id}/scan", status_code=202)
def scan_library(
    library_id: UUID,
    request: Request,
    _auth: None = Depends(require_session),
    session: Session = db_dep,
    settings: Settings = Depends(settings_dep),
) -> dict[str, str]:
    library = session.get(Library, library_id)
    if library is None:
        raise NotFound("Library was not found.")
    if settings.require_external_library_mounts:
        try:
            assert_library_root_mounted(library.root_path, settings.library_roots())
        except ValueError as exc:
            raise NotConfigured(str(exc)) from None
    active = session.scalar(
        select(ProcessingJob)
        .where(
            ProcessingJob.library_id == library.id,
            ProcessingJob.kind == "scan",
            ProcessingJob.status.in_((JobStatus.QUEUED.value, JobStatus.RUNNING.value)),
        )
        .order_by(ProcessingJob.created_at.desc())
    )
    if active is not None:
        return {"job_id": str(active.id), "status": active.status}
    job = ProcessingJob(kind="scan", status=JobStatus.QUEUED.value, library_id=library.id, detail="scan queued")
    session.add(job)
    session.commit()
    if settings.pipeline_mode == "inline":
        run_scan(
            session,
            library.id,
            settings.derivatives_root,
            request.app.state.classifier,
            request.app.state.face_detector,
            job.id,
        )
        return {"job_id": str(job.id), "status": job.status}
    try:
        from redis import Redis
        from rq import Queue

        Queue("io", connection=Redis.from_url(settings.redis_url)).enqueue(
            "instamagic.workers.tasks.run_scan_job",
            str(library.id),
            str(job.id),
            job_timeout=SCAN_TIMEOUT,
        )
    except Exception as exc:
        job.status = JobStatus.FAILED.value
        job.error = "Could not reach the worker queue."
        job.detail = "Could not reach the worker queue."
        job.finished_at = datetime.now(UTC)
        session.commit()
        raise NotConfigured("Could not reach the worker queue.") from exc
    return {"job_id": str(job.id), "status": JobStatus.QUEUED.value}


def _library_classification_counts(session: Session, library_id: UUID | None) -> tuple[int, int]:
    if library_id is None:
        return 0, 0
    classified = int(
        session.scalar(
            select(func.count())
            .select_from(Image)
            .where(Image.library_id == library_id, Image.skip_reason.is_(None), Image.model_state.is_not(None))
        )
        or 0
    )
    visible = int(
        session.scalar(
            select(func.count()).select_from(Image).where(Image.library_id == library_id, Image.skip_reason.is_(None))
        )
        or 0
    )
    return classified, max(0, visible - classified)


def _job_out(session: Session, job: ProcessingJob) -> JobOut:
    classified, pending = _library_classification_counts(session, job.library_id)
    return JobOut(
        id=job.id,
        kind=job.kind,
        status=job.status,
        detail=job.detail,
        error=job.error,
        library_id=job.library_id,
        files_seen=job.files_seen or 0,
        files_processed=job.files_processed or 0,
        files_skipped=job.files_skipped or 0,
        files_failed=job.files_failed or 0,
        files_total=job.files_total,
        current_path=job.current_path,
        current_stage=job.current_stage,
        classified_count=classified,
        pending_count=pending,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
    )


def _folder_or_400(raw: str | None) -> str:
    try:
        return normalize_folder(raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


@router.get("/api/libraries/{library_id}/folders", response_model=FolderListing)
def library_folders(
    library_id: UUID,
    path: str | None = Query(default=None, max_length=1024),
    _auth: None = Depends(require_session),
    session: Session = db_dep,
) -> FolderListing:
    library = session.get(Library, library_id)
    if library is None:
        raise NotFound("Library was not found.")
    return list_folders(session, library.id, Path(library.root_path), _folder_or_400(path))


@router.get("/api/jobs", response_model=list[JobOut])
def list_jobs(
    library_id: UUID | None = None,
    _auth: None = Depends(require_session),
    session: Session = db_dep,
) -> list[JobOut]:
    stmt = select(ProcessingJob).order_by(ProcessingJob.created_at.desc()).limit(20)
    if library_id:
        stmt = stmt.where(ProcessingJob.library_id == library_id)
    jobs = session.scalars(stmt).all()
    return [_job_out(session, job) for job in jobs]


@router.get("/api/jobs/{job_id}", response_model=JobOut)
def get_job(
    job_id: UUID,
    _auth: None = Depends(require_session),
    session: Session = db_dep,
) -> JobOut:
    job = session.get(ProcessingJob, job_id)
    if job is None:
        raise NotFound("Job was not found.")
    return _job_out(session, job)


@router.get("/api/queues", response_model=QueuesOut)
def list_queues(_auth: None = Depends(require_session), settings: Settings = Depends(settings_dep)) -> QueuesOut:
    return snapshot_queues(settings.redis_url)


@router.get("/api/facets")
def facets(library_id: UUID | None = None, _auth: None = Depends(require_session), session: Session = db_dep) -> dict[str, list[str]]:
    def values(column):  # type: ignore[no-untyped-def]
        stmt = select(column).where(column.is_not(None), Image.skip_reason.is_(None)).distinct()
        if library_id:
            stmt = stmt.where(Image.library_id == library_id)
        return sorted(value for value in session.scalars(stmt).all() if value)

    return {
        "cameras": values(Image.camera),
        "sessions": values(Image.session_name),
        "orientations": values(Image.orientation),
    }


@router.get("/api/images", response_model=ImagePage)
def list_images(
    library_id: UUID | None = None,
    model_state: str | None = None,
    human_rating: str | None = None,
    unreviewed: bool = False,
    session_name: str | None = None,
    camera: str | None = None,
    orientation: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    queued: bool | None = None,
    posted: bool | None = None,
    folder: str | None = Query(default=None, max_length=1024),
    recursive: bool = False,
    cursor: str | None = None,
    limit: int = Query(default=60, ge=1, le=200),
    _auth: None = Depends(require_session),
    session: Session = db_dep,
) -> ImagePage:
    stmt = (
        select(Image)
        .where(Image.skip_reason.is_(None))
        .options(
            selectinload(Image.score),
            selectinload(Image.queue_items),
            selectinload(Image.crops),
            selectinload(Image.blurs),
        )
        .order_by(Image.ingested_at.desc(), Image.id.desc())
    )
    if library_id:
        stmt = stmt.where(Image.library_id == library_id)
    if model_state:
        # Match the Review badge (effective_state), not raw classifier output alone.
        stmt = stmt.where(effective_state_filter_clause(Image, model_state))
    if human_rating:
        normalized_rating = normalize_human_rating_filter(human_rating)
        if normalized_rating is None:
            raise HTTPException(
                status_code=400,
                detail="human_rating must be reject, review, safe, or excellent.",
            )
        stmt = stmt.where(Image.human_rating == normalized_rating)
    if unreviewed:
        stmt = stmt.where(Image.human_rating.is_(None))
    if session_name is not None:
        stmt = stmt.where(Image.session_name == session_name)
    if folder is not None:
        normalized = _folder_or_400(folder)
        if not recursive:
            stmt = stmt.where(Image.session_name == normalized)
        elif normalized:
            stmt = stmt.where(
                or_(
                    Image.session_name == normalized,
                    Image.session_name.like(f"{escape_like(normalized)}/%", escape="\\"),
                )
            )
    if camera:
        stmt = stmt.where(Image.camera == camera)
    if orientation:
        stmt = stmt.where(Image.orientation == orientation)
    if date_from:
        stmt = stmt.where(Image.captured_at >= date_from)
    if date_to:
        stmt = stmt.where(Image.captured_at <= date_to)
    if queued is True:
        stmt = stmt.where(Image.id.in_(select(PublishingQueueItem.image_id)))
    elif queued is False:
        stmt = stmt.where(Image.id.not_in(select(PublishingQueueItem.image_id)))
    if posted is True:
        stmt = stmt.where(Image.id.in_(select(InstagramPost.image_id)))
    elif posted is False:
        stmt = stmt.where(Image.id.not_in(select(InstagramPost.image_id)))
    if cursor:
        stamp, image_id = _decode_cursor(cursor)
        stmt = stmt.where(
            or_(
                Image.ingested_at < stamp,
                and_(Image.ingested_at == stamp, Image.id < image_id),
            )
        )
    rows = session.scalars(stmt.limit(limit + 1)).all()
    page = rows[:limit]
    next_cursor = _encode_cursor(page[-1]) if len(rows) > limit and page else None
    return ImagePage(items=[_image_out(session, image) for image in page], next_cursor=next_cursor)


@router.get("/api/images/{image_id}", response_model=ImageOut)
def get_image(image_id: UUID, _auth: None = Depends(require_session), session: Session = db_dep) -> ImageOut:
    return _image_out(session, _load_image(session, image_id))


@router.get("/api/images/{image_id}/publish-readiness", response_model=ImagePublishReadinessOut)
def get_image_publish_readiness(
    image_id: UUID,
    request: Request,
    _auth: None = Depends(require_session),
    session: Session = db_dep,
    settings: Settings = Depends(settings_dep),
) -> ImagePublishReadinessOut:
    image = _load_image(session, image_id)
    _reload_instagram_client(request, settings, session)
    client = request.app.state.instagram
    configured = bool(client.configured()) or settings.instagram_provider == "fake"
    payload = build_image_publish_readiness(
        session,
        settings,
        image,
        instagram_configured=configured,
    )
    presented = _image_out(session, image)
    payload["feed_preview_url"] = presented.preview_url or presented.thumbnail_url
    return ImagePublishReadinessOut(**payload)


@router.post("/api/images/{image_id}/rating", response_model=ImageOut)
def rate(
    image_id: UUID,
    body: RatingIn,
    request: Request,
    _auth: None = Depends(require_session),
    session: Session = db_dep,
    settings: Settings = Depends(settings_dep),
) -> ImageOut:
    image = _load_image(session, image_id)
    try:
        rating = body.normalized()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    blocked = rating_blocked_reason(session, image, rating)
    if blocked:
        raise NotAllowed(blocked)
    rate_image(session, image, rating)
    session.refresh(image)
    if rating in {HumanRating.SAFE.value, HumanRating.EXCELLENT.value} and should_retry_label(
        image.gemini_label_status, image.gemini_label_attempts
    ):
        _enqueue_gemini_label_work(
            session,
            settings,
            library_id=image.library_id,
            image_ids=[image.id],
            all_eligible=False,
        )
    if rating in {HumanRating.SAFE.value, HumanRating.EXCELLENT.value}:
        _wake_enrichment(settings)
    return _image_out(session, _load_image(session, image_id))


@router.get("/api/images/{image_id}/crops", response_model=list[CropOut])
def list_crops(image_id: UUID, _auth: None = Depends(require_session), session: Session = db_dep) -> list[CropOut]:
    image = _load_image(session, image_id)
    return [_crop_out(image, crop) for crop in image.crops]


@router.post("/api/images/{image_id}/crops/{crop_id}/approve", response_model=CropOut)
def approve_crop(
    image_id: UUID,
    crop_id: UUID,
    body: CropApproveIn = CropApproveIn(),
    _auth: None = Depends(require_session),
    session: Session = db_dep,
    settings: Settings = Depends(settings_dep),
) -> CropOut:
    image = _load_image(session, image_id)
    crop = next((item for item in image.crops if item.id == crop_id), None)
    if crop is None:
        raise NotFound("Crop was not found.")
    if image.model_state == SafetyState.DO_NOT_POST.value:
        raise NotAllowed("Crop approval cannot override DO_NOT_POST. Rate the image instead.")
    try:
        adjusted = body.adjusted()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if adjusted is not None:
        if image.width is None or image.height is None:
            raise NotAllowed("Image dimensions are unknown.")
        try:
            window = validate_crop_window(
                image_width=image.width,
                image_height=image.height,
                x=adjusted[0],
                y=adjusted[1],
                width=adjusted[2],
                height=adjusted[3],
                aspect_ratio=crop.aspect_ratio,
                reference_width=crop.width,
                reference_height=crop.height,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        crop.x = window.x
        crop.y = window.y
        crop.width = window.width
        crop.height = window.height
        unsafe = clear_exposed_boxes(session, image.id)
        crop.excludes_unsafe = window_excludes_unsafe(window, unsafe)
        if crop.aspect_ratio != "original":
            _refresh_crop_derivative(session, settings, image, crop)
        else:
            crop.derivative_path = None
    if not crop.excludes_unsafe:
        raise NotAllowed("This crop still includes a detected unsafe region.")
    for item in image.crops:
        item.approved = item.id == crop.id
    for blur in image.blurs:
        blur.approved = False
    if image.blurs:
        _blur_changed(session, image, reason="crop_approved_blurs_unapproved")
    session.commit()
    return _crop_out(image, crop)


def _queue_item_images(session: Session, item: PublishingQueueItem) -> list[Image]:
    images = [item.image] if item.image is not None else []
    for slide in getattr(item, "slides", []) or []:
        if slide.image is not None and slide.image not in images:
            images.append(slide.image)
    return images


def _blur_changed(session: Session, image: Image, *, reason: str) -> None:
    """Any blur edit invalidates verification; a safe/excellent rating drops back to review."""
    reset_blur_unlock(image)
    session.flush()
    demote_if_unverified(session, image, reason=reason)


@router.get("/api/images/{image_id}/blurs", response_model=list[BlurOut])
def list_blurs(
    image_id: UUID,
    _auth: None = Depends(require_session),
    session: Session = db_dep,
    settings: Settings = Depends(settings_dep),
) -> list[BlurOut]:
    image = _load_image(session, image_id)
    return [_blur_out(image, blur, settings) for blur in image.blurs]


@router.post("/api/images/{image_id}/blurs", response_model=BlurOut, status_code=201)
def create_blur(
    image_id: UUID,
    body: BlurCreateIn,
    _auth: None = Depends(require_session),
    session: Session = db_dep,
    settings: Settings = Depends(settings_dep),
) -> BlurOut:
    image = _load_image(session, image_id)
    if image.width is None or image.height is None:
        raise NotAllowed("Image dimensions are unknown.")
    try:
        window = manual_blur(
            image.width,
            image.height,
            Box(body.x, body.y, body.width, body.height),
            strength=body.strength,
            reason=body.reason.strip() or "Manual ellipse cover.",
            shape=body.shape,
            rotation_deg=body.rotation_deg,
            corner_radius=body.corner_radius,
            edge_mode=body.edge_mode,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    for blur in image.blurs:
        blur.approved = False
    row = BlurCover(
        image_id=image.id,
        x=window.x,
        y=window.y,
        width=window.width,
        height=window.height,
        shape=window.shape,
        rotation_deg=window.rotation_deg,
        corner_radius=window.corner_radius,
        feather=window.feather,
        edge_mode=window.edge_mode,
        radius=window.radius,
        strength=window.strength,
        reason=window.reason,
        covers_unsafe=False,
        approved=False,
    )
    session.add(row)
    _reload_blurs(session, image)
    _refresh_blur_derivative(session, settings, image)
    _blur_changed(session, image, reason="blur_edit")
    session.commit()
    session.refresh(row)
    return _blur_out(image, row, settings)


@router.post("/api/images/{image_id}/blurs/suggest", response_model=list[BlurOut])
def suggest_blur_covers(
    image_id: UUID,
    body: BlurSuggestIn,
    _auth: None = Depends(require_session),
    session: Session = db_dep,
    settings: Settings = Depends(settings_dep),
) -> list[BlurOut]:
    """Opt-in: build ellipse covers from stored local-model unsafe boxes (not auto on scan)."""
    image = _load_image(session, image_id)
    if image.width is None or image.height is None:
        raise NotAllowed("Image dimensions are unknown.")
    library = session.get(Library, image.library_id)
    if library is None:
        raise NotFound("Library not found.")
    try:
        rgb = oriented_rgb(read_image(safe_join(Path(library.root_path), image.relative_path)))
        rows = replace_suggested_blurs(
            session,
            image=image,
            rgb=rgb,
            derivatives_root=Path(settings.derivatives_root),
            strength=body.strength,
        )
    except ValueError as exc:
        raise NotAllowed(str(exc)) from exc
    # Old covers were bulk-deleted: reload the collection and re-render so the cover
    # and the grid thumbnail both reflect exactly the new set.
    _reload_blurs(session, image)
    _refresh_blur_derivative(session, settings, image)
    for crop in image.crops:
        crop.approved = False
    _blur_changed(session, image, reason="blur_edit")
    session.commit()
    return [_blur_out(image, row, settings) for row in rows]


@router.post("/api/images/{image_id}/blurs/strength", response_model=list[BlurOut])
def set_blur_strength(
    image_id: UUID,
    body: BlurStrengthIn,
    _auth: None = Depends(require_session),
    session: Session = db_dep,
    settings: Settings = Depends(settings_dep),
) -> list[BlurOut]:
    image = _load_image(session, image_id)
    try:
        for blur in image.blurs:
            updated = with_strength(_window_from_row(blur), body.strength)
            blur.radius = updated.radius
            blur.strength = updated.strength
            blur.approved = False
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _refresh_blur_derivative(session, settings, image)
    _blur_changed(session, image, reason="blur_edit")
    session.commit()
    return [_blur_out(image, blur, settings) for blur in image.blurs]


@router.patch("/api/images/{image_id}/blurs/{blur_id}", response_model=BlurOut)
def update_blur(
    image_id: UUID,
    blur_id: UUID,
    body: BlurUpdateIn,
    _auth: None = Depends(require_session),
    session: Session = db_dep,
    settings: Settings = Depends(settings_dep),
) -> BlurOut:
    from instamagic.services.blurs.validate import validate_blur_geometry

    image = _load_image(session, image_id)
    blur = session.get(BlurCover, blur_id)
    if blur is None or blur.image_id != image.id:
        raise NotFound("Blur cover was not found.")
    if image.width is None or image.height is None:
        raise NotAllowed("Image dimensions are unknown.")
    current = _window_from_row(blur)
    window = BlurWindow(
        x=body.x if body.x is not None else current.x,
        y=body.y if body.y is not None else current.y,
        width=body.width if body.width is not None else current.width,
        height=body.height if body.height is not None else current.height,
        shape=body.shape if body.shape is not None else current.shape,
        feather=current.feather,
        radius=current.radius,
        strength=body.strength if body.strength is not None else current.strength,
        reason=body.reason if body.reason is not None else current.reason,
        covers_unsafe=current.covers_unsafe,
        rotation_deg=body.rotation_deg if body.rotation_deg is not None else current.rotation_deg,
        corner_radius=body.corner_radius if body.corner_radius is not None else current.corner_radius,
        edge_mode=body.edge_mode if body.edge_mode is not None else current.edge_mode,
    )
    if body.edge_mode is not None and body.edge_mode not in {"hard", "soft"}:
        raise HTTPException(status_code=400, detail="edge_mode must be hard or soft.")
    if body.strength is not None and body.strength != current.strength:
        window = with_strength(window, body.strength)
    try:
        validate_blur_geometry(image.width, image.height, window)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    blur.x = window.x
    blur.y = window.y
    blur.width = window.width
    blur.height = window.height
    blur.shape = window.shape
    blur.rotation_deg = window.rotation_deg
    blur.corner_radius = window.corner_radius
    blur.edge_mode = window.edge_mode
    blur.radius = window.radius
    blur.strength = window.strength
    blur.reason = window.reason
    blur.approved = False
    for other in image.blurs:
        if other.id != blur.id:
            other.approved = False
    _refresh_blur_derivative(session, settings, image)
    _blur_changed(session, image, reason="blur_edit")
    session.commit()
    session.refresh(blur)
    return _blur_out(image, blur, settings)


@router.delete("/api/images/{image_id}/blurs/{blur_id}", status_code=204)
def delete_blur(
    image_id: UUID,
    blur_id: UUID,
    _auth: None = Depends(require_session),
    session: Session = db_dep,
    settings: Settings = Depends(settings_dep),
) -> None:
    image = _load_image(session, image_id)
    blur = session.get(BlurCover, blur_id)
    if blur is None or blur.image_id != image.id:
        raise NotFound("Blur cover was not found.")
    session.delete(blur)
    _reload_blurs(session, image)
    _refresh_blur_derivative(session, settings, image)
    _blur_changed(session, image, reason="blur_edit")
    session.commit()


@router.get("/api/images/{image_id}/blur-coverage")
def blur_coverage(
    image_id: UUID,
    _auth: None = Depends(require_session),
    session: Session = db_dep,
) -> dict[str, Any]:
    """Live, read-only coverage of each flagged region by the covers as they are now.

    Same regions, padding and threshold as the unlock gate's coverage check; it never
    changes approval or unlock state.
    """
    image = _load_image(session, image_id)
    return blur_coverage_report(session, image)


@router.post("/api/images/{image_id}/blur-detector-probe")
def blur_detector_probe(
    image_id: UUID,
    request: Request,
    _auth: None = Depends(require_lan_session),
    session: Session = db_dep,
    settings: Settings = Depends(settings_dep),
) -> dict[str, object]:
    """Run the safety classifier on the blurred render for the covers saved now (read-only)."""
    image = _load_image(session, image_id)
    try:
        return probe_blurred_detector(session, settings, image, request.app.state.classifier)
    except ValueError as exc:
        raise NotAllowed(str(exc)) from exc


@router.post("/api/images/{image_id}/blurs/approve", response_model=list[BlurOut])
def approve_blurs(
    image_id: UUID,
    request: Request,
    _auth: None = Depends(require_session),
    session: Session = db_dep,
    settings: Settings = Depends(settings_dep),
) -> list[BlurOut]:
    image = _load_image(session, image_id)
    if not image.blurs:
        raise NotAllowed("No blur covers are available.")
    unsafe = clear_exposed_boxes(session, image.id)
    windows = [_window_from_row(blur) for blur in image.blurs]
    for crop in image.crops:
        crop.approved = False
    for blur in image.blurs:
        blur.approved = True
    session.flush()
    # With "Detector pass overrules coverage" on, the geometric pre-check is advisory: the
    # detector re-check on the blurred render (verify_blur_unlock below) decides.
    strict_coverage = not blur_coverage_settings(session).detector_overrules_coverage
    if image.model_state != SafetyState.DO_NOT_POST.value and strict_coverage:
        if image.width is None or image.height is None:
            raise NotAllowed("Image dimensions are unknown.")
        if unsafe and not covers_unsafe_boxes(windows, unsafe, (image.width, image.height)):
            for blur in image.blurs:
                blur.approved = False
            _blur_changed(session, image, reason="blur_approve_uncovered")
            session.commit()
            raise NotAllowed("These blur covers do not fully cover every detected unsafe region.")
    # Every blurred photo (flagged or not) is verified on the blurred render before it can be
    # rated safe/excellent or sent to Gemini.
    try:
        result = verify_blur_unlock(session, settings, image, request.app.state.classifier)
    except ValueError as exc:
        raise NotAllowed(str(exc)) from exc
    apply_unlock_result(image, result)
    session.flush()
    demote_if_unverified(session, image, reason="blur_verify_failed")
    session.commit()
    return [_blur_out(image, blur, settings) for blur in image.blurs]


_DERIVATIVE_CACHE = {"Cache-Control": "private, max-age=604800, immutable"}
# Blur/crop renders are rewritten in place: only a versioned URL (?v=) may be cached
# long-term; an unversioned request must always revalidate.
_NO_CACHE = {"Cache-Control": "private, no-cache"}


def _render_cache(v: str | None) -> dict[str, str]:
    return _DERIVATIVE_CACHE if v else _NO_CACHE
_GRID_KIND_FILES = {"thumbnail": THUMB_FILENAME, "preview": PREVIEW_FILENAME}
_BLUR_KIND_FILES = {"blurred-preview": "blurs/cover.jpg", "blurred-thumbnail": "blurs/thumb.jpg"}


@router.get("/api/derivatives/{image_id}/{kind}")
def derivative(
    image_id: UUID,
    kind: str,
    v: str | None = Query(default=None, max_length=64),
    _auth: None = Depends(require_session),
    settings: Settings = Depends(settings_dep),
) -> FileResponse:
    """Serve grid thumbs/previews from the stable on-disk layout.

    Contact-sheet loads fire dozens of these in parallel. Skipping the DB
    avoids saturating the SQLAlchemy pool (default size 5) under thumbnail
    stampedes. Paths are fixed under ``{image_id}/`` so path-traversal via a
    poisoned DB column cannot apply here.
    """
    blur_file = _BLUR_KIND_FILES.get(kind)
    if blur_file is not None:
        path = safe_join(settings.derivatives_root, f"{image_id}/{blur_file}")
        if not path.is_file() and kind == "blurred-thumbnail":
            cover = safe_join(settings.derivatives_root, f"{image_id}/blurs/cover.jpg")
            if cover.is_file():
                path = cover
        if not path.is_file():
            raise NotFound("Derivative was not found.")
        return FileResponse(path, media_type="image/jpeg", headers=_render_cache(v))
    filename = _GRID_KIND_FILES.get(kind)
    if filename is None:
        raise NotFound("Derivative was not found.")
    path = safe_join(settings.derivatives_root, f"{image_id}/{filename}")
    if not path.is_file():
        raise NotFound("Derivative was not found.")
    return FileResponse(path, media_type="image/jpeg", headers=_DERIVATIVE_CACHE)


@router.get("/api/images/{image_id}/publish-preview")
def publish_preview(
    image_id: UUID,
    _auth: None = Depends(require_session),
    session: Session = db_dep,
    settings: Settings = Depends(settings_dep),
) -> FileResponse:
    """Exact JPEG bytes that would be uploaded on publish (re-rendered at request time)."""
    image = _load_image(session, image_id)
    approved = next((crop for crop in image.crops if crop.approved), None)
    relative = ensure_publish_derivative(session, settings, image, approved)
    session.commit()
    path = safe_join(settings.derivatives_root, relative)
    if not path.is_file():
        raise NotFound("Publish preview was not found.")
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "private, no-cache"})


@router.get("/api/images/{image_id}/story-preview")
def story_preview(
    image_id: UUID,
    request: Request,
    render_mode: str | None = Query(default=None, max_length=32),
    border_color: str | None = Query(default=None, max_length=16),
    _auth: None = Depends(require_lan_session),
    session: Session = db_dep,
    settings: Settings = Depends(settings_dep),
) -> FileResponse:
    """LAN-only 9:16 story render from the same feed publish derivative (no Graph call)."""
    del request
    from instamagic.services.instagram.publish_derivative import ensure_publish_derivative
    from instamagic.services.instagram.story_image import (
        render_story_from_feed_file,
        write_story_jpeg,
    )
    from instamagic.services.posting.schedule_config import get_or_create_config

    image = _load_image(session, image_id)
    approved = next((crop for crop in image.crops if crop.approved), None)
    relative = ensure_publish_derivative(session, settings, image, approved)
    session.commit()
    feed_path = safe_join(settings.derivatives_root, relative)
    cfg = get_or_create_config(session)
    mode = (render_mode or cfg.story_render_mode_default or "blur_backdrop").strip().lower()
    border = (border_color or cfg.story_border_color_default or "black").strip().lower()
    story_rgb = render_story_from_feed_file(feed_path, mode=mode, border_color=border)
    preview_path = settings.derivatives_root / f"{image.id}/publish/story-preview.jpg"
    preview_path.parent.mkdir(parents=True, exist_ok=True)
    write_story_jpeg(story_rgb, preview_path)
    return FileResponse(preview_path, media_type="image/jpeg", headers={"Cache-Control": "private, no-cache"})


@router.get("/api/derivatives/{image_id}/crops/{crop_id}")
def crop_derivative(
    image_id: UUID,
    crop_id: UUID,
    v: str | None = Query(default=None, max_length=64),
    _auth: None = Depends(require_session),
    session: Session = db_dep,
    settings: Settings = Depends(settings_dep),
) -> FileResponse:
    crop = session.scalar(select(CropSuggestion).where(CropSuggestion.id == crop_id, CropSuggestion.image_id == image_id))
    if crop is None or not crop.derivative_path:
        raise NotFound("Derivative was not found.")
    path = safe_join(settings.derivatives_root, crop.derivative_path)
    if not path.is_file():
        raise NotFound("Derivative was not found.")
    return FileResponse(path, media_type="image/jpeg", headers=_render_cache(v))


@router.get("/api/derivatives/{image_id}/blurs/{blur_id}")
def blur_derivative(
    image_id: UUID,
    blur_id: UUID,
    v: str | None = Query(default=None, max_length=64),
    _auth: None = Depends(require_session),
    session: Session = db_dep,
    settings: Settings = Depends(settings_dep),
) -> FileResponse:
    blur = session.scalar(select(BlurCover).where(BlurCover.id == blur_id, BlurCover.image_id == image_id))
    if blur is None or not blur.derivative_path:
        raise NotFound("Derivative was not found.")
    path = safe_join(settings.derivatives_root, blur.derivative_path)
    if not path.is_file():
        raise NotFound("Derivative was not found.")
    return FileResponse(path, media_type="image/jpeg", headers=_render_cache(v))


@router.get("/api/tags")
def list_tags(_auth: None = Depends(require_session), session: Session = db_dep) -> list[dict[str, object]]:
    tags = session.scalars(select(ContentTag).order_by(ContentTag.name)).all()
    return [{"id": tag.id, "name": tag.name} for tag in tags]


@router.post("/api/tags", status_code=201)
def create_tag(body: TagIn, _auth: None = Depends(require_session), session: Session = db_dep) -> dict[str, object]:
    name = body.name.strip().lower()
    existing = session.scalar(select(ContentTag).where(ContentTag.name == name))
    if existing:
        return {"id": existing.id, "name": existing.name}
    tag = ContentTag(name=name)
    session.add(tag)
    session.commit()
    return {"id": tag.id, "name": tag.name}


@router.post("/api/images/{image_id}/tags")
def add_tag(
    image_id: UUID,
    body: TagIn,
    _auth: None = Depends(require_session),
    session: Session = db_dep,
) -> dict[str, str]:
    image = _load_image(session, image_id)
    name = body.name.strip().lower()
    tag = session.scalar(select(ContentTag).where(ContentTag.name == name))
    if tag is None:
        tag = ContentTag(name=name)
        session.add(tag)
        session.flush()
    exists = session.scalar(select(ImageTag).where(ImageTag.image_id == image.id, ImageTag.tag_id == tag.id))
    if exists is None:
        session.add(ImageTag(image_id=image.id, tag_id=tag.id, source=TagSource.HUMAN.value))
        session.commit()
    return {"name": tag.name}


@router.post("/api/queue/carousel", status_code=201)
def enqueue_carousel(
    body: CarouselQueueIn,
    _auth: None = Depends(require_session),
    session: Session = db_dep,
) -> dict[str, object]:
    item = create_carousel_queue_item(session, body.image_ids)
    return _queue_out(session, item)


@router.put("/api/queue/{item_id}/carousel-order")
def reorder_carousel(
    item_id: UUID,
    body: CarouselReorderIn,
    _auth: None = Depends(require_session),
    session: Session = db_dep,
) -> dict[str, object]:
    item = reorder_carousel_slides(session, item_id, body.image_ids)
    return _queue_out(session, item)


@router.post("/api/queue", status_code=201)
def enqueue(
    body: QueueIn,
    _auth: None = Depends(require_session),
    session: Session = db_dep,
) -> dict[str, object]:
    image = _load_image(session, body.image_id)
    approved = next((crop for crop in image.crops if crop.approved), None)
    gate = publishing_gate(attested=is_attested(session, image.library_id), **publishing_gate_kwargs(session, image))
    blur_pending = blur_drawn_not_approved_reason(image)
    if not gate.allowed or blur_pending:
        reasons = list(gate.reasons)
        if blur_pending:
            reasons.append(blur_pending)
        raise NotAllowed(" ".join(reasons))
    existing = session.scalar(
        select(PublishingQueueItem)
        .where(PublishingQueueItem.image_id == image.id)
        .order_by(PublishingQueueItem.created_at.desc())
    )
    if existing is not None:
        return _queue_out(session, existing)
    item = PublishingQueueItem(
        image_id=image.id,
        crop_id=approved.id if approved else None,
        state=QueueState.CANDIDATE.value,
    )
    from instamagic.services.posting.story_fields import apply_story_fields_from_payload

    apply_story_fields_from_payload(
        session,
        item,
        story_share_enabled=body.story_share_enabled,
        story_render_mode=body.story_render_mode,
        story_border_color=body.story_border_color,
        story_timing_mode=body.story_timing_mode,
        story_delay_hours=body.story_delay_hours,
        is_new=True,
    )
    session.add(item)
    session.commit()
    return _queue_out(session, item)


@router.patch("/api/queue/{item_id}")
def patch_queue_item(
    item_id: UUID,
    body: QueueStoryUpdateIn,
    _auth: None = Depends(require_session),
    session: Session = db_dep,
) -> dict[str, object]:
    item = session.get(PublishingQueueItem, item_id)
    if item is None:
        raise NotFound("Queue item was not found.")
    if item.state == QueueState.PUBLISHING.value:
        raise NotAllowed("Cannot edit story options while publishing.")
    from instamagic.services.posting.story_fields import apply_story_fields_from_payload

    apply_story_fields_from_payload(
        session,
        item,
        story_share_enabled=body.story_share_enabled,
        story_render_mode=body.story_render_mode,
        story_border_color=body.story_border_color,
        story_timing_mode=body.story_timing_mode,
        story_delay_hours=body.story_delay_hours,
        is_new=False,
    )
    session.commit()
    return _queue_out(session, item)


@router.get("/api/queue")
def list_queue(_auth: None = Depends(require_session), session: Session = db_dep) -> list[dict[str, object]]:
    items = session.scalars(select(PublishingQueueItem).order_by(PublishingQueueItem.created_at.desc())).all()
    return [_queue_out(session, item) for item in items]


@router.post("/api/queue/{item_id}/approve")
def approve_queue_item(item_id: UUID, _auth: None = Depends(require_session), session: Session = db_dep) -> dict[str, object]:
    item = session.get(PublishingQueueItem, item_id)
    if item is None:
        raise NotFound("Queue item was not found.")
    if item.state != QueueState.CANDIDATE.value:
        raise NotAllowed("Only a candidate can be approved.")
    for gated in _queue_item_images(session, item):
        if not blur_gate_passes(session, gated):
            raise NotAllowed(BLUR_VERIFY_FIRST)
    item.state = QueueState.APPROVED.value
    item.approved_at = datetime.now(UTC)
    session.commit()
    return _queue_out(session, item)


@router.post("/api/queue/{item_id}/publish")
def publish_queue(
    item_id: UUID,
    body: PublishIn,
    request: Request,
    _auth: None = Depends(require_session),
    session: Session = db_dep,
    settings: Settings = Depends(settings_dep),
) -> dict[str, object]:
    """Human-gated Instagram publish. Re-checks attestation / DO_NOT_POST / crop gates."""
    outcome = publish_queue_item(
        session,
        settings,
        request.app.state.instagram,
        item_id=item_id,
        caption_id=body.caption_id,
    )
    item = session.get(PublishingQueueItem, outcome.queue_item_id)
    assert item is not None
    payload = _queue_out(session, item)
    payload["ig_media_id"] = outcome.ig_media_id
    payload["permalink"] = outcome.permalink
    payload["published_caption"] = outcome.caption
    return payload


@router.get("/api/posting-schedule/config")
def get_posting_schedule_config(_auth: None = Depends(require_session), session: Session = db_dep) -> dict[str, object]:
    return config_as_dict(config_view(session))


@router.put("/api/posting-schedule/config")
def put_posting_schedule_config(
    body: PostingScheduleConfigIn,
    _auth: None = Depends(require_session),
    session: Session = db_dep,
) -> dict[str, object]:
    view = update_config(
        session,
        timezone=body.timezone,
        weekday_slots=body.weekday_slots,
        max_posts_per_day=body.max_posts_per_day,
        min_gap_hours=body.min_gap_hours,
        paused=body.paused,
        story_share_default=body.story_share_default,
        story_render_mode_default=body.story_render_mode_default,
        story_border_color_default=body.story_border_color_default,
        blur_coverage_threshold=body.blur_coverage_threshold,
        blur_region_padding_px=body.blur_region_padding_px,
        blur_detector_overrules_coverage=body.blur_detector_overrules_coverage,
    )
    return config_as_dict(view)


@router.get("/api/posting-schedule/upcoming")
def get_upcoming_scheduled(
    _auth: None = Depends(require_session),
    session: Session = db_dep,
    limit: int = Query(default=50, ge=1, le=200),
) -> list[dict[str, object]]:
    return [_queue_out(session, item) for item in list_upcoming(session, limit=limit)]


@router.post("/api/posting-schedule/fill-slots")
def fill_posting_slots(_auth: None = Depends(require_session), session: Session = db_dep) -> dict[str, int]:
    assigned = fill_slots_from_approved(session)
    return {"assigned": assigned}


@router.post("/api/queue/{item_id}/schedule")
def schedule_item(
    item_id: UUID,
    body: ScheduleIn,
    _auth: None = Depends(require_session),
    session: Session = db_dep,
) -> dict[str, object]:
    item = schedule_queue_item(
        session,
        item_id,
        scheduled_at=body.scheduled_at,
        mode=body.mode.strip().lower(),
    )
    if body.caption_id is not None:
        caption = session.get(Caption, body.caption_id)
        if caption is None or caption.image_id != item.image_id:
            raise NotFound("Caption was not found for this image.")
        item.caption_id = body.caption_id
        session.commit()
        session.refresh(item)
    return _queue_out(session, item)


@router.post("/api/queue/{item_id}/unschedule")
def unschedule_item(
    item_id: UUID,
    _auth: None = Depends(require_session),
    session: Session = db_dep,
) -> dict[str, object]:
    item = unschedule_queue_item(session, item_id)
    return _queue_out(session, item)


@router.post("/api/images/{image_id}/captions", status_code=201)
def create_captions(
    image_id: UUID,
    body: CaptionIn,
    request: Request,
    _auth: None = Depends(require_session),
    session: Session = db_dep,
    settings: Settings = Depends(settings_dep),
) -> list[dict[str, object]]:
    image = _load_image(session, image_id)
    approved = next((crop for crop in image.crops if crop.approved), None)
    covering_blur = _has_approved_covering_blur(session, image)
    gate_kwargs = publishing_gate_kwargs(session, image)
    tags = list(
        session.scalars(
            select(ContentTag.name).join(ImageTag, ImageTag.tag_id == ContentTag.id).where(ImageTag.image_id == image.id)
        ).all()
    )
    mood_names = [tag for tag in tags if tag in {"soft", "dramatic", "playful", "elegant", "high key", "low key"}]
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
    composition = image.orientation or "unknown orientation"
    description = f"A {composition} photograph."
    if approved is not None and image.model_state == SafetyState.SAFE_AFTER_CROP.value:
        description = f"Approved {approved.aspect_ratio} crop of a {composition} photograph."
    elif covering_blur and image.model_state == SafetyState.SAFE_AFTER_CROP.value:
        description = f"A {composition} photograph with approved covering blur."
    context = append_voice_to_context(
        build_context(
            attested=is_attested(session, image.library_id),
            human_rating=image.human_rating,
            model_state=image.model_state,
            needs_crop=image.model_state == SafetyState.SAFE_AFTER_CROP.value,
            has_approved_crop=approved is not None,
            has_approved_covering_blur=covering_blur,
            has_approved_blur=bool(gate_kwargs["has_approved_blur"]),
            do_not_post_blur_unlocked=bool(gate_kwargs["do_not_post_blur_unlocked"]),
            blur_verification_pending=bool(gate_kwargs["blur_verification_pending"]),
            policy=settings.gemini_image_policy,
            operator_confirmed=body.confirmed,
            content_tags=tags,
            composition=composition,
            mood=", ".join(mood_names) if mood_names else "unspecified",
            safe_description=description,
            crop=crop_payload if image.model_state == SafetyState.SAFE_AFTER_CROP.value and approved else None,
            gemini_refused=is_gemini_refused(image),
        ),
        account_voice=settings.account_voice,
    )
    pixels = _preview_bytes(session, settings, image, approved)
    audit = GeminiRequest(
        image_id=image.id,
        policy=settings.gemini_image_policy,
        context_sha256=context.sha256(),
        context_json=context.canonical_json(),
        status="pending",
    )
    session.add(audit)
    session.commit()
    # Final pre-send blur re-check from the database (another request may have edited the blur).
    if not fresh_blur_gate_passes(session, image):
        audit.status = "failed"
        session.commit()
        raise GeminiBlocked(BLUR_NOT_VERIFIED_GEMINI)
    try:
        batch = request.app.state.gemini.generate_captions(
            context, policy=settings.gemini_image_policy, image_bytes=pixels
        )
    except (GeminiBlocked, NotConfigured) as exc:
        audit.status = "failed"
        if is_provider_refusal(exc):
            mark_gemini_refused(image, f"blocked:{getattr(exc, 'reason', 'refused')}")
        session.commit()
        raise
    except Exception:
        audit.status = "failed"
        session.commit()
        raise NotConfigured("Gemini request failed.") from None
    audit.status = "succeeded"
    created = []
    for draft in batch.captions:
        row = Caption(
            image_id=image.id,
            style=draft.style,
            language=draft.language,
            text=draft.text,
            original_text=draft.text,
            status=CaptionStatus.GENERATED.value,
        )
        session.add(row)
        created.append(row)
    session.commit()
    return [_caption_out(row) for row in created]


@router.get("/api/images/{image_id}/captions")
def list_captions(image_id: UUID, _auth: None = Depends(require_session), session: Session = db_dep) -> list[dict[str, object]]:
    _load_image(session, image_id)
    rows = session.scalars(select(Caption).where(Caption.image_id == image_id).order_by(Caption.created_at.desc())).all()
    return [_caption_out(row) for row in rows]


@router.patch("/api/captions/{caption_id}")
def update_caption(
    caption_id: UUID,
    body: CaptionUpdate,
    _auth: None = Depends(require_session),
    session: Session = db_dep,
) -> dict[str, object]:
    if body.status not in {CaptionStatus.USED.value, CaptionStatus.MODIFIED.value, CaptionStatus.REJECTED.value}:
        raise HTTPException(status_code=400, detail="Status must be used, modified, or rejected.")
    row = session.get(Caption, caption_id)
    if row is None:
        raise NotFound("Caption was not found.")
    row.status = body.status
    if body.text is not None and body.text.strip() and body.text.strip() != row.text:
        row.text = body.text.strip()
        row.status = CaptionStatus.MODIFIED.value
    session.commit()
    return _caption_out(row)


def _candidate_inputs(
    session: Session,
    settings: Settings,
) -> tuple[list[CandidateInput], dict[str, Image], dict[str, str | None]]:
    rows = session.scalars(
        select(Image)
        .where(Image.skip_reason.is_(None))
        .where(Image.human_rating.in_([HumanRating.SAFE.value, HumanRating.EXCELLENT.value]))
        .where(Image.model_state.is_not(None))
        .where(
            or_(
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
            selectinload(Image.captions),
        )
    ).all()
    perf_weight = settings.curate_performance_weight
    perf_signals = load_performance_signals(session) if perf_weight > 0 else None
    attested: dict[UUID, bool] = {}
    inputs: list[CandidateInput] = []
    images: dict[str, Image] = {}
    aspects: dict[str, str | None] = {}
    for image in rows:
        if image.library_id not in attested:
            attested[image.library_id] = is_attested(session, image.library_id)
        if not attested[image.library_id]:
            continue
        approved = next((crop for crop in image.crops if crop.approved), None)
        covering_blur = _has_approved_covering_blur(session, image)
        if image.model_state == SafetyState.SAFE_AFTER_CROP.value and approved is None and not covering_blur:
            continue
        score = image.score
        covered = approved is not None or covering_blur
        tags = [link.tag.name for link in image.tags if link.tag is not None] if image.tags else []
        inputs.append(
            CandidateInput(
                image_id=str(image.id),
                aesthetic_score=score.aesthetic_score if score else 0,
                human_rating=image.human_rating,
                uniqueness=score.uniqueness if score else 0,
                phash=image.phash,
                has_approved_crop=covered,
                crop_aspect=approved.aspect_ratio if approved else None,
                model_state=image.model_state,
                sharpness=score.sharpness if score else 0,
                composition=score.composition if score else 0,
                performance_score=(
                    image_performance_boost(perf_signals, str(image.id), tags) if perf_signals else 0.0
                ),
            )
        )
        images[str(image.id)] = image
        aspects[str(image.id)] = approved.aspect_ratio if approved else None
    return inputs, images, aspects


def _candidate_out(session: Session, item, images: dict[str, Image], aspects: dict[str, str | None]) -> CandidateOut:
    image = images[item.image_id]
    queue_item = next(iter(image.queue_items), None) if image.queue_items else None
    published = bool(queue_item and queue_item.state == QueueState.PUBLISHED.value)
    gemini_status = getattr(image, "gemini_label_status", None)
    return CandidateOut(
        image=_image_out(session, image),
        score=item.score,
        reasons=item.reasons,
        held_back=item.held_back,
        crop_aspect=aspects.get(item.image_id),
        model_state=image.model_state,
        has_tags=bool(image.tags),
        has_caption=bool(image.captions),
        published=published,
        gemini_label_status=str(gemini_status) if gemini_status else None,
    )


@router.get("/api/candidates", response_model=CandidatePage)
def candidates(
    limit: int = Query(default=20, ge=1, le=50),
    offset: int = Query(default=0, ge=0, le=5000),
    exclude_queued: bool = Query(default=False),
    exclude_published: bool = Query(default=False),
    _auth: None = Depends(require_session),
    session: Session = db_dep,
    settings: Settings = Depends(settings_dep),
) -> CandidatePage:
    inputs, images, aspects = _candidate_inputs(session, settings)
    ranked = rank_candidates(
        inputs,
        limit=max(len(inputs), 1),
        offset=0,
        performance_weight=settings.curate_performance_weight,
    )
    filtered = []
    for item in ranked:
        image = images[item.image_id]
        if exclude_queued and image.queue_items:
            continue
        if exclude_published:
            queue_item = next(iter(image.queue_items), None) if image.queue_items else None
            if queue_item and queue_item.state == QueueState.PUBLISHED.value:
                continue
        filtered.append(item)
    page_items = filtered[offset : offset + limit]
    next_offset = offset + limit if len(filtered) > offset + limit else None
    return CandidatePage(
        items=[_candidate_out(session, item, images, aspects) for item in page_items],
        next_offset=next_offset,
    )


@router.post("/api/candidates/queue", response_model=CandidateBulkQueueResult)
def candidates_bulk_queue(
    body: CandidateBulkIn,
    _auth: None = Depends(require_session),
    session: Session = db_dep,
) -> CandidateBulkQueueResult:
    raw = bulk_enqueue(session, body.image_ids, covering_blur_fn=_has_approved_covering_blur)
    results = [
        CandidateBulkItemResult(image_id=image_id, ok=ok, error=error) for image_id, ok, error in raw
    ]
    queued_count = sum(1 for row in results if row.ok)
    return CandidateBulkQueueResult(results=results, queued_count=queued_count)


@router.post("/api/candidates/reject", response_model=list[CandidateBulkItemResult])
def candidates_bulk_reject(
    body: CandidateBulkIn,
    _auth: None = Depends(require_session),
    session: Session = db_dep,
) -> list[CandidateBulkItemResult]:
    raw = bulk_reject(session, body.image_ids)
    return [CandidateBulkItemResult(image_id=image_id, ok=ok, error=error) for image_id, ok, error in raw]


@router.post("/api/curation", status_code=201)
def create_curation(
    body: CurationIn,
    request: Request,
    _auth: None = Depends(require_session),
    session: Session = db_dep,
    settings: Settings = Depends(settings_dep),
) -> dict[str, object]:
    """Queue Gemini + local curation. Draft picks only — never auto-queues or publishes."""
    reconcile_stale_curation_batches(session, settings.redis_url)
    if active_curation_in_flight(session, body.library_id):
        raise NotAllowed("A Gemini curation batch is already queued or running for this library.")
    folder = None
    if body.folder:
        folder = _folder_or_400(body.folder)
    defer = settings.pipeline_mode != "inline"
    batch = run_curation(
        session,
        request.app.state.gemini,
        library_id=body.library_id,
        folder=folder or None,
        image_ids=body.image_ids,
        limit=body.limit,
        account_voice=settings.account_voice,
        policy=settings.gemini_image_policy,
        covering_blur_fn=_has_approved_covering_blur,
        include_images=body.include_images,
        preview_bytes_fn=lambda image: _curation_derivative_bytes(session, settings, image),
        defer_execution=defer,
        thinking_budget=settings.gemini_curation_thinking_budget,
        performance_weight=settings.curate_performance_weight,
    )
    if defer:
        job = ProcessingJob(
            kind=JobKind.CURATE.value,
            status=JobStatus.QUEUED.value,
            library_id=body.library_id,
            detail=json.dumps(
                {
                    "batch_id": str(batch.id),
                    "include_images": body.include_images,
                    "image_ids": [str(value) for value in body.image_ids or []],
                },
                sort_keys=True,
            ),
        )
        session.add(job)
        session.commit()
        _enqueue_worker_job(
            settings,
            "instamagic.workers.tasks.run_curation_job",
            str(job.id),
            job_timeout=60 * 60 * 2,
            queue_name=QUEUE_GEMINI,
        )
    return _curation_batch_out(session, batch)


@router.post("/api/gemini/label", status_code=202)
def enqueue_gemini_label(
    body: GeminiLabelIn,
    _auth: None = Depends(require_session),
    session: Session = db_dep,
    settings: Settings = Depends(settings_dep),
) -> dict[str, object]:
    if not body.all_eligible and not body.image_ids:
        raise NotAllowed("Provide image_ids or set all_eligible=true.")
    job = create_gemini_label_job(
        session,
        library_id=body.library_id,
        image_ids=body.image_ids,
        all_eligible=body.all_eligible,
    )
    _enqueue_gemini_label_work(
        session,
        settings,
        library_id=body.library_id,
        image_ids=body.image_ids,
        all_eligible=body.all_eligible,
        existing_job=job,
    )
    return {"job_id": str(job.id), "status": job.status}


@router.get("/api/gemini/label/stats")
def gemini_label_stats(
    library_id: UUID | None = None,
    _auth: None = Depends(require_session),
    session: Session = db_dep,
) -> dict[str, int]:
    return gemini_label_counts(session, library_id=library_id, covering_blur_fn=_has_approved_covering_blur)


@router.get("/api/curation")
def list_curation(
    _auth: None = Depends(require_session),
    session: Session = db_dep,
    settings: Settings = Depends(settings_dep),
) -> list[dict[str, object]]:
    reconcile_stale_curation_batches(session, settings.redis_url)
    rows = session.scalars(select(CurationBatch).order_by(CurationBatch.created_at.desc()).limit(30)).all()
    return [_curation_batch_summary(row) for row in rows]


@router.get("/api/curation/{batch_id}")
def get_curation(
    batch_id: UUID,
    _auth: None = Depends(require_session),
    session: Session = db_dep,
    settings: Settings = Depends(settings_dep),
) -> dict[str, object]:
    reconcile_stale_curation_batches(session, settings.redis_url)
    batch = session.get(CurationBatch, batch_id)
    if batch is None:
        raise NotFound("Curation batch was not found.")
    return _curation_batch_out(session, batch)


@router.patch("/api/curation/{batch_id}/reorder")
def reorder_curation(
    batch_id: UUID,
    body: CurationReorderIn,
    _auth: None = Depends(require_session),
    session: Session = db_dep,
) -> dict[str, object]:
    batch = session.get(CurationBatch, batch_id)
    if batch is None:
        raise NotFound("Curation batch was not found.")
    picks = {
        pick.id: pick
        for pick in session.scalars(select(CurationPick).where(CurationPick.batch_id == batch_id)).all()
    }
    if set(body.pick_ids) != set(picks):
        raise NotAllowed("Reorder must include every pick in the batch exactly once.")
    for index, pick_id in enumerate(body.pick_ids, start=1):
        picks[pick_id].sort_order = index
    session.commit()
    return _curation_batch_out(session, batch)


@router.patch("/api/curation/picks/{pick_id}")
def update_curation_pick(
    pick_id: UUID,
    body: CurationPickUpdate,
    _auth: None = Depends(require_session),
    session: Session = db_dep,
) -> dict[str, object]:
    pick = session.get(CurationPick, pick_id)
    if pick is None:
        raise NotFound("Curation pick was not found.")
    if body.status is not None:
        allowed = {
            CurationPickStatus.SUGGESTED.value,
            CurationPickStatus.ACCEPTED.value,
            CurationPickStatus.REJECTED.value,
        }
        if body.status not in allowed:
            raise HTTPException(status_code=400, detail="Status must be suggested, accepted, or rejected.")
        if pick.status == CurationPickStatus.QUEUED.value:
            raise NotAllowed("Queued picks cannot change status here.")
        pick.status = body.status
    if body.preferred_caption_id is not None:
        caption = session.get(Caption, body.preferred_caption_id)
        if caption is None or caption.image_id != pick.image_id:
            raise NotFound("Caption was not found for this image.")
        pick.preferred_caption_id = caption.id
    if body.suggested_tags is not None:
        cleaned: list[str] = []
        for raw in body.suggested_tags:
            name = raw.strip().lstrip("#").lower().replace(" ", "")[:48]
            if name and name not in cleaned and not any(ch in name for ch in "/\\\x00\n\r\t"):
                cleaned.append(name)
        pick.suggested_tags_json = json.dumps(cleaned[:12], ensure_ascii=False)
    if body.caption_text is not None and body.caption_text.strip():
        if pick.preferred_caption_id is None:
            raise NotAllowed("Select a caption before editing its text.")
        caption = session.get(Caption, pick.preferred_caption_id)
        if caption is None:
            raise NotFound("Caption was not found.")
        caption.text = body.caption_text.strip()
        caption.status = CaptionStatus.MODIFIED.value
    session.commit()
    return _curation_pick_out(session, pick)


@router.post("/api/curation/picks/{pick_id}/undo-queue")
def undo_curation_pick_queue(
    pick_id: UUID,
    _auth: None = Depends(require_session),
    session: Session = db_dep,
) -> dict[str, object]:
    """Undo a recent send-to-queue while the publish queue item is still a candidate."""
    pick = session.get(CurationPick, pick_id)
    if pick is None:
        raise NotFound("Curation pick was not found.")
    pick = undo_queued_pick(session, pick)
    return _curation_pick_out(session, pick)


@router.post("/api/curation/queue")
def send_curation_to_queue(
    body: CurationQueueIn,
    _auth: None = Depends(require_session),
    session: Session = db_dep,
) -> list[dict[str, object]]:
    """Human last word: only selected picks enter the publish queue (still not published)."""
    items = queue_picks(
        session,
        pick_ids=body.pick_ids,
        covering_blur_fn=_has_approved_covering_blur,
        story_share_enabled=body.story_share_enabled,
        story_render_mode=body.story_render_mode,
        story_border_color=body.story_border_color,
        story_timing_mode=body.story_timing_mode,
        story_delay_hours=body.story_delay_hours,
    )
    return [_queue_out(session, item) for item in items]


def _reload_instagram_client(request: Request, settings: Settings, session: Session) -> None:
    request.app.state.instagram = build_instagram(settings, session)


def _kick_social(settings: Settings) -> None:
    """After a reconnect: ask worker-io's social lane to sync insights/comments and fill Engage."""
    if settings.pipeline_mode != "inline":
        from instamagic.workers.social_loop import kick_social

        kick_social(settings.redis_url)


@router.get("/api/instagram/status")
def instagram_status(
    request: Request,
    _auth: None = Depends(require_session),
    settings: Settings = Depends(settings_dep),
    session: Session = db_dep,
) -> dict[str, object]:
    """Credential presence + cached profile — no Graph call. Never returns the raw access token."""
    _reload_instagram_client(request, settings, session)
    client = request.app.state.instagram
    return connection_status_fields(settings, session, configured=bool(client.configured()))


@router.post("/api/instagram/credentials")
def instagram_save_credentials(
    body: InstagramCredentialsIn,
    request: Request,
    _auth: None = Depends(require_session),
    settings: Settings = Depends(settings_dep),
    session: Session = db_dep,
) -> dict[str, object]:
    """Seal long-lived token + IG business user id in the DB, then fetch and cache account JSON."""
    if settings.instagram_provider == "fake":
        raise NotAllowed("Cannot save Graph credentials while INSTAGRAM_PROVIDER=fake.")
    if settings.instagram_access_token.strip() and settings.instagram_business_account_id.strip():
        raise NotAllowed(
            "Host .env already sets INSTAGRAM_ACCESS_TOKEN and INSTAGRAM_BUSINESS_ACCOUNT_ID. "
            "Clear those env vars (and restart) to manage credentials from this page, or keep using env."
        )
    try:
        save_credentials(
            session,
            settings,
            access_token=body.access_token,
            business_account_id=body.business_account_id,
        )
    except ValueError as exc:
        raise NotAllowed(str(exc)) from exc
    _reload_instagram_client(request, settings, session)
    client = request.app.state.instagram
    status = connection_status_fields(settings, session, configured=bool(client.configured()))
    try:
        details = client.fetch_account_details()
    except NotConfigured as exc:
        return {
            **status,
            "ok": False,
            "error": exc.detail,
            "account": status.get("account"),
        }
    save_account_profile(session, details)
    status = connection_status_fields(settings, session, configured=True)
    return {
        **status,
        "ok": True,
        "error": None,
        "account": details.as_dict(),
    }


@router.delete("/api/instagram/credentials")
def instagram_clear_credentials(
    request: Request,
    _auth: None = Depends(require_session),
    settings: Settings = Depends(settings_dep),
    session: Session = db_dep,
) -> dict[str, object]:
    """Remove sealed DB credentials and cached profile. Env credentials are unchanged."""
    if settings.instagram_provider == "fake":
        raise NotAllowed("Fake provider has no database credentials to clear.")
    clear_credentials(session)
    _reload_instagram_client(request, settings, session)
    client = request.app.state.instagram
    status = connection_status_fields(settings, session, configured=bool(client.configured()))
    return {**status, "ok": True, "error": None, "account": status.get("account")}


@router.post("/api/instagram/account")
def instagram_account_refresh(
    request: Request,
    _auth: None = Depends(require_session),
    settings: Settings = Depends(settings_dep),
    session: Session = db_dep,
) -> dict[str, object]:
    """Test connection, fetch IG user fields, and persist the profile JSON. Does not publish."""
    _reload_instagram_client(request, settings, session)
    client = request.app.state.instagram
    status = connection_status_fields(settings, session, configured=bool(client.configured()))
    if not client.configured():
        return {
            **status,
            "ok": False,
            "error": (
                "Instagram is not configured. Use Connect with Meta on this page, paste a Page token "
                "below, or set INSTAGRAM_* in the host .env."
            ),
            "account": status.get("account"),
        }
    try:
        details = client.fetch_account_details()
    except NotConfigured as exc:
        return {
            **status,
            "ok": False,
            "error": exc.detail,
            "account": status.get("account"),
        }
    save_account_profile(session, details)
    refresh_token_scopes(session, client)
    status = connection_status_fields(settings, session, configured=True)
    return {
        **status,
        "ok": True,
        "error": None,
        "account": details.as_dict(),
    }


@router.get("/api/instagram/insights/dashboard")
def instagram_insights_dashboard(
    _auth: None = Depends(require_session),
    session: Session = db_dep,
) -> dict[str, object]:
    return insights_dashboard(session)


@router.get("/api/instagram/best-posting-times")
def instagram_best_posting_times(
    _auth: None = Depends(require_session),
    session: Session = db_dep,
) -> dict[str, object]:
    return best_posting_times(session)


@router.post("/api/instagram/insights/sync")
def instagram_insights_sync(
    request: Request,
    force: bool = Query(default=False),
    _auth: None = Depends(require_session),
    settings: Settings = Depends(settings_dep),
    session: Session = db_dep,
) -> dict[str, object]:
    _reload_instagram_client(request, settings, session)
    client = request.app.state.instagram
    result = sync_instagram_insights(session, settings, client, force=force)
    status = connection_status_fields(settings, session, configured=bool(client.configured()))
    return {**status, **result}


@router.post("/api/instagram/meta-app")
def instagram_save_meta_app(
    body: MetaAppCredentialsIn,
    request: Request,
    _auth: None = Depends(require_session),
    settings: Settings = Depends(settings_dep),
    session: Session = db_dep,
) -> dict[str, object]:
    """Seal Meta App ID + App Secret in the DB so Connect works without META_* in .env."""
    if settings.instagram_provider == "fake":
        raise NotAllowed("Cannot save Meta app credentials while INSTAGRAM_PROVIDER=fake.")
    try:
        save_meta_app_credentials(
            session,
            settings,
            app_id=body.app_id,
            app_secret=body.app_secret,
        )
    except ValueError as exc:
        raise NotAllowed(str(exc)) from exc
    _reload_instagram_client(request, settings, session)
    client = request.app.state.instagram
    status = connection_status_fields(settings, session, configured=bool(client.configured()))
    return {**status, "ok": True, "error": None, "account": status.get("account")}


@router.delete("/api/instagram/meta-app")
def instagram_clear_meta_app(
    request: Request,
    _auth: None = Depends(require_session),
    settings: Settings = Depends(settings_dep),
    session: Session = db_dep,
) -> dict[str, object]:
    """Remove sealed Meta App credentials. Host META_* env (if any) still applies as fallback."""
    if settings.instagram_provider == "fake":
        raise NotAllowed("Fake provider has no Meta app credentials to clear.")
    clear_meta_app_credentials(session)
    _reload_instagram_client(request, settings, session)
    client = request.app.state.instagram
    status = connection_status_fields(settings, session, configured=bool(client.configured()))
    return {**status, "ok": True, "error": None, "account": status.get("account")}


@router.get("/api/instagram/oauth/start")
def instagram_oauth_start(
    response: Response,
    _auth: None = Depends(require_session),
    settings: Settings = Depends(settings_dep),
    session: Session = db_dep,
) -> dict[str, object]:
    """Return the Meta authorize URL when Meta App ID/Secret are in DB or host env."""
    meta = resolve_meta_app_credentials(settings, session)
    if not oauth_available(meta):
        raise NotConfigured(
            "Meta OAuth is not configured. Save Meta App ID and App Secret on /instagram, "
            "or set META_APP_ID / META_APP_SECRET on the host."
        )
    assert meta is not None
    url, _state, nonce = build_authorize_url(settings, meta)
    response.set_cookie(
        OAUTH_NONCE_COOKIE,
        nonce,
        max_age=600,
        httponly=True,
        samesite="lax",
        secure=settings.session_cookie_secure(),
        path="/",
    )
    return {"authorize_url": url, "redirect_uri": oauth_redirect_uri(settings)}


@router.get("/api/instagram/oauth/callback")
def instagram_oauth_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
    settings: Settings = Depends(settings_dep),
    session: Session = db_dep,
) -> RedirectResponse:
    """Meta redirects here (no session cookie required). Saves sealed credentials then sends UI home."""
    frontend = settings.frontend_origin.rstrip("/")
    dest_ok = f"{frontend}/instagram?oauth=ok"
    dest_err = f"{frontend}/instagram?oauth=error"

    def _fail(message: str) -> RedirectResponse:
        # Keep message short and token-free for the query string.
        safe = "".join(ch for ch in message if ch.isprintable())[:180]
        from urllib.parse import quote

        return RedirectResponse(url=f"{dest_err}&detail={quote(safe)}", status_code=302)

    if error:
        return _fail(error_description or error)
    if not code or not state:
        return _fail("Missing OAuth code or state.")
    nonce_cookie = request.cookies.get(OAUTH_NONCE_COOKIE)
    redirect_clear_cookie = RedirectResponse(url=dest_ok, status_code=302)
    redirect_clear_cookie.delete_cookie(
        OAUTH_NONCE_COOKIE,
        path="/",
        httponly=True,
        samesite="lax",
        secure=settings.session_cookie_secure(),
    )
    try:
        verify_oauth_state(state, settings, nonce_cookie=nonce_cookie)
        meta = resolve_meta_app_credentials(settings, session)
        if not oauth_available(meta):
            return _fail("Meta OAuth is not configured.")
        assert meta is not None
        tokens, selection, _user_token = exchange_code_for_credentials_with_pages(
            settings, meta, code=code
        )
        if selection is not None:
            pending = seal_oauth_pending(
                settings,
                user_access_token=selection.user_access_token,
                options=selection.options,
            )
            from urllib.parse import quote

            choose_url = (
                f"{frontend}/instagram?oauth=choose&pending={quote(pending, safe='')}"
            )
            resp = RedirectResponse(url=choose_url, status_code=302)
            resp.delete_cookie(
                OAUTH_NONCE_COOKIE,
                path="/",
                httponly=True,
                samesite="lax",
                secure=settings.session_cookie_secure(),
            )
            return resp
        if tokens is None:
            return _fail("OAuth did not return Instagram credentials.")
        save_credentials(
            session,
            settings,
            access_token=tokens.access_token,
            business_account_id=tokens.business_account_id,
            facebook_page_id=tokens.page_id,
        )
        _reload_instagram_client(request, settings, session)
        client = request.app.state.instagram
        details = client.fetch_account_details()
        save_account_profile(session, details)
        _kick_social(settings)
    except (NotConfigured, NotAllowed, ValueError) as exc:
        detail = getattr(exc, "detail", None) or str(exc)
        return _fail(detail)
    except Exception:
        return _fail("OAuth failed unexpectedly. Check Meta app settings and try again.")
    return redirect_clear_cookie


@router.get("/api/instagram/oauth/pending")
def instagram_oauth_pending_options(
    pending: str = Query(min_length=20, max_length=8192),
    _auth: None = Depends(require_session),
    settings: Settings = Depends(settings_dep),
) -> dict[str, object]:
    """List Page choices for an in-progress OAuth (no tokens returned)."""
    payload = open_oauth_pending(settings, pending)
    options = payload.get("options")
    if not isinstance(options, list):
        return {"options": []}
    return {"options": options}


@router.post("/api/instagram/oauth/select-page")
def instagram_oauth_select_page(
    body: InstagramOAuthSelectIn,
    request: Request,
    _auth: None = Depends(require_session),
    settings: Settings = Depends(settings_dep),
    session: Session = db_dep,
) -> dict[str, object]:
    """Finish OAuth when multiple Pages have Instagram accounts."""
    pending = open_oauth_pending(settings, body.pending_token)
    user_token = str(pending.get("user_token") or "").strip()
    if not user_token:
        raise NotAllowed("OAuth page selection is invalid.")
    tokens = complete_oauth_with_page(settings, user_access_token=user_token, page_id=body.page_id)
    save_credentials(
        session,
        settings,
        access_token=tokens.access_token,
        business_account_id=tokens.business_account_id,
        facebook_page_id=tokens.page_id,
    )
    _reload_instagram_client(request, settings, session)
    client = request.app.state.instagram
    details = client.fetch_account_details()
    save_account_profile(session, details)
    _kick_social(settings)
    status = connection_status_fields(settings, session, configured=True)
    return {
        **status,
        "ok": True,
        "error": None,
        "account": details.as_dict(),
        "connected_page_name": tokens.page_name,
        "connected_ig_username": tokens.ig_username,
    }


@router.api_route(
    "/api/public/publish-assets/{token}",
    methods=["GET", "HEAD"],
    response_model=None,
)
def public_publish_asset(
    request: Request,
    token: str,
    settings: Settings = Depends(settings_dep),
) -> FileResponse | Response:
    """Unauthenticated short-lived JPEG for Meta's crawler. Token-gated; no session cookie."""
    # Invalid, garbled, expired or missing → plain 404 (no oracle for token guessing).
    try:
        asset = parse_publish_token(token, secret=settings.app_secret)
        path = resolve_publish_file(settings.derivatives_root, asset)
    except (NotAllowed, NotFound):
        raise HTTPException(
            status_code=404, detail="Not Found", headers={"X-Instamagic-Asset": "rejected"}
        ) from None
    cache = {"Cache-Control": "private, max-age=60"}
    if request.method == "HEAD":
        stat = path.stat()
        return Response(
            status_code=200,
            headers={
                **cache,
                "Content-Type": "image/jpeg",
                "Content-Length": str(stat.st_size),
            },
        )
    return FileResponse(path, media_type="image/jpeg", headers=cache)


@router.get("/api/related-accounts")
def list_related_accounts(_auth: None = Depends(require_session), session: Session = db_dep) -> list[dict[str, object]]:
    rows = session.scalars(select(RelatedAccount).order_by(RelatedAccount.handle.asc())).all()
    return [_related_out(row) for row in rows]


@router.post("/api/related-accounts", status_code=201)
def create_related_account(
    body: RelatedAccountIn,
    _auth: None = Depends(require_session),
    session: Session = db_dep,
) -> dict[str, object]:
    existing = session.scalar(select(RelatedAccount).where(RelatedAccount.handle == body.handle))
    if existing is not None:
        raise NotAllowed("That handle is already on the research list.")
    row = RelatedAccount(
        handle=body.handle,
        display_name=(body.display_name or "").strip() or None,
        profile_url=(body.profile_url or "").strip() or None,
        notes=(body.notes or "").strip() or None,
        why_relevant=(body.why_relevant or "").strip() or None,
    )
    session.add(row)
    session.commit()
    return _related_out(row)


@router.patch("/api/related-accounts/{account_id}")
def update_related_account(
    account_id: UUID,
    body: RelatedAccountIn,
    _auth: None = Depends(require_session),
    session: Session = db_dep,
) -> dict[str, object]:
    row = session.get(RelatedAccount, account_id)
    if row is None:
        raise NotFound("Related account was not found.")
    clash = session.scalar(
        select(RelatedAccount).where(RelatedAccount.handle == body.handle, RelatedAccount.id != account_id)
    )
    if clash is not None:
        raise NotAllowed("That handle is already on the research list.")
    row.handle = body.handle
    row.display_name = (body.display_name or "").strip() or None
    row.profile_url = (body.profile_url or "").strip() or None
    row.notes = (body.notes or "").strip() or None
    row.why_relevant = (body.why_relevant or "").strip() or None
    session.commit()
    return _related_out(row)


@router.get("/api/inbox/comments")
def inbox_list_comments(
    status: str = Query(default="open"),
    _auth: None = Depends(require_session),
    session: Session = db_dep,
) -> list[dict[str, object]]:
    stmt = select(InstagramInboxComment).order_by(InstagramInboxComment.commented_at.desc().nullslast())
    if status != "all":
        stmt = stmt.where(InstagramInboxComment.status == status)
    rows = session.scalars(stmt.limit(100)).all()
    return [_inbox_comment_out(row) for row in rows]


@router.post("/api/inbox/comments/sync")
def inbox_sync_comments(
    request: Request,
    _auth: None = Depends(require_session),
    settings: Settings = Depends(settings_dep),
    session: Session = db_dep,
) -> dict[str, object]:
    _reload_instagram_client(request, settings, session)
    client = request.app.state.instagram
    return sync_inbox_comments(session, settings, client, force=True)


@router.post("/api/inbox/comments/{comment_id}/suggest-reply")
def inbox_suggest_reply(
    comment_id: UUID,
    request: Request,
    _auth: None = Depends(require_session),
    session: Session = db_dep,
    settings: Settings = Depends(settings_dep),
) -> dict[str, object]:
    row = session.get(InstagramInboxComment, comment_id)
    if row is None:
        raise NotFound("Comment was not found.")
    post = session.get(InstagramPost, row.post_id) if row.post_id else None
    text = suggest_reply(
        session,
        request.app.state.growth,
        row,
        account_voice=settings.account_voice,
        post_caption=post.caption if post else None,
    )
    return {"suggested_reply": text}


@router.post("/api/inbox/comments/{comment_id}/reply")
def inbox_post_reply(
    comment_id: UUID,
    body: dict[str, str],
    request: Request,
    _auth: None = Depends(require_session),
    settings: Settings = Depends(settings_dep),
    session: Session = db_dep,
) -> dict[str, object]:
    row = session.get(InstagramInboxComment, comment_id)
    if row is None:
        raise NotFound("Comment was not found.")
    message = (body.get("message") or row.suggested_reply or "").strip()
    _reload_instagram_client(request, settings, session)
    post_reply(session, request.app.state.instagram, row, message)
    return _inbox_comment_out(row)


@router.patch("/api/inbox/comments/{comment_id}")
def inbox_update_comment(
    comment_id: UUID,
    body: dict[str, str],
    _auth: None = Depends(require_session),
    session: Session = db_dep,
) -> dict[str, object]:
    row = session.get(InstagramInboxComment, comment_id)
    if row is None:
        raise NotFound("Comment was not found.")
    if "status" in body and body["status"] in {"open", "dismissed", "hidden"}:
        row.status = body["status"]
    if "suggested_reply" in body:
        row.suggested_reply = body["suggested_reply"].strip() or row.suggested_reply
    session.commit()
    return _inbox_comment_out(row)


@router.get("/api/hashtag-research/budget")
def hashtag_research_budget(
    _auth: None = Depends(require_session),
    session: Session = db_dep,
) -> dict[str, int]:
    return budget_status(session)


@router.post("/api/hashtag-research/{tag}")
def hashtag_research_lookup(
    tag: str,
    request: Request,
    force: bool = Query(default=False),
    _auth: None = Depends(require_session),
    settings: Settings = Depends(settings_dep),
    session: Session = db_dep,
) -> dict[str, object]:
    _reload_instagram_client(request, settings, session)
    client = request.app.state.instagram
    try:
        return research_hashtag(session, client, tag, force=force)
    except HashtagResearchUnavailable as exc:
        raise NotConfigured(str(exc.detail)) from exc


@router.get("/api/related-accounts/{account_id}/discovery")
def get_related_discovery(
    account_id: UUID,
    _auth: None = Depends(require_session),
    session: Session = db_dep,
) -> dict[str, object]:
    account = session.get(RelatedAccount, account_id)
    if account is None:
        raise NotFound("Related account was not found.")
    row = session.get(RelatedAccountDiscovery, account_id)
    if row is None:
        return {"handle": account.handle, "cached": False}
    return {**discovery_out(row, account.handle), "cached": True}


@router.post("/api/related-accounts/{account_id}/discovery")
def sync_related_discovery(
    account_id: UUID,
    request: Request,
    force: bool = Query(default=False),
    _auth: None = Depends(require_session),
    settings: Settings = Depends(settings_dep),
    session: Session = db_dep,
) -> dict[str, object]:
    account = session.get(RelatedAccount, account_id)
    if account is None:
        raise NotFound("Related account was not found.")
    _reload_instagram_client(request, settings, session)
    client = request.app.state.instagram
    return sync_related_account_discovery(session, client, account, force=force)


@router.delete("/api/related-accounts/{account_id}", status_code=204)
def delete_related_account(
    account_id: UUID,
    _auth: None = Depends(require_session),
    session: Session = db_dep,
) -> Response:
    row = session.get(RelatedAccount, account_id)
    if row is None:
        raise NotFound("Related account was not found.")
    session.delete(row)
    session.commit()
    return Response(status_code=204)


@router.post("/api/comment-drafts", status_code=201)
def create_comment_drafts(
    body: CommentDraftIn,
    request: Request,
    _auth: None = Depends(require_session),
    session: Session = db_dep,
    settings: Settings = Depends(settings_dep),
) -> list[dict[str, object]]:
    """Assisted reply drafts only — never posted to Instagram by the API."""
    image = None
    research = ""
    if body.image_id is not None:
        image = _load_image(session, body.image_id)
    related = None
    if body.related_account_id is not None:
        related = session.get(RelatedAccount, body.related_account_id)
        if related is None:
            raise NotFound("Related account was not found.")
        research = " ".join(part for part in [related.why_relevant, related.notes] if part)
    if image is None:
        # Synthetic minimal context when drafting against a related account only.
        context = append_voice_to_context(
            build_context(
                attested=True,
                human_rating=HumanRating.SAFE.value,
                model_state=SafetyState.SAFE.value,
                needs_crop=False,
                has_approved_crop=False,
                policy="text_only",
                operator_confirmed=True,
                content_tags=[],
                composition="unspecified",
                mood="unspecified",
                safe_description="Operator research drafting; no photo attached.",
                crop=None,
            ),
            account_voice=settings.account_voice,
            research_notes=research,
        )
    else:
        approved = next((crop for crop in image.crops if crop.approved), None)
        covering_blur = _has_approved_covering_blur(session, image)
        tags = list(
            session.scalars(
                select(ContentTag.name).join(ImageTag, ImageTag.tag_id == ContentTag.id).where(ImageTag.image_id == image.id)
            ).all()
        )
        mood_names = [tag for tag in tags if tag in {"soft", "dramatic", "playful", "elegant", "high key", "low key"}]
        composition = image.orientation or "unknown orientation"
        context = append_voice_to_context(
            build_context(
                attested=is_attested(session, image.library_id),
                human_rating=image.human_rating,
                model_state=image.model_state,
                needs_crop=image.model_state == SafetyState.SAFE_AFTER_CROP.value,
                has_approved_crop=approved is not None,
                has_approved_covering_blur=covering_blur,
                policy="text_only",
                operator_confirmed=body.confirmed or settings.gemini_image_policy != "manual_only",
                content_tags=tags,
                composition=composition,
                mood=", ".join(mood_names) if mood_names else "unspecified",
                safe_description=f"A {composition} photograph.",
                crop=None,
                gemini_refused=is_gemini_refused(image),
                blur_verification_pending=not blur_gate_passes(session, image),
            ),
            account_voice=settings.account_voice,
            research_notes=research,
        )
    target = body.target_context.strip()
    if related is not None and not target:
        target = f"Reply in conversation with @{related.handle}"
    if not target:
        target = "General thoughtful reply about photographic craft."
    try:
        batch = request.app.state.growth.generate_comment_drafts(
            context,
            target_context=target,
            account_voice=settings.account_voice,
        )
    except (GeminiBlocked, NotConfigured):
        raise
    except Exception:
        raise NotConfigured("Gemini request failed.") from None
    created = []
    for draft in batch.drafts:
        row = CommentDraft(
            image_id=image.id if image else None,
            related_account_id=related.id if related else None,
            target_context=target,
            text=draft.text,
            status=CommentDraftStatus.DRAFT.value,
        )
        session.add(row)
        created.append(row)
    session.commit()
    return [_comment_draft_out(row) for row in created]


@router.get("/api/comment-drafts")
def list_comment_drafts(_auth: None = Depends(require_session), session: Session = db_dep) -> list[dict[str, object]]:
    rows = session.scalars(select(CommentDraft).order_by(CommentDraft.created_at.desc()).limit(100)).all()
    return [_comment_draft_out(row) for row in rows]


@router.patch("/api/comment-drafts/{draft_id}")
def update_comment_draft(
    draft_id: UUID,
    body: CommentDraftUpdate,
    _auth: None = Depends(require_session),
    session: Session = db_dep,
) -> dict[str, object]:
    allowed = {CommentDraftStatus.DRAFT.value, CommentDraftStatus.COPIED.value, CommentDraftStatus.DISCARDED.value}
    if body.status not in allowed:
        raise HTTPException(status_code=400, detail="Status must be draft, copied, or discarded.")
    row = session.get(CommentDraft, draft_id)
    if row is None:
        raise NotFound("Comment draft was not found.")
    row.status = body.status
    if body.text is not None and body.text.strip():
        row.text = body.text.strip()
    session.commit()
    return _comment_draft_out(row)


@router.post("/api/images/{image_id}/hashtags")
def suggest_hashtags(
    image_id: UUID,
    body: HashtagIn,
    request: Request,
    _auth: None = Depends(require_session),
    session: Session = db_dep,
    settings: Settings = Depends(settings_dep),
) -> dict[str, object]:
    _ = body.confirmed
    image = _load_image(session, image_id)
    stored = clean_hashtags(stored_hashtags(image))
    if stored and not body.refresh:
        return {"tags": stored, "gemini_tags": [], "research_tags": [], "stored": True}
    try:
        merged = generate_enrichment_hashtags(
            session, request.app.state.growth, image, account_voice=settings.account_voice
        )
    except (GeminiBlocked, NotConfigured) as exc:
        if is_provider_refusal(exc):
            mark_gemini_refused(image, f"blocked:{getattr(exc, 'reason', 'refused')}")
            session.commit()
        raise
    except Exception:
        raise NotConfigured("Gemini request failed.") from None
    merged = clean_hashtags(merged)[:15]
    image.enrich_hashtags_json = json.dumps(merged, ensure_ascii=False)
    image.enrich_status = "ok"
    image.enrich_at = datetime.now(UTC)
    session.commit()
    return {"tags": merged, "gemini_tags": [], "research_tags": [], "stored": False}


@router.get("/api/enrichment/status")
def get_enrichment_status(
    _auth: None = Depends(require_session),
    session: Session = db_dep,
    settings: Settings = Depends(settings_dep),
) -> dict[str, object]:
    status = enrichment_status(
        session, _enrich_redis(settings), prefetch_max_score=settings.enrich_prefetch_max_score
    )
    status["rpm"] = settings.enrich_rpm
    status["concurrency"] = settings.enrich_concurrency
    return status


def _enrichment_config_update(session: Session, settings: Settings, **fields: object) -> dict[str, object]:
    row = get_or_create_schedule_config(session)
    for key, value in fields.items():
        setattr(row, key, value)
    session.commit()
    _wake_enrichment(settings)
    return get_enrichment_status(None, session, settings)  # type: ignore[arg-type]


@router.post("/api/enrichment/pause")
def pause_enrichment(
    _auth: None = Depends(require_session),
    session: Session = db_dep,
    settings: Settings = Depends(settings_dep),
) -> dict[str, object]:
    return _enrichment_config_update(session, settings, enrichment_paused=True)


@router.post("/api/enrichment/resume")
def resume_enrichment(
    _auth: None = Depends(require_session),
    session: Session = db_dep,
    settings: Settings = Depends(settings_dep),
) -> dict[str, object]:
    return _enrichment_config_update(session, settings, enrichment_paused=False)


@router.put("/api/enrichment/settings")
def update_enrichment_settings(
    body: EnrichmentSettingsIn,
    _auth: None = Depends(require_session),
    session: Session = db_dep,
    settings: Settings = Depends(settings_dep),
) -> dict[str, object]:
    fields: dict[str, object] = {}
    if body.enabled is not None:
        fields["enrichment_enabled"] = body.enabled
    if body.daily_cap is not None:
        fields["enrichment_daily_cap"] = body.daily_cap
    if body.prefetch_enabled is not None:
        fields["enrichment_prefetch_enabled"] = body.prefetch_enabled
        if body.prefetch_enabled:
            # Operator turned prefetch back on: clear the breaker reason and its window.
            fields["enrichment_prefetch_paused_reason"] = None
            fields["enrichment_prefetch_paused_at"] = None
            reset_prefetch_breaker(_enrich_redis(settings))
    if body.prefetch_daily_cap is not None:
        fields["enrichment_prefetch_daily_cap"] = body.prefetch_daily_cap
    return _enrichment_config_update(session, settings, **fields)


@router.post("/api/enrichment/retry")
def retry_enrichment(
    _auth: None = Depends(require_session),
    session: Session = db_dep,
    settings: Settings = Depends(settings_dep),
) -> dict[str, object]:
    reset = retry_enrichment_errors(session)
    status = _enrichment_config_update(session, settings)
    status["reset"] = reset
    return status


def _crop_out(image: Image, crop: CropSuggestion) -> CropOut:
    preview = None
    if crop.derivative_path:
        stamp = file_stamp(_derivatives_path(crop.derivative_path))
        crop_v = hashlib.sha1(f"{crop.x},{crop.y},{crop.width},{crop.height}|{stamp}".encode()).hexdigest()[:12]
        preview = f"/api/derivatives/{image.id}/crops/{crop.id}?v={crop_v}"
    elif crop.aspect_ratio == "original":
        preview = f"/api/derivatives/{image.id}/preview"
    return CropOut(
        id=crop.id,
        x=crop.x,
        y=crop.y,
        width=crop.width,
        height=crop.height,
        aspect_ratio=crop.aspect_ratio,
        reason=crop.reason,
        contains_face=crop.contains_face,
        excludes_unsafe=crop.excludes_unsafe,
        approved=crop.approved,
        preview_url=preview,
    )


def _blur_out(image: Image, blur: BlurCover, settings: Settings | None = None) -> BlurOut:
    preview = None
    if blur.derivative_path:
        version = blur_render_version(image.blurs, _derivatives_path(blur.derivative_path))
        preview = f"/api/derivatives/{image.id}/blurs/{blur.id}?v={version}"
    return BlurOut(
        id=blur.id,
        x=blur.x,
        y=blur.y,
        width=blur.width,
        height=blur.height,
        shape=blur.shape,
        rotation_deg=blur.rotation_deg,
        corner_radius=blur.corner_radius,
        feather=blur.feather,
        edge_mode=blur.edge_mode,
        radius=blur.radius,
        strength=blur.strength,
        reason=blur.reason,
        covers_unsafe=blur.covers_unsafe,
        approved=blur.approved,
        preview_url=preview,
    )


def _window_from_row(blur: BlurCover) -> BlurWindow:
    return BlurWindow(
        x=blur.x,
        y=blur.y,
        width=blur.width,
        height=blur.height,
        shape=blur.shape,
        feather=blur.feather,
        radius=blur.radius,
        reason=blur.reason,
        covers_unsafe=blur.covers_unsafe,
        strength=blur.strength,
        rotation_deg=blur.rotation_deg,
        corner_radius=blur.corner_radius,
        edge_mode=blur.edge_mode,
    )


def _has_approved_covering_blur(session: Session, image: Image) -> bool:
    return _has_approved_covering_blur_impl(session, image)


def _derivatives_path(relative: str | None) -> Path | None:
    if not relative:
        return None
    try:
        return safe_join(get_settings().derivatives_root, relative)
    except Exception:  # noqa: BLE001 - a version stamp must never break a listing
        return None


def _image_blur_version(image: Image, relative: str = "blurs/cover.jpg") -> str:
    return blur_render_version(image.blurs, _derivatives_path(f"{image.id}/{relative}"))


def _reload_blurs(session: Session, image: Image) -> None:
    """Flush pending blur inserts/deletes and reload ``image.blurs`` from the DB.

    An already-loaded collection does not pick up ``session.add`` of a new row or
    ``session.delete`` of an old one, so rendering from it would bake a stale set of
    covers (new cover missing, deleted cover still blurred).
    """
    session.flush()
    session.expire(image, ["blurs"])


def _refresh_blur_derivative(session: Session, settings: Settings, image: Image) -> None:
    library = session.get(Library, image.library_id)
    if library is None:
        raise NotFound("Library was not found.")
    source = safe_join(Path(library.root_path), image.relative_path)
    if not source.is_file():
        raise NotFound("Original image was not found.")
    rgb = oriented_rgb(read_image(source))
    relative = render_blur_derivative(
        rgb=rgb,
        covers=list(image.blurs),
        image_id=image.id,
        derivatives_root=settings.derivatives_root,
    )
    for blur in image.blurs:
        blur.derivative_path = relative
    if relative:
        from instamagic.services.images.derivatives import THUMB_LONG_EDGE, THUMB_QUALITY
        from PIL import Image as PilImage

        cover_path = settings.derivatives_root / relative
        thumb_path = settings.derivatives_root / f"{image.id}/blurs/thumb.jpg"
        thumb_path.parent.mkdir(parents=True, exist_ok=True)
        with PilImage.open(cover_path) as cover:
            thumb = cover.copy()
            thumb.thumbnail((THUMB_LONG_EDGE, THUMB_LONG_EDGE), PilImage.Resampling.LANCZOS)
            thumb.save(thumb_path, format="JPEG", quality=THUMB_QUALITY)
    else:
        for name in ("cover.jpg", "thumb.jpg"):
            stale = settings.derivatives_root / f"{image.id}/blurs/{name}"
            if stale.is_file():
                stale.unlink()


def _rgb_for_image(session: Session, settings: Settings, image: Image):
    """Prefer the original file; fall back to the preview derivative when the mount lacks it."""
    library = session.get(Library, image.library_id)
    if library is None:
        raise NotFound("Library was not found.")
    source = safe_join(Path(library.root_path), image.relative_path)
    if source.is_file():
        return oriented_rgb(read_image(source))
    if image.preview_path:
        preview = settings.derivatives_root / image.preview_path
        if preview.is_file():
            return oriented_rgb(read_image(preview))
    raise NotFound("Original image was not found.")


def _refresh_crop_derivative(session: Session, settings: Settings, image: Image, crop: CropSuggestion) -> None:
    from instamagic.services.instagram.publish_derivative import refresh_crop_derivative

    refresh_crop_derivative(session, settings, image, crop)


def _queue_out(session: Session, item: PublishingQueueItem) -> dict[str, object]:
    image = _load_image(session, item.image_id)
    ig_post = session.scalar(
        select(InstagramPost)
        .where(InstagramPost.queue_item_id == item.id)
        .order_by(InstagramPost.published_at.desc())
    )
    published_caption: str | None = None
    if ig_post and ig_post.caption:
        published_caption = ig_post.caption
    elif item.caption_id is not None:
        caption_row = session.get(Caption, item.caption_id)
        if caption_row is not None:
            published_caption = caption_row.text
    slide_rows = slides_for_item(session, item) if item.media_type == "CAROUSEL" else []
    slides_payload = []
    for slide in slide_rows:
        slide_image = _load_image(session, slide.image_id)
        slides_payload.append(
            {
                "id": slide.id,
                "image_id": slide.image_id,
                "sort_order": slide.sort_order,
                "image": _image_out(session, slide_image).model_dump(mode="json"),
            }
        )
    from instamagic.models.enums import PublishAttemptKind
    from instamagic.services.posting.story_options import effective_story_options

    story_opts = effective_story_options(session, item)
    story_attempt = session.scalar(
        select(PublishAttempt)
        .where(
            PublishAttempt.queue_item_id == item.id,
            PublishAttempt.kind == PublishAttemptKind.STORY.value,
        )
        .order_by(PublishAttempt.started_at.desc())
    )
    story_status = "off"
    if story_opts.share_enabled and item.media_type != "CAROUSEL":
        if item.story_published_at:
            story_status = "published"
        elif story_attempt and not story_attempt.success and story_attempt.error:
            story_status = "failed"
        elif item.story_scheduled_at and (
            item.story_scheduled_at
            if item.story_scheduled_at.tzinfo
            else item.story_scheduled_at.replace(tzinfo=UTC)
        ) > datetime.now(UTC):
            story_status = "scheduled"
        elif item.state == QueueState.PUBLISHED.value and story_opts.share_enabled:
            story_status = "pending"
        else:
            story_status = "enabled"
    return {
        "id": item.id,
        "state": item.state,
        "media_type": item.media_type,
        "crop_id": item.crop_id,
        "caption_id": item.caption_id,
        "created_at": item.created_at,
        "approved_at": item.approved_at,
        "published_at": item.published_at,
        "last_error": item.last_error,
        "schedule_mode": item.schedule_mode,
        "scheduled_at": item.scheduled_at,
        "publish_retry_after": item.publish_retry_after,
        "permalink": ig_post.permalink if ig_post else None,
        "published_caption": published_caption,
        "slides": slides_payload,
        "story_share_enabled": item.story_share_enabled,
        "story_render_mode": story_opts.render_mode,
        "story_border_color": story_opts.border_color,
        "story_timing_mode": story_opts.timing_mode,
        "story_delay_hours": story_opts.delay_hours,
        "story_scheduled_at": item.story_scheduled_at,
        "story_published_at": item.story_published_at,
        "story_last_error": item.story_last_error,
        "story_status": story_status,
        "story_ig_media_id": story_attempt.ig_media_id if story_attempt and story_attempt.success else None,
        "story_preview_url": f"/api/images/{image.id}/story-preview",
        "image": _image_out(session, image).model_dump(mode="json"),
    }


def _caption_out(row: Caption) -> dict[str, object]:
    return {
        "id": row.id,
        "image_id": row.image_id,
        "style": row.style,
        "language": row.language,
        "text": row.text,
        "status": row.status,
        "created_at": row.created_at,
    }


def _inbox_comment_out(row: InstagramInboxComment) -> dict[str, object]:
    return {
        "id": row.id,
        "ig_comment_id": row.ig_comment_id,
        "ig_media_id": row.ig_media_id,
        "author_username": row.author_username,
        "text": row.text,
        "commented_at": row.commented_at.isoformat() if row.commented_at else None,
        "suggested_reply": row.suggested_reply,
        "status": row.status,
        "replied_at": row.replied_at.isoformat() if row.replied_at else None,
    }


def _related_out(row: RelatedAccount) -> dict[str, object]:
    return {
        "id": row.id,
        "handle": row.handle,
        "display_name": row.display_name,
        "profile_url": row.profile_url,
        "notes": row.notes,
        "why_relevant": row.why_relevant,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def _comment_draft_out(row: CommentDraft) -> dict[str, object]:
    return {
        "id": row.id,
        "image_id": row.image_id,
        "related_account_id": row.related_account_id,
        "target_context": row.target_context,
        "text": row.text,
        "status": row.status,
        "created_at": row.created_at,
    }


def _curation_batch_summary(batch: CurationBatch) -> dict[str, object]:
    from instamagic.services.recommendations.curate import parse_curation_detail_held_back

    return {
        "id": batch.id,
        "library_id": batch.library_id,
        "folder": batch.folder,
        "status": batch.status,
        "limit": batch.limit,
        "detail": batch.detail,
        "held_back": parse_curation_detail_held_back(batch.detail),
        "error": batch.error,
        "created_at": batch.created_at,
        "finished_at": batch.finished_at,
        "pick_count": len(batch.picks) if batch.picks is not None else 0,
    }


def _curation_pick_out(
    session: Session, pick: CurationPick, rated_at: dict[str, datetime] | None = None
) -> dict[str, object]:
    try:
        reasons = json.loads(pick.reasons_json or "[]")
    except json.JSONDecodeError:
        reasons = []
    try:
        tags = json.loads(pick.suggested_tags_json or "[]")
    except json.JSONDecodeError:
        tags = []
    image = _load_image(session, pick.image_id)
    captions = session.scalars(
        select(Caption).where(Caption.image_id == pick.image_id).order_by(Caption.created_at.desc())
    ).all()
    return {
        "id": pick.id,
        "batch_id": pick.batch_id,
        "sort_order": pick.sort_order,
        "gemini_rank": pick.gemini_rank,
        "local_score": pick.local_score,
        "reasons": reasons,
        "suggested_tags": tags,
        "status": pick.status,
        "preferred_caption_id": pick.preferred_caption_id,
        "rated_at": (rated_at or {}).get(str(pick.image_id)),
        "image": _image_out(session, image).model_dump(mode="json"),
        "captions": [_caption_out(row) for row in captions],
    }


def _curation_batch_out(session: Session, batch: CurationBatch) -> dict[str, object]:
    picks = session.scalars(
        select(CurationPick).where(CurationPick.batch_id == batch.id).order_by(CurationPick.sort_order.asc())
    ).all()
    from instamagic.services.recommendations.curate_skipped import latest_rating_times

    rated_at = latest_rating_times(session, [pick.image_id for pick in picks])
    summary = _curation_batch_summary(batch)
    summary["pick_count"] = len(picks)
    summary["picks"] = [_curation_pick_out(session, pick, rated_at) for pick in picks]
    summary["skipped"] = _curation_skipped_out(session, batch, picks)
    return summary


def _skipped_thumbnail_url(image: Image | None) -> str | None:
    """Thumbnail for the why-not list: flagged images only ever get the blurred render."""
    if image is None:
        return None
    if image.model_state == SafetyState.DO_NOT_POST.value:
        if not image.blurs:
            return None  # never the original for a DO_NOT_POST image
        thumb_v = _image_blur_version(image, "blurs/thumb.jpg")
        return f"/api/derivatives/{image.id}/blurred-thumbnail?v={thumb_v}"
    if any(blur.approved for blur in image.blurs or []):
        thumb_v = _image_blur_version(image, "blurs/thumb.jpg")
        return f"/api/derivatives/{image.id}/blurred-thumbnail?v={thumb_v}"
    return f"/api/derivatives/{image.id}/thumbnail" if image.thumbnail_path else None


def _curation_skipped_out(
    session: Session, batch: CurationBatch, picks: list[CurationPick]
) -> dict[str, object] | None:
    from instamagic.services.recommendations.curate_skipped import load_skipped, record_skipped

    data = load_skipped(batch)
    if data is None and batch.status == CurationBatchStatus.READY.value:
        latest_id = session.scalar(select(CurationBatch.id).order_by(CurationBatch.created_at.desc()).limit(1))
        if latest_id == batch.id:
            # Batches created before this feature: explain once, against today's state.
            eligible = collect_eligible(
                session,
                library_id=batch.library_id,
                folder=batch.folder,
                image_ids=None,
                covering_blur_fn=_has_approved_covering_blur,
            )
            picked = [str(pick.image_id) for pick in picks]
            record_skipped(
                session,
                batch,
                eligible_ids=[str(item.image.id) for item in eligible],
                shortlist_ids=picked,
                picked_ids=picked,
                covering_blur_fn=_has_approved_covering_blur,
            )
            data = load_skipped(batch)
            if data is not None:
                data["computed_later"] = True
                batch.skipped_json = json.dumps(data, ensure_ascii=False, default=str)
                session.commit()
    if data is None:
        return None
    for item in data.get("items", []):
        try:
            image = session.get(Image, UUID(str(item.get("image_id"))))
        except ValueError:
            image = None
        item["thumbnail_url"] = _skipped_thumbnail_url(image)
        item["filename"] = image.relative_path.rsplit("/", 1)[-1] if image and image.relative_path else None
    return data


def _curation_derivative_bytes(session: Session, settings: Settings, image: Image) -> bytes | None:
    return _curation_derivative_bytes_impl(session, settings, image)


def _preview_bytes(session: Session, settings: Settings, image: Image, approved: CropSuggestion | None) -> bytes | None:
    from instamagic.services.recommendations.gemini_derivatives import gemini_preview_bytes

    return gemini_preview_bytes(session, settings, image, approved, require_policy=True)


def _derivative_bytes_for_gemini(
    session: Session,
    settings: Settings,
    image: Image,
    approved: CropSuggestion | None,
    *,
    require_policy: bool,
) -> bytes | None:
    return derivative_bytes_for_gemini(session, settings, image, approved, require_policy=require_policy)


def _encode_cursor(image: Image) -> str:
    stamp = image.ingested_at.isoformat()
    raw = f"{stamp}|{image.id}".encode()
    return base64.urlsafe_b64encode(raw).decode()


def _decode_cursor(cursor: str) -> tuple[datetime, UUID]:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
        stamp, image_id = raw.split("|", 1)
        return datetime.fromisoformat(stamp), UUID(image_id)
    except (ValueError, UnicodeError) as exc:
        raise HTTPException(status_code=400, detail="Invalid cursor.") from exc

