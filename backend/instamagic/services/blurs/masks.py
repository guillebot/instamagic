"""Rotated ellipse / rounded-rectangle masks and coverage rasterization."""

from __future__ import annotations

import math

import cv2
import numpy as np
from instamagic.services.blurs.generator import BlurWindow
from instamagic.services.geometry import Box
from PIL import Image


def window_center(window: BlurWindow) -> tuple[float, float]:
    return window.x + window.width / 2, window.y + window.height / 2


def _rotate_point(px: float, py: float, cx: float, cy: float, theta_rad: float) -> tuple[float, float]:
    cos_t = math.cos(theta_rad)
    sin_t = math.sin(theta_rad)
    dx = px - cx
    dy = py - cy
    return cx + dx * cos_t - dy * sin_t, cy + dx * sin_t + dy * cos_t


def ellipse_polygon(window: BlurWindow, segments: int = 72) -> np.ndarray:
    cx, cy = window_center(window)
    rx = window.width / 2
    ry = window.height / 2
    theta = math.radians(window.rotation_deg)
    angles = np.linspace(0, 2 * math.pi, segments, endpoint=False)
    pts = []
    for angle in angles:
        lx = rx * math.cos(angle)
        ly = ry * math.sin(angle)
        gx = cx + lx * math.cos(theta) - ly * math.sin(theta)
        gy = cy + lx * math.sin(theta) + ly * math.cos(theta)
        pts.append([gx, gy])
    return np.asarray(pts, dtype=np.float32)


def rounded_rect_polygon(window: BlurWindow, segments_per_corner: int = 12) -> np.ndarray:
    cx, cy = window_center(window)
    hw = window.width / 2
    hh = window.height / 2
    r = min(window.corner_radius, hw, hh)
    theta = math.radians(window.rotation_deg)

    def local_to_global(lx: float, ly: float) -> tuple[float, float]:
        return _rotate_point(cx + lx, cy + ly, cx, cy, theta)

    if r <= 0:
        corners = [(-hw, -hh), (hw, -hh), (hw, hh), (-hw, hh)]
        return np.asarray([local_to_global(x, y) for x, y in corners], dtype=np.float32)

    pts: list[tuple[float, float]] = []
    local_corners = [
        (hw - r, -hh + r, -math.pi / 2, 0),
        (hw - r, hh - r, 0, math.pi / 2),
        (-hw + r, hh - r, math.pi / 2, math.pi),
        (-hw + r, -hh + r, math.pi, 3 * math.pi / 2),
    ]
    for lcx, lcy, a0, a1 in local_corners:
        for i in range(segments_per_corner):
            t = i / segments_per_corner
            ang = a0 + t * (a1 - a0)
            lx = lcx + r * math.cos(ang)
            ly = lcy + r * math.sin(ang)
            pts.append(local_to_global(lx, ly))
    return np.asarray(pts, dtype=np.float32)


def shape_polygon(window: BlurWindow) -> np.ndarray:
    if window.shape == "rectangle":
        return rounded_rect_polygon(window)
    return ellipse_polygon(window)


def rasterize_polygon(size: tuple[int, int], polygon: np.ndarray) -> np.ndarray:
    width, height = size
    mask = np.zeros((height, width), dtype=np.uint8)
    if polygon.size == 0:
        return mask
    pts = np.round(polygon).astype(np.int32)
    cv2.fillPoly(mask, [pts], 255)
    return mask


def _local_coords_grid(
    size: tuple[int, int], cx: float, cy: float, theta_rad: float
) -> tuple[np.ndarray, np.ndarray]:
    width, height = size
    yy, xx = np.mgrid[0:height, 0:width]
    dx = xx.astype(np.float64) - cx
    dy = yy.astype(np.float64) - cy
    cos_t = math.cos(theta_rad)
    sin_t = math.sin(theta_rad)
    lx = dx * cos_t + dy * sin_t
    ly = -dx * sin_t + dy * cos_t
    return lx, ly


def _antialias_width_norm(min_axis: float, pixels: float = 1.5) -> float:
    return pixels / max(min_axis, 1e-6)


