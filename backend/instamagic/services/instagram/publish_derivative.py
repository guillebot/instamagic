"""Publish-file selection: blur-first, crop-from-blurred, integrity checks."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

import cv2
import numpy as np
from instamagic.config import Settings
from instamagic.errors import NotAllowed, NotConfigured, NotFound
from instamagic.models import BlurCover, CropSuggestion, Image, Library
from instamagic.models.enums import SafetyState
from instamagic.services.blurs.generator import BlurWindow, apply_blur_covers
from instamagic.services.blurs.unlock import blur_gate_passes, blur_unlock_effective
from instamagic.services.images.derivatives import crop_image, oriented_rgb, read_image
from instamagic.services.images.paths import safe_join
from instamagic.services.instagram.feed_image import (
    prepare_publish_file_from_path,
    write_instagram_publish_jpeg,
)
from instamagic.services.pipeline import render_blur_derivative
from PIL import Image as PilImage
from sqlalchemy.orm import Session

# Mean |ΔRGB| per channel in a blur ellipse must exceed this vs the unblurred equivalent.
_MIN_BLUR_REGION_MEAN_ABS_DIFF = 4.0
# Laplacian variance in the feather-eroded interior must drop sharply vs the sharp original.
_MIN_LAPLACIAN_REDUCTION_RATIO = 8.0
# Publish interior texture must match a full-strength reference blur (guards partial composites).
_MAX_LAPLACIAN_RATIO_VS_REFERENCE = 1.35
# Entire shape interior (including feather band) must match reference blur compositing.
_MAX_SHAPE_INTERIOR_MEAN_ABS_DIFF_VS_REFERENCE = 8.0

BLUR_COVER_RELATIVE = "blurs/cover.jpg"


def has_any_approved_blur(image: Image) -> bool:
    return any(blur.approved for blur in image.blurs)


def blur_drawn_not_approved_reason(image: Image) -> str | None:
    """Block queue/publish when safe-rated images have unapproved blur regions."""
    from instamagic.models.enums import HumanRating

    if not image.blurs:
        return None
    if any(blur.approved for blur in image.blurs):
        return None
    if image.human_rating in {HumanRating.SAFE.value, HumanRating.EXCELLENT.value}:
        return "Blur drawn but not approved."
    if image.model_state == SafetyState.DO_NOT_POST.value and image.blur_unlock_status != "none":
        return "Blur drawn but not approved."
    return None


def _window_from_row(blur: BlurCover) -> BlurWindow:
    return BlurWindow(
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
        rotation_deg=blur.rotation_deg,
        corner_radius=blur.corner_radius,
        edge_mode=blur.edge_mode,
    )


def _approved_blur_windows(image: Image) -> list[BlurWindow]:
    return [_window_from_row(blur) for blur in image.blurs if blur.approved]


def _load_original_rgb(session: Session, settings: Settings, image: Image) -> PilImage.Image:
    library = session.get(Library, image.library_id)
    if library is None:
        raise NotFound("Library was not found.")
    source = safe_join(Path(library.root_path), image.relative_path)
    if not source.is_file():
        raise NotFound("Original image was not found.")
    return oriented_rgb(read_image(source))


def _scale_crop_to_rgb(
    crop: CropSuggestion, image: Image, rgb: PilImage.Image
) -> tuple[int, int, int, int]:
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
    return x, y, width, height


def _windows_in_crop_space(
    windows: list[BlurWindow], crop_box: tuple[int, int, int, int]
) -> list[BlurWindow]:
    from instamagic.services.blurs.masks import window_for_crop

    cx, cy, _, _ = crop_box
    return [window_for_crop(window, cx, cy) for window in windows]


def _interior_core_masks(size: tuple[int, int], windows: list[BlurWindow]) -> list[np.ndarray]:
    from instamagic.services.blurs.masks import core_mask_array

    masks: list[np.ndarray] = []
    for window in windows:
        if window.width <= 0 or window.height <= 0:
            continue
        core = core_mask_array(size, window)
        if np.any(core):
            masks.append(core)
    return masks


def _shape_interior_masks(size: tuple[int, int], windows: list[BlurWindow]) -> list[np.ndarray]:
    from instamagic.services.blurs.masks import interior_mask_array

    masks: list[np.ndarray] = []
    for window in windows:
        if window.width <= 0 or window.height <= 0:
            continue
        interior = interior_mask_array(size, window)
        if np.any(interior):
            masks.append(interior)
    return masks


def _mean_abs_diff_in_cores(
    publish: np.ndarray, reference: np.ndarray, core_masks: list[np.ndarray]
) -> float:
    total_diff = 0.0
    total_weight = 0.0
    for core in core_masks:
        if not np.any(core):
            continue
        diff = float(np.mean(np.abs(publish[core] - reference[core])))
        weight = float(np.count_nonzero(core))
        total_diff += diff * weight
        total_weight += weight
    if total_weight <= 0:
        return 0.0
    return total_diff / total_weight


def _laplacian_stats_in_cores(rgb: np.ndarray, core_masks: list[np.ndarray]) -> float:
    gray = np.mean(rgb, axis=2).astype(np.float32)
    lap = cv2.Laplacian(gray, cv2.CV_32F, ksize=3)
    total_var = 0.0
    total_weight = 0.0
    for core in core_masks:
        if not np.any(core):
            continue
        vals = lap[core]
        if vals.size == 0:
            continue
        var = float(vals.var())
        weight = float(vals.size)
        total_var += var * weight
        total_weight += weight
    if total_weight <= 0:
        return 0.0
    return total_var / total_weight


def assert_publish_blur_integrity(
    *,
    original_rgb: PilImage.Image,
    publish_rgb: PilImage.Image,
    windows: list[BlurWindow],
    crop_box: tuple[int, int, int, int] | None,
) -> None:
    if not windows:
        return
    if crop_box is not None:
        x, y, width, height = crop_box
        reference = crop_image(original_rgb, x, y, width, height)
        check_windows = _windows_in_crop_space(windows, crop_box)
    else:
        reference = original_rgb
        check_windows = windows
    if not check_windows:
        return
    if publish_rgb.size != reference.size:
        raise NotAllowed("Publish derivative size does not match the unblurred reference.")
    pub = np.asarray(publish_rgb.convert("RGB"), dtype=np.float32)
    ref = np.asarray(reference.convert("RGB"), dtype=np.float32)
    cores = _interior_core_masks(publish_rgb.size, check_windows)
    if not cores:
        return
    mean_diff = _mean_abs_diff_in_cores(pub, ref, cores)
    if mean_diff < _MIN_BLUR_REGION_MEAN_ABS_DIFF:
        raise NotAllowed(
            "Publish derivative failed blur integrity check; refusing to upload an unblurred frame."
        )
    original_lap = _laplacian_stats_in_cores(ref, cores)
    publish_lap = _laplacian_stats_in_cores(pub, cores)
    if original_lap > 1.0:
        reduction = original_lap / max(publish_lap, 1e-6)
        if reduction < _MIN_LAPLACIAN_REDUCTION_RATIO:
            raise NotAllowed(
                "Publish derivative failed blur integrity check (high-frequency detail remains inside blur regions)."
            )
    reference_blurred = apply_blur_covers(reference, check_windows)
    ref_blur = np.asarray(reference_blurred.convert("RGB"), dtype=np.float32)
    reference_lap = _laplacian_stats_in_cores(ref_blur, cores)
    if reference_lap > 1.0:
        ratio = publish_lap / reference_lap
        if ratio > _MAX_LAPLACIAN_RATIO_VS_REFERENCE:
            raise NotAllowed(
                "Publish derivative failed blur integrity check (interior blur weaker than reference)."
            )
    shape_masks = _shape_interior_masks(publish_rgb.size, check_windows)
    shape_mae = _mean_abs_diff_in_cores(pub, ref_blur, shape_masks)
    if shape_mae > _MAX_SHAPE_INTERIOR_MEAN_ABS_DIFF_VS_REFERENCE:
        raise NotAllowed(
            "Publish derivative failed blur integrity check (shape interior does not match reference blur)."
        )


def _rgb_temp_file(rgb: PilImage.Image, settings: Settings, image_id: UUID) -> Path:
    path = settings.derivatives_root / f"{image_id}/publish/_rgb_staging.jpg"
    path.parent.mkdir(parents=True, exist_ok=True)
    write_instagram_publish_jpeg(rgb, path)
    return path


def ensure_publish_derivative(
    session: Session,
    settings: Settings,
    image: Image,
    approved: CropSuggestion | None,
    *,
    carousel_slide: bool = False,
) -> str:
    """Return a derivatives-relative JPEG path suitable for Meta to fetch."""
    if image.model_state == SafetyState.DO_NOT_POST.value and not blur_unlock_effective(session, image):
        raise NotAllowed("DO_NOT_POST requires verified blur unlock before publishing.")
    if not blur_gate_passes(session, image):
        raise NotAllowed("Blur covers are not verified. Blur and verify first.")
    pending = blur_drawn_not_approved_reason(image)
    if pending:
        raise NotAllowed(pending)

    if has_any_approved_blur(image):
        return _publish_with_approved_blurs(
            session, settings, image, approved, carousel_slide=carousel_slide
        )

    if approved is not None:
        if not approved.derivative_path:
            refresh_crop_derivative(session, settings, image, approved)
            session.commit()
        if approved.derivative_path:
            return _instagram_publish_copy(
                settings, image.id, approved.derivative_path, "from-crop", carousel_slide=carousel_slide
            )

    if image.preview_path:
        source = settings.derivatives_root / image.preview_path
        if source.is_file():
            relative = f"{image.id}/publish/feed.jpg"
            dest = settings.derivatives_root / relative
            if not dest.is_file() or dest.stat().st_mtime < source.stat().st_mtime:
                prepare_publish_file_from_path(source, dest, carousel_slide=carousel_slide)
            return relative
    raise NotConfigured("No publishable derivative is available for this image.")


def _publish_with_approved_blurs(
    session: Session,
    settings: Settings,
    image: Image,
    approved: CropSuggestion | None,
    *,
    carousel_slide: bool,
) -> str:
    windows = _approved_blur_windows(image)
    if not windows:
        raise NotAllowed("Blur drawn but not approved.")

    original_rgb = _load_original_rgb(session, settings, image)
    blurred_rgb = apply_blur_covers(original_rgb, windows)
    blur_covers = [blur for blur in image.blurs if blur.approved]
    relative_blur = render_blur_derivative(
        rgb=original_rgb,
        covers=blur_covers,
        image_id=image.id,
        derivatives_root=settings.derivatives_root,
    )
    if not relative_blur:
        raise NotConfigured("Could not render blur derivative.")
    for blur in image.blurs:
        if blur.approved:
            blur.derivative_path = relative_blur

    crop_box: tuple[int, int, int, int] | None = None
    if approved is not None:
        crop_box = _scale_crop_to_rgb(approved, image, blurred_rgb)
        publish_rgb = crop_image(blurred_rgb, *crop_box)
        label = "from-blur-crop"
    else:
        publish_rgb = blurred_rgb
        label = "from-blur"

    assert_publish_blur_integrity(
        original_rgb=original_rgb,
        publish_rgb=publish_rgb,
        windows=windows,
        crop_box=crop_box,
    )

    relative = f"{image.id}/publish/{label}.jpg"
    dest = settings.derivatives_root / relative
    dest.parent.mkdir(parents=True, exist_ok=True)
    if carousel_slide:
        staging = _rgb_temp_file(publish_rgb, settings, image.id)
        prepare_publish_file_from_path(staging, dest, carousel_slide=True)
    else:
        write_instagram_publish_jpeg(publish_rgb, dest)
    return relative


def _instagram_publish_copy(
    settings: Settings,
    image_id: UUID,
    source_relative: str,
    label: str,
    *,
    carousel_slide: bool = False,
) -> str:
    source = settings.derivatives_root / source_relative
    relative = f"{image_id}/publish/{label}.jpg"
    dest = settings.derivatives_root / relative
    if not dest.is_file() or dest.stat().st_mtime < source.stat().st_mtime:
        prepare_publish_file_from_path(source, dest, carousel_slide=carousel_slide)
    return relative


def refresh_crop_derivative(session: Session, settings: Settings, image: Image, crop: CropSuggestion) -> None:
    if has_any_approved_blur(image):
        windows = _approved_blur_windows(image)
        original_rgb = _load_original_rgb(session, settings, image)
        rgb = apply_blur_covers(original_rgb, windows)
    else:
        rgb = _rgb_for_image(session, settings, image)
    x, y, width, height = _scale_crop_to_rgb(crop, image, rgb)
    relative = f"{image.id}/crops/{crop.aspect_ratio.replace(':', 'x')}.jpg"
    rendered = crop_image(rgb, x, y, width, height)
    write_instagram_publish_jpeg(rendered, settings.derivatives_root / relative)
    crop.derivative_path = relative


def _rgb_for_image(session: Session, settings: Settings, image: Image) -> PilImage.Image:
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
