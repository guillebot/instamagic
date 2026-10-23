from __future__ import annotations

import os
import time
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import UUID

from instamagic.logging_config import get_logger
from instamagic.models import (
    AdultAttestation,
    BlurCover,
    ContentTag,
    CropSuggestion,
    Image,
    ImageDuplicate,
    ImageRating,
    ImageScore,
    ImageTag,
    JobStatus,
    ProcessingJob,
    SafetyAssessment,
    SafetyRegion,
    SafetyState,
    TagSource,
)
from instamagic.services.blurs.generator import BlurWindow, apply_blur_covers, suggest_blurs
from instamagic.services.geometry import Box
from instamagic.services.images.derivatives import (
    PREVIEW_FILENAME,
    PREVIEW_LONG_EDGE,
    PREVIEW_QUALITY,
    THUMB_FILENAME,
    THUMB_LONG_EDGE,
    THUMB_QUALITY,
    classify_extension,
    crop_image,
    metadata_from_image,
    oriented_rgb,
    read_image,
    save_derivative,
)
from instamagic.services.images.hashes import dhash, file_sha256, hamming
from instamagic.services.images.paths import is_transient_io_error, retry_transient_io
from instamagic.services.safety.analyze import analyze_image
from instamagic.services.safety.decision import DecisionInput, decide
from instamagic.services.safety.labels import CLEAR_SCORE, EXPOSED_LABELS
from PIL import Image as PilImage
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

log = get_logger("pipeline.scan")
_PROGRESS_SECONDS = 1.5
IngestOutcome = Literal["processed", "skipped"]

SEED_TAGS = (
    "portrait",
    "full body",
    "close-up",
    "black and white",
    "lingerie",
    "fashion",
    "silhouette",
    "window light",
    "studio",
    "outdoor",
    "editorial",
    "high key",
    "low key",
    "color",
    "monochrome",
    "dramatic",
    "soft",
    "playful",
    "elegant",
)


def seed_tags(session: Session) -> None:
    existing = set(session.scalars(select(ContentTag.name)))
    for name in SEED_TAGS:
        if name not in existing:
            session.add(ContentTag(name=name))
    session.commit()


def is_attested(session: Session, library_id: UUID) -> bool:
    row = session.scalar(
        select(AdultAttestation)
        .where(AdultAttestation.library_id == library_id)
        .order_by(AdultAttestation.attested_at.desc())
    )
    return bool(row and row.confirmed)


def _relative_to_root(root: Path, path: Path) -> str:
    try:
        relative = path.relative_to(root).as_posix()
    except ValueError:
        relative = path.name
    return relative[:1024]


def _path_under_root(root: Path, path_text: str | None) -> str | None:
    if not path_text:
        return None
    try:
        return Path(path_text).relative_to(root).as_posix()
    except ValueError:
        root_text = str(root).rstrip("/\\")
        text = path_text
        for prefix in (root_text + "/", root_text + "\\", root_text):
            if text.startswith(prefix):
                text = text[len(prefix) :]
                break
        text = text.lstrip("/\\")
        return text or None


def _brief_error(exc: BaseException, root: Path) -> str:
    """Human-readable scan error; never strip library root into a fake absolute path."""
    name = type(exc).__name__
    detail = " ".join(str(exc).replace("\n", " ").split())
    filename = getattr(exc, "filename", None)
    relative = _path_under_root(root, filename if isinstance(filename, str) else None)
    if relative is None:
        # Scrub absolute root without leaving a fake "/relative" absolute path.
        for root_text in {str(root), str(root.resolve())}:
            root_text = root_text.rstrip("/\\")
            if not root_text:
                continue
            detail = detail.replace(f"'{root_text}/", "'").replace(f'"{root_text}/', '"')
            detail = detail.replace(f"'{root_text}'", "'.'").replace(f'"{root_text}"', '"."')
            detail = detail.replace(root_text + "/", "").replace(root_text + "\\", "")
            detail = detail.replace(root_text, ".")
    else:
        if filename:
            detail = detail.replace(str(filename), relative)
        detail = detail.replace(f"'{relative}'", relative).replace(f'"{relative}"', relative)
    hint = ""
    if is_transient_io_error(exc):
        hint = " (flaky library mount; retried, still unavailable)"
    if relative:
        text = f"{name} at {relative}: {detail}{hint}"
    else:
        text = f"{name}: {detail}{hint}"
    text = " ".join(text.split()) or "Scan failed."
    if len(text) > 400:
        text = text[:397] + "..."
    return text


