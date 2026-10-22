from __future__ import annotations

import base64
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from instamagic.api.deps import db_dep, require_session, settings_dep
from instamagic.auth import (
    COOKIE_NAME,
    check_password,
    clear_login_failures,
    client_ip,
    login_is_blocked,
    record_login_failure,
    sign_session,
)
from instamagic.config import ATTESTATION_STATEMENT, Settings
from instamagic.errors import GeminiBlocked, NotAllowed, NotConfigured, NotFound
from instamagic.models import (
    AdultAttestation,
    BlurCover,
    Caption,
    CaptionStatus,
    ContentTag,
    CropSuggestion,
    GeminiRequest,
    HumanRating,
    Image,
    ImageTag,
    InstagramPost,
    JobStatus,
    Library,
    ProcessingJob,
    PublishingQueueItem,
    QueueState,
    SafetyState,
    TagSource,
)
from instamagic.schemas.dto import (
    AttestIn,
    BlurCreateIn,
    BlurOut,
    BlurStrengthIn,
    CandidateOut,
    CaptionIn,
    CaptionUpdate,
    CropApproveIn,
    CropOut,
    FolderListing,
    ImageOut,
    ImagePage,
    JobOut,
    LibraryIn,
    LoginIn,
    QueueIn,
    QueuesOut,
    RatingIn,
    TagIn,
)
from instamagic.services.blurs.generator import (
    BlurWindow,
    covers_unsafe_boxes,
    manual_blur,
    with_strength,
)
from instamagic.services.crops.generator import validate_crop_window, window_excludes_unsafe
from instamagic.services.gemini.context import build_context
from instamagic.services.geometry import Box
from instamagic.services.images.derivatives import (
    crop_image,
    oriented_rgb,
    read_image,
    save_derivative,
)
from instamagic.services.images.folders import escape_like, list_folders, normalize_folder
from instamagic.services.images.paths import (
    assert_library_root,
    assert_library_root_mounted,
    safe_join,
)
from instamagic.services.images.presentation import effective_state
from instamagic.services.pipeline import (
    clear_exposed_boxes,
    duplicate_count,
    is_attested,
    rate_image,
    recompute_model_states,
    render_blur_derivative,
    run_scan,
)
from instamagic.services.queues import snapshot_queues
from instamagic.services.recommendations.gates import publishing_gate
from instamagic.services.recommendations.rank import CandidateInput, rank_candidates
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session, selectinload

router = APIRouter()


def _image_out(session: Session, image: Image) -> ImageOut:
    approved_queue = next(iter(image.queue_items), None) if image.queue_items else None
    has_approved_crop = any(crop.approved for crop in image.crops) if image.crops is not None else False
    return ImageOut(
        id=image.id,
        filename=image.filename,
        relative_path=image.relative_path,
        width=image.width,
        height=image.height,
        orientation=image.orientation,
        captured_at=image.captured_at,
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
        effective_state=effective_state(image.human_rating, image.model_state),
        aesthetic_score=image.score.aesthetic_score if image.score else None,
        queued=bool(image.queue_items),
        queue_state=approved_queue.state if approved_queue else None,
        duplicate_count=duplicate_count(session, image.id),
        has_approved_crop=has_approved_crop,
        thumbnail_url=f"/api/derivatives/{image.id}/thumbnail" if image.thumbnail_path else None,
        preview_url=f"/api/derivatives/{image.id}/preview" if image.preview_path else None,
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
def health() -> dict[str, str]:
    return {"status": "ok"}


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
    response.set_cookie(
        COOKIE_NAME,
        sign_session(settings),
        max_age=settings.session_ttl_seconds,
        httponly=True,
        samesite="lax",
        secure=settings.session_cookie_secure(),
        path="/",
    )
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
    return {
        "gemini_policy": settings.gemini_image_policy,
        "gemini_configured": settings.gemini_provider == "fake" or bool(settings.gemini_api_key),
        "auto_publish": False,
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
            job_timeout=60 * 60 * 12,
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
        .options(selectinload(Image.score), selectinload(Image.queue_items), selectinload(Image.crops))
        .order_by(Image.ingested_at.desc(), Image.id.desc())
    )
    if library_id:
        stmt = stmt.where(Image.library_id == library_id)
    if model_state:
        stmt = stmt.where(Image.model_state == model_state)
    if human_rating:
        stmt = stmt.where(Image.human_rating == human_rating)
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


@router.post("/api/images/{image_id}/rating", response_model=ImageOut)
def rate(
    image_id: UUID,
    body: RatingIn,
    _auth: None = Depends(require_session),
    session: Session = db_dep,
) -> ImageOut:
    image = _load_image(session, image_id)
    try:
        rating = body.normalized()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    rate_image(session, image, rating)
    session.refresh(image)
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
    session.commit()
    return _crop_out(image, crop)


@router.get("/api/images/{image_id}/blurs", response_model=list[BlurOut])
def list_blurs(image_id: UUID, _auth: None = Depends(require_session), session: Session = db_dep) -> list[BlurOut]:
    image = _load_image(session, image_id)
    return [_blur_out(image, blur) for blur in image.blurs]


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
        feather=window.feather,
        radius=window.radius,
        strength=window.strength,
        reason=window.reason,
        covers_unsafe=False,
        approved=False,
    )
    session.add(row)
    session.flush()
    _refresh_blur_derivative(session, settings, image)
    session.commit()
    session.refresh(row)
    return _blur_out(image, row)


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
    session.commit()
    return [_blur_out(image, blur) for blur in image.blurs]


