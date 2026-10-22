from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from instamagic.services.geometry import Box
from PIL import Image, ImageFilter

STRENGTHS = ("subtle", "standard", "strong")

# Sigma scales with the shorter side of the cover. Floor values stay high enough
# that detail inside the region is not recognizable (not a beauty soft-focus).
_STRENGTH_SCALE = {
    "subtle": 0.10,
    "standard": 0.15,
    "strong": 0.22,
}
_STRENGTH_FLOOR = {
    "subtle": 14.0,
    "standard": 20.0,
    "strong": 30.0,
}


@dataclass(frozen=True)
class BlurWindow:
    x: int
    y: int
    width: int
    height: int
    shape: str
    feather: int
    radius: float
    reason: str
    covers_unsafe: bool
    strength: str = "standard"

    def as_box(self) -> Box:
        return Box(self.x, self.y, self.width, self.height)

    def core_radii(self) -> tuple[float, float, float, float]:
        cx = self.x + self.width / 2
        cy = self.y + self.height / 2
        rx = max(0.0, self.width / 2 - self.feather)
        ry = max(0.0, self.height / 2 - self.feather)
        return cx, cy, rx, ry


def suggest_blurs(
    image_width: int,
    image_height: int,
    unsafe_boxes: list[Box],
    *,
    strength: str = "standard",
) -> list[BlurWindow]:
    if strength not in STRENGTHS:
        raise ValueError("Strength must be subtle, standard, or strong.")
    covers: list[BlurWindow] = []
    for box in unsafe_boxes:
        cover = _cover_for_unsafe(box, image_width, image_height, strength=strength)
        covers.append(cover)
    return covers


def manual_blur(
    image_width: int,
    image_height: int,
    box: Box,
    *,
    strength: str = "standard",
    reason: str = "Manual ellipse cover.",
) -> BlurWindow:
    if strength not in STRENGTHS:
        raise ValueError("Strength must be subtle, standard, or strong.")
    clamped = _clamp_box(box, image_width, image_height)
    if clamped.width < 2 or clamped.height < 2:
        raise ValueError("Blur region is too small.")
    feather = _feather_for(clamped.width, clamped.height)
    return BlurWindow(
        x=clamped.x,
        y=clamped.y,
        width=clamped.width,
        height=clamped.height,
        shape="ellipse",
        feather=feather,
        radius=sigma_for(clamped.width, clamped.height, strength),
        reason=reason,
        covers_unsafe=False,
        strength=strength,
    )


def sigma_for(width: int, height: int, strength: str) -> float:
    shorter = max(1, min(width, height))
    scale = _STRENGTH_SCALE[strength]
    floor = _STRENGTH_FLOOR[strength]
    return max(floor, scale * shorter)


def with_strength(window: BlurWindow, strength: str) -> BlurWindow:
    if strength not in STRENGTHS:
        raise ValueError("Strength must be subtle, standard, or strong.")
    return BlurWindow(
        x=window.x,
        y=window.y,
        width=window.width,
        height=window.height,
        shape=window.shape,
        feather=window.feather,
        radius=sigma_for(window.width, window.height, strength),
        reason=window.reason,
        covers_unsafe=window.covers_unsafe,
        strength=strength,
    )


@dataclass(frozen=True)
class RegionCoverage:
    box: Box
    coverage: float


def region_coverages(
    covers: list[BlurWindow],
    unsafe_boxes: list[Box],
    image_size: tuple[int, int],
    *,
    min_ratio: float = 0.95,
    padding: int = 4,
) -> list[RegionCoverage]:
    """Fraction of each unsafe box covered by the combined blur masks (0–1)."""
    if not unsafe_boxes:
        return []
    width, height = image_size
    combined = Image.new("L", (width, height), 0)
    for cover in covers:
        mask = feathered_ellipse_mask(
            (width, height),
            x=cover.x,
            y=cover.y,
            width=cover.width,
            height=cover.height,
            feather=cover.feather,
        )
        combined = _lighter_mask(combined, mask)
    cover_arr = np.asarray(combined, dtype=np.float32) / 255.0
    rows: list[RegionCoverage] = []
    for box in unsafe_boxes:
        x0 = max(0, box.x - padding)
        y0 = max(0, box.y - padding)
        x1 = min(width, box.x + box.width + padding)
        y1 = min(height, box.y + box.height + padding)
        if x1 <= x0 or y1 <= y0:
            rows.append(RegionCoverage(box=box, coverage=0.0))
            continue
        patch = cover_arr[y0:y1, x0:x1]
        rows.append(RegionCoverage(box=box, coverage=float(np.mean(patch)) if patch.size else 0.0))
    return rows


def covers_unsafe_boxes(covers: list[BlurWindow], unsafe_boxes: list[Box]) -> bool:
    """True when every unsafe box lies inside the opaque (feathered) core of some cover."""
    if not unsafe_boxes:
        return True
    if not covers:
        return False
    return all(any(_core_contains(cover, box) for cover in covers) for box in unsafe_boxes)


def _core_contains(cover: BlurWindow, box: Box) -> bool:
    cx, cy, rx, ry = cover.core_radii()
    if rx <= 0 or ry <= 0:
        return False
    corners = (
        (box.x, box.y),
        (box.x + box.width, box.y),
        (box.x, box.y + box.height),
        (box.x + box.width, box.y + box.height),
    )
    return all(_point_in_ellipse(px, py, cx, cy, rx, ry) for px, py in corners)