def iter_library_files(root: Path, on_directory=None) -> Iterator[Path]:  # type: ignore[no-untyped-def]
    root_resolved = retry_transient_io(root.resolve)
    walker = os.walk(root, followlinks=False)
    while True:
        try:
            dirpath, dirnames, filenames = retry_transient_io(lambda: next(walker))
        except StopIteration:
            break
        dirnames[:] = sorted(name for name in dirnames if not name.startswith("."))
        if on_directory is not None:
            on_directory(Path(dirpath))
        for name in sorted(filenames):
            if name.startswith("."):
                continue
            path = Path(dirpath) / name
            try:
                resolved = retry_transient_io(path.resolve)
                if os.path.commonpath([str(root_resolved), str(resolved)]) != str(root_resolved):
                    continue
                if not retry_transient_io(resolved.is_file):
                    continue
            except OSError as exc:
                if is_transient_io_error(exc):
                    log.info(
                        "scan_skip_transient_io",
                        relative_path=_relative_to_root(root, path),
                        error_type=type(exc).__name__,
                    )
                continue
            yield path


class _ProgressClock:
    def __init__(self) -> None:
        self.at = 0.0

    def due(self, force: bool = False) -> bool:
        now = time.monotonic()
        if force or now - self.at >= _PROGRESS_SECONDS:
            self.at = now
            return True
        return False


def _summary(job: ProcessingJob) -> str:
    text = (
        "Scan finished. "
        f"seen={job.files_seen} processed={job.files_processed} "
        f"skipped={job.files_skipped} failed={job.files_failed}"
    )
    return text[:512]


def _flush_progress(session: Session, job: ProcessingJob, clock: _ProgressClock, *, force: bool = False) -> None:
    if clock.due(force=force):
        session.commit()