@router.post("/api/images/{image_id}/blurs/approve", response_model=list[BlurOut])
def approve_blurs(
    image_id: UUID,
    _auth: None = Depends(require_session),
    session: Session = db_dep,
) -> list[BlurOut]:
    image = _load_image(session, image_id)
    if not image.blurs:
        raise NotAllowed("No blur covers are available.")
    unsafe = clear_exposed_boxes(session, image.id)
    windows = [_window_from_row(blur) for blur in image.blurs]
    if unsafe and not covers_unsafe_boxes(windows, unsafe):
        raise NotAllowed("These blur covers do not fully cover every detected unsafe region.")
    for crop in image.crops:
        crop.approved = False
    for blur in image.blurs:
        blur.approved = True
    session.commit()
    return [_blur_out(image, blur) for blur in image.blurs]


@router.get("/api/derivatives/{image_id}/{kind}")
def derivative(
    image_id: UUID,
    kind: str,
    _auth: None = Depends(require_session),
    session: Session = db_dep,
    settings: Settings = Depends(settings_dep),
) -> FileResponse:
    image = session.get(Image, image_id)
    if image is None:
        raise NotFound("Image was not found.")
    relative = image.thumbnail_path if kind == "thumbnail" else image.preview_path if kind == "preview" else None
    if relative is None:
        raise NotFound("Derivative was not found.")
    path = safe_join(settings.derivatives_root, relative)
    if not path.is_file():
        raise NotFound("Derivative was not found.")
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=3600"})


@router.get("/api/derivatives/{image_id}/crops/{crop_id}")
def crop_derivative(
    image_id: UUID,
    crop_id: UUID,
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
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=3600"})


@router.get("/api/derivatives/{image_id}/blurs/{blur_id}")
def blur_derivative(
    image_id: UUID,
    blur_id: UUID,
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
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=3600"})


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


@router.post("/api/queue", status_code=201)
def enqueue(
    body: QueueIn,
    _auth: None = Depends(require_session),
    session: Session = db_dep,
) -> dict[str, object]:
    image = _load_image(session, body.image_id)
    approved = next((crop for crop in image.crops if crop.approved), None)
    covering_blur = _has_approved_covering_blur(session, image)
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
    session.add(item)
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
    item.state = QueueState.APPROVED.value
    item.approved_at = datetime.now(UTC)
    session.commit()
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
    context = build_context(
        attested=is_attested(session, image.library_id),
        human_rating=image.human_rating,
        model_state=image.model_state,
        needs_crop=image.model_state == SafetyState.SAFE_AFTER_CROP.value,
        has_approved_crop=approved is not None,
        has_approved_covering_blur=covering_blur,
        policy=settings.gemini_image_policy,
        operator_confirmed=body.confirmed,
        content_tags=tags,
        composition=composition,
        mood=", ".join(mood_names) if mood_names else "unspecified",
        safe_description=description,
        crop=crop_payload if image.model_state == SafetyState.SAFE_AFTER_CROP.value and approved else None,
    )
    pixels = _preview_bytes(settings, image, approved)
    audit = GeminiRequest(
        image_id=image.id,
        policy=settings.gemini_image_policy,
        context_sha256=context.sha256(),
        context_json=context.canonical_json(),
        status="pending",
    )
    session.add(audit)
    session.commit()
    try:
        batch = request.app.state.gemini.generate_captions(
            context, policy=settings.gemini_image_policy, image_bytes=pixels
        )
    except (GeminiBlocked, NotConfigured):
        audit.status = "failed"
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


@router.get("/api/candidates", response_model=list[CandidateOut])
def candidates(
    limit: int = Query(default=20, ge=1, le=50),
    _auth: None = Depends(require_session),
    session: Session = db_dep,
) -> list[CandidateOut]:
    rows = session.scalars(
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
        )
    ).all()
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
            )
        )
        images[str(image.id)] = image
        aspects[str(image.id)] = approved.aspect_ratio if approved else None
    ranked = rank_candidates(inputs, limit=limit)
    return [
        CandidateOut(
            image=_image_out(session, images[item.image_id]),
            score=item.score,
            reasons=item.reasons,
            held_back=item.held_back,
            crop_aspect=aspects.get(item.image_id),
            model_state=images[item.image_id].model_state,
        )
        for item in ranked
    ]


