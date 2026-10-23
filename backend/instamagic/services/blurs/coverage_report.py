"""Per-flagged-region coverage report for the blur editor (read-only, same math as the gate).

Uses exactly the same inputs and arithmetic as ``verify_blur_unlock``'s coverage check
(flagged regions from the latest local assessment of the *original* image, padded by
``_REGION_PADDING``, mean of the feathered cover mask must be >= ``_COVERAGE_MIN``).
It only rasterizes the area around the flagged boxes so it is cheap enough to refresh on
every edit. Nothing here changes what the gate accepts.
"""

from __future__ import annotations

import json
from typing import Any

import numpy as np
from instamagic.models import BlurCover, Image, SafetyAssessment
from instamagic.models.enums import BlurUnlockStatus, SafetyState
from instamagic.services.blurs.coverage_settings import blur_coverage_settings
from instamagic.services.blurs.generator import BlurWindow
from instamagic.services.blurs.masks import (
    combined_coverage_mask,
    interior_mask_array,
    translate_window,
)
from instamagic.services.blurs.unlock import blur_unlock_effective
from instamagic.services.blurs.version import blur_geometry_hash as geometry_version
from instamagic.services.geometry import Box
from sqlalchemy import select
from sqlalchemy.orm import Session

# A strip (row/column) of the padded box counts as "missed" below this mean coverage.
_STRIP_MISSED = 0.5


def window_from_cover(blur: BlurCover) -> BlurWindow:
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
        edge_mode=getattr(blur, "edge_mode", None) or "soft",
    )


def flagged_regions(session: Session, image_id: Any) -> list[tuple[str, float, Box]]:
    """Unsafe regions of the latest assessment (the set ``unsafe_model_regions`` returns)."""
    assessment = session.scalar(
        select(SafetyAssessment)
        .where(SafetyAssessment.image_id == image_id)
        .order_by(SafetyAssessment.assessed_at.desc())
    )
    if assessment is None:
        return []
    return [
        (region.label, float(region.score), Box(region.x, region.y, region.width, region.height))
        for region in assessment.regions
        if region.unsafe
    ]


def _padded(box: Box, size: tuple[int, int], padding: int) -> tuple[int, int, int, int]:
    width, height = size
    return (
        max(0, box.x - padding),
        max(0, box.y - padding),
        min(width, box.x + box.width + padding),
        min(height, box.y + box.height + padding),
    )


def _leading(values: np.ndarray) -> int:
    missed = values < _STRIP_MISSED
    if not missed.any() or missed.all():
        return int(missed.sum())
    return int(np.argmax(~missed))


def region_coverage_rows(
    windows: list[BlurWindow],
    boxes: list[Box],
    image_size: tuple[int, int],
    *,
    padding: int = 6,
) -> list[dict[str, Any]]:
    """Coverage per box, identical to ``region_coverages_rotated`` but on a local window."""
    if not boxes:
        return []
    padded = [_padded(box, image_size, padding) for box in boxes]
    valid = [p for p in padded if p[2] > p[0] and p[3] > p[1]]
    rows: list[dict[str, Any]] = []
    if not valid or not windows:
        for box in boxes:
            rows.append({"box": box, "coverage": 0.0, "inside_outline": 0.0, "missed": None})
        return rows
    bx0 = min(p[0] for p in valid)
    by0 = min(p[1] for p in valid)
    bx1 = max(p[2] for p in valid)
    by1 = max(p[3] for p in valid)
    local_size = (bx1 - bx0, by1 - by0)
    local = [translate_window(window, -bx0, -by0) for window in windows]
    cover = combined_coverage_mask(local_size, local)
    outline = np.zeros((local_size[1], local_size[0]), dtype=bool)
    for window in local:
        outline |= interior_mask_array(local_size, window)
    for box, (x0, y0, x1, y1) in zip(boxes, padded, strict=True):
        if x1 <= x0 or y1 <= y0:
            rows.append({"box": box, "coverage": 0.0, "inside_outline": 0.0, "missed": None})
            continue
        patch = cover[y0 - by0 : y1 - by0, x0 - bx0 : x1 - bx0]
        inside = outline[y0 - by0 : y1 - by0, x0 - bx0 : x1 - bx0]
        row_mean = patch.mean(axis=1)
        col_mean = patch.mean(axis=0)
        rows.append(
            {
                "box": box,
                "coverage": float(np.mean(patch)),
                "inside_outline": float(np.mean(inside)),
                "missed": {
                    "top": _leading(row_mean),
                    "bottom": _leading(row_mean[::-1]),
                    "left": _leading(col_mean),
                    "right": _leading(col_mean[::-1]),
                },
            }
        )
    return rows