def run_scan(
    session: Session,
    library_id: UUID,
    derivatives_root: Path,
    classifier: object,
    face_detector: object,
    job_id: UUID | None = None,
) -> ProcessingJob:
    from instamagic.models import Library

    library = session.get(Library, library_id)
    if library is None:
        raise ValueError("Library was not found.")
    job = session.get(ProcessingJob, job_id) if job_id else None
    if job is None:
        job = ProcessingJob(kind="scan", status=JobStatus.RUNNING.value, library_id=library_id, detail="Scanning")
        session.add(job)
        session.flush()
    job_pk = job.id
    root = Path(library.root_path)
    # Resume-safe: keep original started_at so elapsed time survives a worker restart.
    # Counters reset for this walk; already-ingested paths are skipped quickly in _ingest_one.
    already = int(
        session.scalar(
            select(func.count()).select_from(Image).where(Image.library_id == library_id, Image.skip_reason.is_(None))
        )
        or 0
    )
    job.status = JobStatus.RUNNING.value
    if job.started_at is None:
        job.started_at = datetime.now(UTC)
    job.finished_at = None
    job.error = None
    job.detail = "Resuming scan" if already else "Listing the library"
    job.current_path = None
    job.files_seen = 0
    job.files_processed = 0
    job.files_skipped = 0
    job.files_failed = 0
    job.files_total = None
    job.current_stage = "listing"
    session.commit()
    log.info(
        "scan_started",
        library_id=str(library_id),
        job_id=str(job_pk),
        already_ingested=already,
        resume=bool(already),
    )
    clock = _ProgressClock()
    since_flush = 0

    def set_stage(stage: str, *, path: Path | None = None, force: bool = False) -> None:
        nonlocal since_flush
        current = session.get(ProcessingJob, job_pk)
        if current is None:
            return
        current.current_stage = stage
        current.status = JobStatus.RUNNING.value
        if path is not None:
            current.current_path = _relative_to_root(root, path) or None
        since_flush += 1
        if force or since_flush >= 5:
            _flush_progress(session, current, clock, force=True)
            since_flush = 0
        else:
            _flush_progress(session, current, clock)

    def note_directory(directory: Path) -> None:
        set_stage("listing", path=directory)

    try:
        if not retry_transient_io(root.is_dir):
            raise FileNotFoundError("Library folder is not available.")
        from instamagic.config import get_settings
        from instamagic.services.images.paths import assert_library_root_mounted

        settings = get_settings()
        if settings.require_external_library_mounts:
            assert_library_root_mounted(root, settings.library_roots())
        attested = is_attested(session, library_id)
        derivatives_root.mkdir(parents=True, exist_ok=True)
        for path in iter_library_files(root, note_directory):
            relative = _relative_to_root(root, path)
            set_stage("checking", path=path)
            try:
                outcome = _ingest_one(
                    session,
                    library_id=library.id,
                    root=root,
                    path=path,
                    derivatives_root=derivatives_root,
                    attested=attested,
                    classifier=classifier,
                    face_detector=face_detector,
                    on_stage=lambda stage, p=path: set_stage(stage, path=p, force=stage == "classify"),
                )
            except Exception as exc:
                session.rollback()
                failed_file = session.get(ProcessingJob, job_pk)
                if failed_file is None:
                    raise
                failed_file.status = JobStatus.RUNNING.value
                failed_file.files_seen += 1
                failed_file.files_failed += 1
                failed_file.current_path = relative or None
                failed_file.current_stage = "failed"
                failed_file.detail = "Scanning"
                session.commit()
                since_flush = 0
                clock.due(force=True)
                log.info("scan_file", relative_path=relative, outcome="failed", error_type=type(exc).__name__)
                continue
            current = session.get(ProcessingJob, job_pk)
            if current is None:
                raise RuntimeError("Scan job disappeared.")
            current.files_seen += 1
            if outcome == "processed":
                current.files_processed += 1
            else:
                current.files_skipped += 1
            current.current_path = relative or None
            current.current_stage = "scanning"
            current.detail = "Scanning"
            current.status = JobStatus.RUNNING.value
            session.commit()
            since_flush = 0
            clock.due(force=True)
            log.info("scan_file", relative_path=relative, outcome=outcome)
        current = session.get(ProcessingJob, job_pk)
        if current is None:
            raise RuntimeError("Scan job disappeared.")
        current.files_total = current.files_seen
        current.detail = "Checking similar photos"
        current.current_path = None
        current.current_stage = "uniqueness"
        session.commit()
        log.info("scan_uniqueness_started", library_id=str(library_id), job_id=str(job_pk))
        _refresh_uniqueness(session, library_id)
        current = session.get(ProcessingJob, job_pk)
        if current is None:
            raise RuntimeError("Scan job disappeared.")
        current.status = JobStatus.SUCCEEDED.value
        current.finished_at = datetime.now(UTC)
        current.current_path = None
        current.current_stage = "done"
        current.files_total = current.files_seen
        current.detail = _summary(current)
        session.commit()
        log.info(
            "scan_finished",
            library_id=str(library_id),
            job_id=str(job_pk),
            files_seen=current.files_seen,
            files_processed=current.files_processed,
            files_skipped=current.files_skipped,
            files_failed=current.files_failed,
        )
    except Exception as exc:
        session.rollback()
        failed = session.get(ProcessingJob, job_pk)
        message = _brief_error(exc, root)
        if failed is not None:
            failed.status = JobStatus.FAILED.value
            failed.detail = message[:512]
            failed.error = message
            failed.finished_at = datetime.now(UTC)
            session.commit()
        log.info("scan_failed", library_id=str(library_id), job_id=str(job_pk), error_type=type(exc).__name__)
        raise
    finished = session.get(ProcessingJob, job_pk)
    if finished is None:
        raise RuntimeError("Scan job disappeared.")
    return finished


def recompute_model_states(session: Session, library_id: UUID) -> None:
    attested = is_attested(session, library_id)
    images = session.scalars(select(Image).where(Image.library_id == library_id)).all()
    for image in images:
        assessment = _latest_assessment(session, image.id)
        if assessment is None:
            continue
        clear = any(region.unsafe and region.score >= CLEAR_SCORE and region.label in EXPOSED_LABELS for region in assessment.regions)
        viable = any(
            crop.aspect_ratio != "original" and crop.contains_face and crop.excludes_unsafe for crop in image.crops
        )
        state = decide(
            DecisionInput(
                attested=attested,
                detector_available=assessment.detector_available,
                uncertain=assessment.uncertain and not clear,
                clear_exposed=clear,
                viable_safety_crop=viable,
            )
        )
        image.model_state = state.value
        assessment.model_state = state.value
    session.commit()