@router.get("/api/instagram/status")
def instagram_status(_auth: None = Depends(require_session)) -> dict[str, object]:
    from instamagic.services.instagram.client import InstagramClient

    client = InstagramClient()
    return {"configured": client.configured(), "auto_publish": False}


def _crop_out(image: Image, crop: CropSuggestion) -> CropOut:
    preview = None
    if crop.derivative_path:
        preview = f"/api/derivatives/{image.id}/crops/{crop.id}"
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


def _blur_out(image: Image, blur: BlurCover) -> BlurOut:
    preview = None
    if blur.derivative_path:
        preview = f"/api/derivatives/{image.id}/blurs/{blur.id}"
    return BlurOut(
        id=blur.id,
        x=blur.x,
        y=blur.y,
        width=blur.width,
        height=blur.height,
        shape=blur.shape,
        feather=blur.feather,
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
    )


def _has_approved_covering_blur(session: Session, image: Image) -> bool:
    approved = [blur for blur in image.blurs if blur.approved]
    if not approved:
        return False
    unsafe = clear_exposed_boxes(session, image.id)
    if not unsafe:
        return True
    return covers_unsafe_boxes([_window_from_row(blur) for blur in approved], unsafe)


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
    rgb = _rgb_for_image(session, settings, image)
    x, y, width, height = crop.x, crop.y, crop.width, crop.height
    if image.width and image.height and rgb.size != (image.width, image.height):
        scale_x = rgb.size[0] / image.width
        scale_y = rgb.size[1] / image.height
        x = max(0, int(round(x * scale_x)))
        y = max(0, int(round(y * scale_y)))
        width = max(1, int(round(width * scale_x)))
        height = max(1, int(round(height * scale_y)))
        width = min(width, rgb.size[0] - x)
        height = min(height, rgb.size[1] - y)
    relative = f"{image.id}/crops/{crop.aspect_ratio.replace(':', 'x')}.jpg"
    rendered = crop_image(rgb, x, y, width, height)
    save_derivative(rendered, settings.derivatives_root / relative, 1600, quality=84)
    crop.derivative_path = relative


def _queue_out(session: Session, item: PublishingQueueItem) -> dict[str, object]:
    image = _load_image(session, item.image_id)
    return {
        "id": item.id,
        "state": item.state,
        "crop_id": item.crop_id,
        "created_at": item.created_at,
        "approved_at": item.approved_at,
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


def _preview_bytes(settings: Settings, image: Image, approved: CropSuggestion | None) -> bytes | None:
    if settings.gemini_image_policy != "safe_previews":
        return None
    relative = None
    if image.model_state == SafetyState.SAFE_AFTER_CROP.value:
        approved_blur = next((blur for blur in image.blurs if blur.approved and blur.derivative_path), None)
        if approved is not None and approved.derivative_path:
            relative = approved.derivative_path
        elif approved_blur is not None:
            relative = approved_blur.derivative_path
        else:
            raise GeminiBlocked("SAFE_AFTER_CROP requires an approved crop or blur derivative.")
    else:
        relative = image.preview_path
    if not relative:
        return None
    path = safe_join(settings.derivatives_root, relative)
    if not path.is_file():
        return None
    return path.read_bytes()


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