def _point_in_ellipse(px: float, py: float, cx: float, cy: float, rx: float, ry: float) -> bool:
    return ((px - cx) / rx) ** 2 + ((py - cy) / ry) ** 2 <= 1.0 + 1e-9


def feathered_ellipse_mask(
    size: tuple[int, int],
    *,
    x: int,
    y: int,
    width: int,
    height: int,
    feather: int,
) -> Image.Image:
    """L mask: 255 in the opaque core, cosine falloff across the feather, 0 outside."""
    image_w, image_h = size
    yy, xx = np.mgrid[0:image_h, 0:image_w]
    cx = x + width / 2
    cy = y + height / 2
    rx = max(width / 2, 1e-6)
    ry = max(height / 2, 1e-6)
    # Normalized distance from center: 0 at center, 1 on the ellipse boundary.
    dist = np.sqrt(((xx - cx) / rx) ** 2 + ((yy - cy) / ry) ** 2)
    inner = max(0.0, 1.0 - feather / max(min(rx, ry), 1e-6))
    mask = np.zeros((image_h, image_w), dtype=np.float32)
    solid = dist <= inner
    ring = (dist > inner) & (dist <= 1.0)
    mask[solid] = 1.0
    if np.any(ring) and inner < 1.0:
        t = (dist[ring] - inner) / (1.0 - inner)
        # Smooth cosine falloff: 1 at inner edge, 0 at outer edge.
        mask[ring] = 0.5 * (1.0 + np.cos(math.pi * t))
    return Image.fromarray((mask * 255.0).astype(np.uint8), mode="L")


def apply_blur_covers(image: Image.Image, covers: list[BlurWindow]) -> Image.Image:
    """Composite Gaussian-blurred RGB only where feathered ellipse masks are opaque."""
    rgb = image if image.mode == "RGB" else image.convert("RGB")
    if not covers:
        return rgb.copy()
    result = rgb.copy()
    # Group by radius so we blur once per sigma.
    by_radius: dict[float, list[BlurWindow]] = {}
    for cover in covers:
        by_radius.setdefault(round(cover.radius, 3), []).append(cover)
    for radius, group in by_radius.items():
        blurred = rgb.filter(ImageFilter.GaussianBlur(radius=radius))
        combined = Image.new("L", rgb.size, 0)
        for cover in group:
            mask = feathered_ellipse_mask(
                rgb.size,
                x=cover.x,
                y=cover.y,
                width=cover.width,
                height=cover.height,
                feather=cover.feather,
            )
            combined = _lighter_mask(combined, mask)
        result = Image.composite(blurred, result, combined)
    return result


def _lighter_mask(a: Image.Image, b: Image.Image) -> Image.Image:
    return Image.fromarray(np.maximum(np.asarray(a), np.asarray(b)), mode="L")


def _feather_for(width: int, height: int) -> int:
    shorter = max(1, min(width, height))
    # Soft edge: about 15–25% of the shorter side, clamped.
    raw = int(round(0.20 * shorter))
    return max(8, min(raw, max(8, shorter // 2)))


def _cover_for_unsafe(
    box: Box,
    image_width: int,
    image_height: int,
    *,
    strength: str,
) -> BlurWindow:
    """Build an ellipse whose opaque core contains ``box`` (AABB corners).

    Same half-axes as the rectangle are not enough ((a/a)^2+(b/b)^2=2).
    Keep the ellipse centered on the unsafe box — do not clamp the bounding
    box to the image, or edge regions shift the center and fail containment.
    The mask renderer already clips to the bitmap.
    """
    del image_width, image_height  # geometry is box-centered; mask clips at draw time
    reason = (
        "Suggested ellipse over a detected unsafe region. "
        "The feathered core must fully cover that region before approval."
    )
    cx = box.x + box.width / 2
    cy = box.y + box.height / 2
    # √2 core + slack for integer pixel snapping of the bounding box.
    slack = 3.0
    core_rx = (box.width / 2) * math.sqrt(2) + slack
    core_ry = (box.height / 2) * math.sqrt(2) + slack
    candidate = None
    for _ in range(8):
        feather = _feather_for(int(math.ceil(2 * core_rx)), int(math.ceil(2 * core_ry)))
        # Grow outer so core radii after feather stay ≥ target.
        outer_w = max(2, int(math.ceil(2 * (core_rx + feather))))
        outer_h = max(2, int(math.ceil(2 * (core_ry + feather))))
        feather = _feather_for(outer_w, outer_h)
        outer_w = max(2, int(math.ceil(2 * (core_rx + feather))))
        outer_h = max(2, int(math.ceil(2 * (core_ry + feather))))
        x = int(round(cx - outer_w / 2))
        y = int(round(cy - outer_h / 2))
        candidate = BlurWindow(
            x=x,
            y=y,
            width=outer_w,
            height=outer_h,
            shape="ellipse",
            feather=feather,
            radius=sigma_for(outer_w, outer_h, strength),
            reason=reason,
            covers_unsafe=True,
            strength=strength,
        )
        if _core_contains(candidate, box):
            return candidate
        core_rx += 4.0
        core_ry += 4.0
    assert candidate is not None
    return candidate


def _clamp_box(box: Box, image_width: int, image_height: int) -> Box:
    x1 = max(0, box.x)
    y1 = max(0, box.y)
    x2 = min(image_width, box.x + box.width)
    y2 = min(image_height, box.y + box.height)
    return Box(x1, y1, max(0, x2 - x1), max(0, y2 - y1))