def _ingest_one(
    session: Session,
    *,
    library_id: UUID,
    root: Path,
    path: Path,
    derivatives_root: Path,
    attested: bool,
    classifier: object,
    face_detector: object,
    on_stage: Callable[[str], None] | None = None,
) -> IngestOutcome:
    def stage(name: str) -> None:
        if on_stage is not None:
            on_stage(name)

    kind = classify_extension(path)
    if kind == "ignored":
        return "skipped"
    relative = path.relative_to(root).as_posix()
    stat = retry_transient_io(path.stat)
    existing_path = session.scalar(
        select(Image).where(Image.library_id == library_id, Image.relative_path == relative)
    )
    if existing_path is not None and _unchanged(existing_path, stat.st_size, stat.st_mtime_ns):
        if existing_path.file_mtime_ns is None:
            existing_path.file_mtime_ns = stat.st_mtime_ns
        return "skipped"
    if existing_path is None and session.scalar(
        select(ImageDuplicate.id).where(
            ImageDuplicate.library_id == library_id, ImageDuplicate.relative_path == relative
        )
    ):
        return "skipped"
    stage("hashing")
    sha = retry_transient_io(lambda: file_sha256(path))
    existing_sha = session.scalar(select(Image).where(Image.sha256 == sha))
    if existing_sha is not None and (existing_path is None or existing_sha.id != existing_path.id):
        _record_duplicate(session, existing_sha.id, library_id, relative)
        return "skipped"
    if (
        existing_path is not None
        and existing_path.sha256 == sha
        and existing_path.skip_reason is None
        and existing_path.thumbnail_path
        and existing_path.model_state
    ):
        existing_path.file_size = stat.st_size
        existing_path.file_mtime_ns = stat.st_mtime_ns
        return "skipped"
    size = stat.st_size
    session_name = "" if path.parent == root else path.parent.relative_to(root).as_posix()
    image = existing_path or Image(
        library_id=library_id,
        relative_path=relative,
        filename=path.name,
        file_size=size,
        sha256=sha,
        has_gps=False,
    )
    image.filename = path.name
    image.file_size = size
    image.file_mtime_ns = stat.st_mtime_ns
    image.sha256 = sha
    image.session_name = session_name
    if existing_path is None:
        session.add(image)
        session.flush()
    if kind == "raw":
        image.skip_reason = "raw_not_supported"
        image.thumbnail_path = None
        image.preview_path = None
        return "skipped"
    stage("reading")
    try:
        def _load_pil() -> PilImage.Image:
            with path.open("rb"):
                return read_image(path)

        pil = retry_transient_io(_load_pil)
        meta = metadata_from_image(pil)
        rgb = oriented_rgb(pil)
    except Exception:
        image.skip_reason = "unreadable"
        return "skipped"
    image.skip_reason = None
    image.width = meta.width
    image.height = meta.height
    image.orientation = meta.orientation
    captured = meta.captured_at
    if captured is not None and captured.tzinfo is None:
        captured = captured.replace(tzinfo=UTC)
    image.captured_at = captured
    image.camera = meta.camera
    image.lens = meta.lens
    image.focal_length_mm = meta.focal_length_mm
    image.iso = meta.iso
    image.shutter = meta.shutter
    image.aperture = meta.aperture
    image.has_gps = meta.has_gps
    image.phash = dhash(rgb)
    stage("derivatives")
    thumb_rel = f"{image.id}/{THUMB_FILENAME}"
    preview_rel = f"{image.id}/{PREVIEW_FILENAME}"
    save_derivative(
        rgb,
        derivatives_root / thumb_rel,
        THUMB_LONG_EDGE,
        quality=THUMB_QUALITY,
        progressive=True,
    )
    save_derivative(
        rgb,
        derivatives_root / preview_rel,
        PREVIEW_LONG_EDGE,
        quality=PREVIEW_QUALITY,
        optimize=True,
    )
    image.thumbnail_path = thumb_rel
    image.preview_path = preview_rel
    stage("classify")
    analysis = analyze_image(
        rgb,
        filename=path.name,
        attested=attested,
        classifier=classifier,
        face_detector=face_detector,
    )
    image.model_state = analysis.state.value
    session.execute(delete(CropSuggestion).where(CropSuggestion.image_id == image.id))
    session.execute(delete(BlurCover).where(BlurCover.image_id == image.id))
    session.execute(delete(SafetyAssessment).where(SafetyAssessment.image_id == image.id))
    session.flush()
    stage("crops")
    safe_score = {
        SafetyState.DO_NOT_POST: 0.0,
        SafetyState.SAFE_AFTER_CROP: 0.4,
        SafetyState.REVIEW: 0.2,
        SafetyState.SAFE: 0.8,
    }[analysis.state]
    assessment = SafetyAssessment(
        image_id=image.id,
        nudity_score=analysis.summary["nudity_score"],
        explicit_score=analysis.summary["explicit_score"],
        lingerie_score=analysis.summary["lingerie_score"],
        skin_exposure_score=analysis.summary["skin_exposure_score"],
        face_count=len(analysis.faces),
        instagram_safe_score=safe_score,
        crop_possible=any(crop.aspect_ratio != "original" and crop.excludes_unsafe for crop in analysis.crops),
        manual_review_required=True,
        detector_available=analysis.signals.detector_available,
        uncertain=analysis.signals.uncertain or analysis.signals.has_ambiguous_exposure(),
        model_state=analysis.state.value,
        model_name=analysis.signals.model_name,
    )
    session.add(assessment)
    session.flush()
    for region in analysis.signals.regions:
        unsafe = region.label in EXPOSED_LABELS and region.score >= CLEAR_SCORE
        session.add(
            SafetyRegion(
                assessment_id=assessment.id,
                label=region.label,
                score=region.score,
                x=region.box.x,
                y=region.box.y,
                width=region.box.width,
                height=region.box.height,
                unsafe=unsafe,
            )
        )
    for crop in analysis.crops:
        derivative = None
        if crop.aspect_ratio != "original":
            derivative = f"{image.id}/crops/{crop.aspect_ratio.replace(':', 'x')}.jpg"
            rendered = crop_image(rgb, crop.x, crop.y, crop.width, crop.height)
            save_derivative(rendered, derivatives_root / derivative, 1600, quality=84)
        session.add(
            CropSuggestion(
                image_id=image.id,
                x=crop.x,
                y=crop.y,
                width=crop.width,
                height=crop.height,
                aspect_ratio=crop.aspect_ratio,
                reason=crop.reason,
                contains_face=crop.contains_face,
                excludes_unsafe=crop.excludes_unsafe,
                approved=False,
                derivative_path=derivative,
            )
        )
    # Blur covers are opt-in via POST /blurs/suggest (reuse stored unsafe boxes).
    # Scan still records SafetyRegion boxes; it does not auto-apply covers.
    stage("scoring")
    score = image.score or ImageScore(image_id=image.id, uniqueness=1)
    for key, value in analysis.scores.items():
        setattr(score, key, value)
    if image.score is None:
        session.add(score)
    _apply_color_tag(session, image.id, analysis.monochrome)
    return "processed"