def _verification_checks(session: Session, image: Image) -> dict[str, Any]:
    details: dict[str, Any] = {}
    if image.blur_unlock_details_json:
        try:
            details = json.loads(image.blur_unlock_details_json)
        except json.JSONDecodeError:
            details = {}
    current = geometry_version(list(image.blurs)) if image.blurs else None
    checked = details.get("geometry_version")
    status = image.blur_unlock_status or BlurUnlockStatus.NONE.value
    effective = blur_unlock_effective(session, image)
    if effective:
        recheck = "pass"
    elif status == BlurUnlockStatus.FAILED.value and checked and checked == current:
        recheck = "fail" if details.get("classifier_detections") else "pass"
    else:
        recheck = "not_run"
    return {
        "status": status,
        "effective": effective,
        "current_geometry": bool(effective or (checked and checked == current)),
        "recheck": recheck,
        "classifier_detections": details.get("classifier_detections") or [],
        "message": details.get("message"),
    }


def blur_coverage_report(session: Session, image: Image) -> dict[str, Any]:
    gate = blur_coverage_settings(session)
    regions = flagged_regions(session, image.id)
    blocked = image.model_state == SafetyState.DO_NOT_POST.value
    out: dict[str, Any] = {
        "min_coverage": gate.min_ratio,
        "coverage_threshold_percent": gate.threshold_percent,
        "padding": gate.padding_px,
        "do_not_post": blocked,
        "regions": [],
        "all_covered": False,
        "coverage_check": "none",
        "verification": _verification_checks(session, image),
    }
    if image.width is None or image.height is None or not regions:
        out["all_covered"] = not regions
        out["coverage_check"] = "pass" if not regions else "unknown"
        return out
    windows = [window_from_cover(blur) for blur in image.blurs]
    rows = region_coverage_rows(
        windows,
        [box for _, _, box in regions],
        (image.width, image.height),
        padding=gate.padding_px,
    )
    report = []
    for (label, score, box), row in zip(regions, rows, strict=True):
        coverage = round(row["coverage"], 4)
        report.append(
            {
                "label": label,
                "score": round(score, 4),
                "x": box.x,
                "y": box.y,
                "width": box.width,
                "height": box.height,
                "coverage": coverage,
                "inside_outline": round(row["inside_outline"], 4),
                "covered": row["coverage"] >= gate.min_ratio,
                "missed_px": row["missed"],
            }
        )
    out["regions"] = report
    out["all_covered"] = all(row["covered"] for row in report)
    out["coverage_check"] = "pass" if out["all_covered"] else "fail"
    return out


def readiness_blur_reason(session: Session, image: Image) -> str | None:
    """Name the failing blur check for DO_NOT_POST images (adds detail; never unblocks)."""
    if image.model_state != SafetyState.DO_NOT_POST.value or not image.blurs:
        return None
    if blur_unlock_effective(session, image):
        return None
    status = image.blur_unlock_status
    if status == BlurUnlockStatus.FAILED.value and image.blur_unlock_details_json:
        try:
            details = json.loads(image.blur_unlock_details_json)
        except json.JSONDecodeError:
            details = {}
        return f"Blur verification failed — {details.get('message') or 'see the blur editor'}"
    return (
        "Blur covers not verified for the current geometry — approve them to run the coverage "
        "check and the re-check on the blurred render."
    )
