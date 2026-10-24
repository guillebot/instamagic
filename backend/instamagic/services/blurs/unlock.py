"""Blur verification: DO_NOT_POST unlock, and the rule that any blurred photo must be verified."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from instamagic.config import Settings
from instamagic.models import BlurCover, Image, Library
from instamagic.models.enums import BlurUnlockStatus, SafetyState
from instamagic.services.blurs.coverage_settings import BlurCoverageSettings, blur_coverage_settings
from instamagic.services.blurs.generator import (
    BlurWindow,
    apply_blur_covers,
    region_coverages,
)
from instamagic.services.blurs.version import blur_geometry_hash as blur_geometry_version
from instamagic.services.images.derivatives import oriented_rgb, read_image
from instamagic.services.images.paths import safe_join
from instamagic.services.pipeline import (
    clear_exposed_boxes,
    render_blur_derivative,
    unsafe_model_regions,
)
from instamagic.services.safety.signals import SafetySignals
from PIL import Image as PilImage
from sqlalchemy.orm import Session


@dataclass(frozen=True)
class UnlockVerification:
    status: BlurUnlockStatus
    geometry_hash: str | None
    details: dict[str, Any]
    message: str


def blur_geometry_hash(blurs: list[BlurCover]) -> str:
    approved = sorted(
        [blur for blur in blurs if blur.approved],
        key=lambda row: (row.x, row.y, row.width, row.height, str(row.id)),
    )
    payload = [
        {
            "x": blur.x,
            "y": blur.y,
            "width": blur.width,
            "height": blur.height,
            "shape": blur.shape,
            "feather": blur.feather,
            "radius": round(blur.radius, 4),
            "strength": blur.strength,
            "rotation_deg": round(blur.rotation_deg, 4),
            "corner_radius": round(blur.corner_radius, 4),
            # Only non-default modes join the hash so existing (soft) unlock hashes stay valid.
            **({"edge_mode": blur.edge_mode} if (blur.edge_mode or "soft") != "soft" else {}),
        }
        for blur in approved
    ]
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def reset_blur_unlock(image: Image) -> None:
    image.blur_unlock_status = BlurUnlockStatus.PENDING.value
    image.blur_unlock_verified_at = None
    image.blur_unlock_geometry_hash = None
    image.blur_unlock_details_json = None


def _approved_windows(image: Image) -> list[BlurWindow]:
    return [
        BlurWindow(
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
            edge_mode=blur.edge_mode or "soft",
        )
        for blur in image.blurs
        if blur.approved
    ]


def coverage_gate_passes(
    session: Session,
    image: Image,
    gate: BlurCoverageSettings,
) -> bool:
    if image.width is None or image.height is None:
        return False
    windows = _approved_windows(image)
    if not windows:
        return False
    unsafe_boxes = unsafe_model_regions(session, image.id)
    if not unsafe_boxes:
        unsafe_boxes = clear_exposed_boxes(session, image.id)
    if not unsafe_boxes:
        return True
    coverages = region_coverages(
        windows,
        unsafe_boxes,
        (image.width, image.height),
        min_ratio=gate.min_ratio,
        padding=gate.padding_px,
    )
    return all(row.coverage >= gate.min_ratio for row in coverages)


def requires_blur_verification(image: Image) -> bool:
    """A photo the model flagged, or any photo with blur covers, must pass blur verification
    before it can be rated safe/excellent or have any pixels sent to Gemini."""
    return image.model_state == SafetyState.DO_NOT_POST.value or bool(image.blurs)


def blur_unlock_effective(session: Session, image: Image) -> bool:
    if image.model_state != SafetyState.DO_NOT_POST.value:
        return False
    return blur_covers_verified(session, image)


def blur_gate_passes(session: Session, image: Image) -> bool:
    """True when the photo needs no blur verification, or its current covers are verified."""
    if not requires_blur_verification(image):
        return True
    return blur_covers_verified(session, image)


def blur_covers_verified(session: Session, image: Image) -> bool:
    """Verified unlock that still matches the current approved covers (any model_state)."""
    if image.blur_unlock_status != BlurUnlockStatus.VERIFIED.value:
        return False
    if not image.blur_unlock_geometry_hash:
        return False
    if not any(blur.approved for blur in image.blurs):
        return False
    if blur_geometry_hash(image.blurs) != image.blur_unlock_geometry_hash:
        return False
    gate = blur_coverage_settings(session)
    if image.blur_unlock_details_json:
        try:
            details = json.loads(image.blur_unlock_details_json)
        except json.JSONDecodeError:
            details = {}
        verified_min = float(details.get("coverage_min", gate.min_ratio))
        if gate.min_ratio > verified_min:
            return False
    return coverage_gate_passes(session, image, gate)


def unlock_summary_message(session: Session, image: Image) -> str | None:
    if not requires_blur_verification(image):
        return None
    if image.blur_unlock_status == BlurUnlockStatus.VERIFIED.value and blur_covers_verified(session, image):
        return "SAFE · BLURRED (verified)"
    if image.blur_unlock_status == BlurUnlockStatus.FAILED.value and image.blur_unlock_details_json:
        try:
            details = json.loads(image.blur_unlock_details_json)
        except json.JSONDecodeError:
            return "Blur unlock verification failed."
        return details.get("message") or unlock_summary_message_from_parts(
            details.get("uncovered_regions") or [],
            details.get("classifier_detections") or [],
            coverage_min=float(details.get("coverage_min", 0.95)),
        )
    if image.blurs and image.blur_unlock_status == BlurUnlockStatus.PENDING.value:
        return "Blur unlock pending verification."
    if image.blurs:
        return "Blur not verified. Approve the blur to verify it."
    if image.model_state == SafetyState.DO_NOT_POST.value:
        return "Flagged photo: blur and verify first."
    return None


def unlock_summary_message_from_parts(
    uncovered: list[dict],
    detections: list[dict],
    *,
    coverage_min: float = 0.95,
) -> str:
    parts: list[str] = []
    if uncovered:
        pct = ", ".join(f"{round(float(row.get('coverage', 0)) * 100)}%" for row in uncovered)
        parts.append(
            f"coverage check: {len(uncovered)} regions uncovered "
            f"({pct} covered; every flagged area needs {round(coverage_min * 100)}%)"
        )
    if detections:
        labels = ", ".join(sorted({row["label"] for row in detections}))
        parts.append(f"re-check on the blurred render: classifier still detects {labels}")
    if not parts:
        return "Blur unlock verification failed."
    return "Still unsafe: " + " / ".join(parts)


def _classify_render(rgb: PilImage.Image, classifier: object) -> SafetySignals:
    bgr = cv2.cvtColor(np.asarray(rgb.convert("RGB")), cv2.COLOR_RGB2BGR)
    height, width = bgr.shape[:2]
    longest = max(height, width)
    max_edge = 1280
    if longest > max_edge:
        scale = longest / max_edge
        bgr = cv2.resize(bgr, (int(width / scale), int(height / scale)))
    return classifier.classify(bgr, hint="")  # type: ignore[attr-defined]


def verify_blur_unlock(
    session: Session,
    settings: Settings,
    image: Image,
    classifier: object,
) -> UnlockVerification:
    if not image.blurs or not all(blur.approved for blur in image.blurs):
        raise ValueError("All blur covers must be approved before verification.")

    gate = blur_coverage_settings(session)

    library = session.get(Library, image.library_id)
    if library is None:
        raise ValueError("Library was not found.")
    source = safe_join(Path(library.root_path), image.relative_path)
    if not source.is_file():
        raise ValueError("Original image was not found.")
    if image.width is None or image.height is None:
        raise ValueError("Image dimensions are unknown.")

    original_rgb = oriented_rgb(read_image(source))
    windows = _approved_windows(image)
    blurred_rgb = apply_blur_covers(original_rgb, windows)
    render_blur_derivative(
        rgb=original_rgb,
        covers=list(image.blurs),
        image_id=image.id,
        derivatives_root=settings.derivatives_root,
    )

    unsafe_boxes = unsafe_model_regions(session, image.id)
    if not unsafe_boxes:
        unsafe_boxes = clear_exposed_boxes(session, image.id)

    coverages = region_coverages(
        windows,
        unsafe_boxes,
        (image.width, image.height),
        min_ratio=gate.min_ratio,
        padding=gate.padding_px,
    )
    uncovered = [
        {
            "x": row.box.x,
            "y": row.box.y,
            "width": row.box.width,
            "height": row.box.height,
            "coverage": round(row.coverage, 4),
        }
        for row in coverages
        if row.coverage < gate.min_ratio
    ]

    signals = _classify_render(blurred_rgb, classifier)
    detections = [
        {
            "label": region.label,
            "score": round(region.score, 4),
            "x": region.box.x,
            "y": region.box.y,
            "width": region.box.width,
            "height": region.box.height,
        }
        for region in signals.clear_exposed()
    ]

    geometry_hash = blur_geometry_hash(image.blurs)
    details: dict[str, Any] = {
        "uncovered_regions": uncovered,
        "classifier_detections": detections,
        "coverage_min": gate.min_ratio,
        "coverage_threshold_percent": gate.threshold_percent,
        "coverage_padding_px": gate.padding_px,
        "model_name": signals.model_name,
        "geometry_version": blur_geometry_version(list(image.blurs)),
    }

    if uncovered or detections:
        message = unlock_summary_message_from_parts(uncovered, detections, coverage_min=gate.min_ratio)
        details["message"] = message
        return UnlockVerification(
            status=BlurUnlockStatus.FAILED,
            geometry_hash=None,
            details=details,
            message=message,
        )

    details["message"] = "SAFE · BLURRED (verified)"
    return UnlockVerification(
        status=BlurUnlockStatus.VERIFIED,
        geometry_hash=geometry_hash,
        details=details,
        message=details["message"],
    )


def apply_unlock_result(image: Image, result: UnlockVerification) -> None:
    image.blur_unlock_status = result.status.value
    image.blur_unlock_details_json = json.dumps(result.details)
    if result.status == BlurUnlockStatus.VERIFIED:
        image.blur_unlock_verified_at = datetime.now(UTC)
        image.blur_unlock_geometry_hash = result.geometry_hash
    else:
        image.blur_unlock_verified_at = None
        image.blur_unlock_geometry_hash = None