def _unchanged(image: Image, size: int, mtime_ns: int) -> bool:
    if image.file_size != size:
        return False
    if image.file_mtime_ns is not None and image.file_mtime_ns != mtime_ns:
        return False
    if image.skip_reason is not None:
        return True
    return bool(image.thumbnail_path and image.model_state)


def _apply_color_tag(session: Session, image_id: UUID, monochrome: bool) -> None:
    name = "monochrome" if monochrome else "color"
    tag = session.scalar(select(ContentTag).where(ContentTag.name == name))
    if tag is None:
        return
    exists = session.scalar(select(ImageTag).where(ImageTag.image_id == image_id, ImageTag.tag_id == tag.id))
    if exists is None:
        session.add(ImageTag(image_id=image_id, tag_id=tag.id, source=TagSource.HEURISTIC.value))


def _record_duplicate(session: Session, image_id: UUID, library_id: UUID, relative: str) -> None:
    exists = session.scalar(
        select(ImageDuplicate).where(ImageDuplicate.library_id == library_id, ImageDuplicate.relative_path == relative)
    )
    if exists is None:
        session.add(ImageDuplicate(image_id=image_id, library_id=library_id, relative_path=relative))


def _latest_assessment(session: Session, image_id: UUID) -> SafetyAssessment | None:
    return session.scalar(
        select(SafetyAssessment)
        .where(SafetyAssessment.image_id == image_id)
        .order_by(SafetyAssessment.assessed_at.desc())
    )