def _hard_mask_from_normalized_dist(dist: np.ndarray, min_axis: float) -> np.ndarray:
    """Uniform 1.0 inside shape; ~1px linear antialias at the boundary only."""
    aa = _antialias_width_norm(min_axis)
    mask = np.zeros(dist.shape, dtype=np.float32)
    inner = 1.0 - aa
    solid = dist <= inner
    ring = (dist > inner) & (dist <= 1.0 + aa)
    mask[solid] = 1.0
    if np.any(ring):
        t = (dist[ring] - inner) / max(2.0 * aa, 1e-6)
        mask[ring] = np.clip(1.0 - t, 0.0, 1.0)
    return mask


def _feather_mask_from_normalized_dist(
    dist: np.ndarray, feather: int, min_axis: float
) -> np.ndarray:
    """Build float mask: 1 in opaque core, cosine ring to boundary at dist==1, 0 outside."""
    inner = max(0.0, 1.0 - feather / max(min_axis, 1e-6))
    mask = np.zeros(dist.shape, dtype=np.float32)
    solid = dist <= inner
    ring = (dist > inner) & (dist <= 1.0)
    mask[solid] = 1.0
    if np.any(ring) and inner < 1.0:
        t = (dist[ring] - inner) / (1.0 - inner)
        mask[ring] = 0.5 * (1.0 + np.cos(math.pi * t))
    return mask


def _ellipse_normalized_dist(
    size: tuple[int, int], window: BlurWindow
) -> tuple[np.ndarray, float]:
    cx, cy = window_center(window)
    rx = max(window.width / 2, 1e-6)
    ry = max(window.height / 2, 1e-6)
    theta = math.radians(window.rotation_deg)
    lx, ly = _local_coords_grid(size, cx, cy, theta)
    dist = np.sqrt((lx / rx) ** 2 + (ly / ry) ** 2)
    return dist.astype(np.float32), float(min(rx, ry))


def _rectangle_normalized_dist(
    size: tuple[int, int], window: BlurWindow
) -> tuple[np.ndarray, float]:
    cx, cy = window_center(window)
    hw = max(window.width / 2, 1e-6)
    hh = max(window.height / 2, 1e-6)
    r = min(window.corner_radius, hw, hh)
    theta = math.radians(window.rotation_deg)
    lx, ly = _local_coords_grid(size, cx, cy, theta)
    if r <= 0:
        dist = np.maximum(np.abs(lx) / hw, np.abs(ly) / hh)
        return dist.astype(np.float32), float(min(hw, hh))
    qx = np.abs(lx) - (hw - r)
    qy = np.abs(ly) - (hh - r)
    outside = np.sqrt(np.maximum(qx, 0.0) ** 2 + np.maximum(qy, 0.0) ** 2)
    inside = np.minimum(np.maximum(qx, qy), 0.0)
    sdf = (outside + inside - r).astype(np.float32)
    inscribed = max(min(hw, hh) - r, 1e-6)
    dist = np.ones_like(sdf, dtype=np.float32)
    interior = sdf <= 0
    dist[interior] = 1.0 - (-sdf[interior] / inscribed)
    dist[~interior] = np.maximum(dist[~interior], 1.0 + sdf[~interior] / inscribed)
    return dist, float(min(hw, hh))


def _normalized_shape_dist(size: tuple[int, int], window: BlurWindow) -> tuple[np.ndarray, float]:
    if window.shape == "ellipse":
        return _ellipse_normalized_dist(size, window)
    return _rectangle_normalized_dist(size, window)


def feathered_shape_mask(size: tuple[int, int], window: BlurWindow) -> Image.Image:
    """L mask with opaque core and cosine feather band (clipped to image bounds)."""
    edge_mode = getattr(window, "edge_mode", "soft") or "soft"
    if edge_mode == "hard":
        dist, min_axis = _normalized_shape_dist(size, window)
        mask = _hard_mask_from_normalized_dist(dist, min_axis)
        return Image.fromarray((mask * 255.0).astype(np.uint8), mode="L")
    if (
        window.shape == "ellipse"
        and window.rotation_deg == 0.0
        and window.corner_radius == 0.0
    ):
        from instamagic.services.blurs.generator import feathered_ellipse_mask

        return feathered_ellipse_mask(
            size,
            x=window.x,
            y=window.y,
            width=window.width,
            height=window.height,
            feather=window.feather,
        )
    feather = max(0, int(window.feather))
    dist, min_axis = _normalized_shape_dist(size, window)
    if feather <= 0:
        mask = (dist <= 1.0).astype(np.float32)
    else:
        mask = _feather_mask_from_normalized_dist(dist, feather, min_axis)
    return Image.fromarray((mask * 255.0).astype(np.uint8), mode="L")