def _refresh_uniqueness(session: Session, library_id: UUID) -> None:
    images = session.scalars(
        select(Image).where(Image.library_id == library_id, Image.phash.is_not(None), Image.skip_reason.is_(None))
    ).all()
    for image in images:
        distances = [
            hamming(image.phash, other.phash)
            for other in images
            if other.id != image.id
        ]
        usable = [distance for distance in distances if distance is not None]
        uniqueness = 1.0 if not usable else min(1.0, min(usable) / 16)
        if image.score is not None:
            image.score.uniqueness = uniqueness
    session.commit()


def duplicate_count(session: Session, image_id: UUID) -> int:
    return int(
        session.scalar(select(func.count()).select_from(ImageDuplicate).where(ImageDuplicate.image_id == image_id))
        or 0
    )


def rate_image(session: Session, image: Image, rating: str) -> None:
    image.human_rating = rating
    session.add(ImageRating(image_id=image.id, rating=rating))
    session.commit()


def _blur_row(image_id: UUID, window: BlurWindow, derivative: str | None) -> BlurCover:
    return BlurCover(
        image_id=image_id,
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
        covers_unsafe=window.covers_unsafe,
        approved=False,
        derivative_path=derivative,
    )


def clear_exposed_boxes(session: Session, image_id: UUID) -> list[Box]:
    assessment = _latest_assessment(session, image_id)
    if assessment is None:
        return []
    return [
        Box(region.x, region.y, region.width, region.height)
        for region in assessment.regions
        if region.unsafe and region.score >= CLEAR_SCORE and region.label in EXPOSED_LABELS
    ]


def unsafe_model_regions(session: Session, image_id: UUID) -> list[Box]:
    """All regions the latest local assessment marked unsafe (for blur-unlock coverage)."""
    assessment = _latest_assessment(session, image_id)
    if assessment is None:
        return []
    return [
        Box(region.x, region.y, region.width, region.height)
        for region in assessment.regions
        if region.unsafe
    ]


def replace_suggested_blurs(
    session: Session,
    *,
    image: Image,
    rgb: PilImage.Image,
    derivatives_root: Path,
    strength: str = "standard",
) -> list[BlurCover]:
    """Rebuild unapproved blur covers from stored clear-exposed regions (operator opt-in)."""
    if image.width is None or image.height is None:
        raise ValueError("Image dimensions are unknown.")
    unsafe = clear_exposed_boxes(session, image.id)
    if not unsafe:
        unsafe = unsafe_model_regions(session, image.id)
    if not unsafe:
        raise ValueError("No clear exposed regions to cover.")
    windows = suggest_blurs(image.width, image.height, unsafe, strength=strength)
    session.execute(delete(BlurCover).where(BlurCover.image_id == image.id))
    session.flush()
    derivative = None
    if windows:
        derivative = f"{image.id}/blurs/cover.jpg"
        save_derivative(apply_blur_covers(rgb, windows), derivatives_root / derivative, 1600, quality=84)
    rows = [_blur_row(image.id, window, derivative) for window in windows]
    for row in rows:
        session.add(row)
    session.flush()
    return rows


def render_blur_derivative(
    *,
    rgb: PilImage.Image,
    covers: list[BlurCover],
    image_id: UUID,
    derivatives_root: Path,
) -> str | None:
    if not covers:
        return None
    windows = [
        BlurWindow(
            x=cover.x,
            y=cover.y,
            width=cover.width,
            height=cover.height,
            shape=cover.shape,
            feather=cover.feather,
            radius=cover.radius,
            reason=cover.reason,
            covers_unsafe=cover.covers_unsafe,
            strength=cover.strength,
            rotation_deg=cover.rotation_deg,
            corner_radius=cover.corner_radius,
            edge_mode=cover.edge_mode,
        )
        for cover in covers
    ]
    relative = f"{image_id}/blurs/cover.jpg"
    save_derivative(apply_blur_covers(rgb, windows), derivatives_root / relative, 1600, quality=84)
    return relative