def core_mask_array(size: tuple[int, int], window: BlurWindow) -> np.ndarray:
    """Boolean core (opaque interior) for containment and integrity checks."""
    edge_mode = getattr(window, "edge_mode", "soft") or "soft"
    dist, min_axis = _normalized_shape_dist(size, window)
    if edge_mode == "hard":
        aa = _antialias_width_norm(min_axis, pixels=2.0)
        return dist <= (1.0 - aa)
    feather = max(0, int(window.feather))
    if feather <= 0:
        return dist <= 1.0
    inner = max(0.0, 1.0 - feather / max(min_axis, 1e-6))
    return dist <= inner


def interior_mask_array(size: tuple[int, int], window: BlurWindow) -> np.ndarray:
    """Boolean mask for the full shape interior (normalized distance <= 1)."""
    dist, _ = _normalized_shape_dist(size, window)
    return dist <= 1.0


def combined_coverage_mask(size: tuple[int, int], covers: list[BlurWindow]) -> np.ndarray:
    width, height = size
    combined = np.zeros((height, width), dtype=np.float32)
    for cover in covers:
        mask = np.asarray(feathered_shape_mask(size, cover), dtype=np.float32) / 255.0
        combined = np.maximum(combined, mask)
    return combined


def region_coverages_rotated(
    covers: list[BlurWindow],
    unsafe_boxes: list[Box],
    image_size: tuple[int, int],
    *,
    padding: int = 4,
) -> list[tuple[Box, float]]:
    if not unsafe_boxes:
        return []
    cover_arr = combined_coverage_mask(image_size, covers)
    width, height = image_size
    rows: list[tuple[Box, float]] = []
    for box in unsafe_boxes:
        x0 = max(0, box.x - padding)
        y0 = max(0, box.y - padding)
        x1 = min(width, box.x + box.width + padding)
        y1 = min(height, box.y + box.height + padding)
        if x1 <= x0 or y1 <= y0:
            rows.append((box, 0.0))
            continue
        patch = cover_arr[y0:y1, x0:x1]
        rows.append((box, float(np.mean(patch)) if patch.size else 0.0))
    return rows


def covers_unsafe_boxes_rotated(covers: list[BlurWindow], unsafe_boxes: list[Box], image_size: tuple[int, int]) -> bool:
    if not unsafe_boxes:
        return True
    if not covers:
        return False
    for box in unsafe_boxes:
        corners = (
            (box.x + 0.5, box.y + 0.5),
            (box.x + box.width - 0.5, box.y + 0.5),
            (box.x + 0.5, box.y + box.height - 0.5),
            (box.x + box.width - 0.5, box.y + box.height - 0.5),
        )
        if not all(any(_corner_in_core(cover, corner, image_size) for cover in covers) for corner in corners):
            return False
    return True


def _corner_in_core(cover: BlurWindow, corner: tuple[float, float], image_size: tuple[int, int]) -> bool:
    width, height = image_size
    px, py = corner
    if px < 0 or py < 0 or px >= width or py >= height:
        return False
    core = core_mask_array(image_size, cover)
    ix, iy = int(px), int(py)
    return bool(core[iy, ix])


def translate_window(window: BlurWindow, dx: float, dy: float) -> BlurWindow:
    return BlurWindow(
        x=int(round(window.x + dx)),
        y=int(round(window.y + dy)),
        width=window.width,
        height=window.height,
        shape=window.shape,
        feather=window.feather,
        radius=window.radius,
        reason=window.reason,
        covers_unsafe=window.covers_unsafe,
        strength=window.strength,
        rotation_deg=window.rotation_deg,
        corner_radius=window.corner_radius,
        edge_mode=window.edge_mode,
    )


def window_for_crop(window: BlurWindow, crop_x: int, crop_y: int) -> BlurWindow:
    return translate_window(window, -crop_x, -crop_y)
