"""Golden renders: pre-#27 ellipse masks vs fixed flexible-shape compositing."""

from __future__ import annotations

import math

import cv2
import numpy as np
from instamagic.services.blurs.generator import (
    BlurWindow,
    apply_blur_covers,
    feathered_ellipse_mask,
)
from instamagic.services.blurs.masks import (
    core_mask_array,
    feathered_shape_mask,
    rasterize_polygon,
    shape_polygon,
)
from instamagic.services.instagram.publish_derivative import assert_publish_blur_integrity
from PIL import Image, ImageFilter


def _horizontal_stripes(width: int = 400, height: int = 600) -> Image.Image:
    arr = np.zeros((height, width, 3), dtype=np.uint8)
    for y in range(height):
        if (y // 8) % 2 == 0:
            arr[y, :] = 240
    return Image.fromarray(arr, mode="RGB")


def _legacy_apply_rot0_ellipse(image: Image.Image, cover: BlurWindow) -> Image.Image:
    """Pre-#27 publish path: axis-aligned ellipse mask + padded Gaussian composite."""
    rgb = image if image.mode == "RGB" else image.convert("RGB")
    pad = int(cover.radius * 4) + cover.feather + 8
    padded = Image.new("RGB", (rgb.width + 2 * pad, rgb.height + 2 * pad))
    padded.paste(rgb, (pad, pad))
    blurred = padded.filter(ImageFilter.GaussianBlur(radius=cover.radius))
    mask = feathered_ellipse_mask(
        padded.size,
        x=cover.x + pad,
        y=cover.y + pad,
        width=cover.width,
        height=cover.height,
        feather=cover.feather,
    )
    result = Image.composite(blurred, padded, mask)
    return result.crop((pad, pad, pad + rgb.width, pad + rgb.height))


def test_soft_rotation_zero_ellipse_golden_jpeg_md5() -> None:
    """Soft rotation-0 ellipse JPEG bytes stay stable (see test_blur_edge_mode golden)."""
    import hashlib
    import io

    image = _horizontal_stripes()
    cover = BlurWindow(
        x=80,
        y=200,
        width=240,
        height=180,
        shape="ellipse",
        feather=24,
        radius=28.0,
        reason="golden",
        covers_unsafe=True,
        rotation_deg=0.0,
        edge_mode="soft",
    )
    rendered = apply_blur_covers(image, [cover])
    buf = io.BytesIO()
    rendered.save(buf, format="JPEG", quality=84)
    assert hashlib.md5(buf.getvalue()).hexdigest() == "5009607f90156c49afede52eaff28537"


def test_rotation_zero_ellipse_matches_pre_27_renderer() -> None:
    image = _horizontal_stripes()
    cover = BlurWindow(
        x=80,
        y=200,
        width=240,
        height=180,
        shape="ellipse",
        feather=24,
        radius=28.0,
        reason="golden",
        covers_unsafe=True,
        rotation_deg=0.0,
        edge_mode="soft",
    )
    legacy = _legacy_apply_rot0_ellipse(image, cover)
    fixed = apply_blur_covers(image, [cover])
    diff = np.abs(np.asarray(fixed, dtype=np.float32) - np.asarray(legacy, dtype=np.float32))
    mean_abs_diff = float(diff.mean())
    assert mean_abs_diff < 0.75, mean_abs_diff


def test_rotated_ellipse_interior_is_strongly_blurred() -> None:
    image = _horizontal_stripes(320, 320)
    cover = BlurWindow(
        x=60,
        y=100,
        width=200,
        height=80,
        shape="ellipse",
        feather=14,
        radius=22.0,
        reason="rotated",
        covers_unsafe=True,
        rotation_deg=35.0,
    )
    result = apply_blur_covers(image, [cover])
    core = core_mask_array(image.size, cover)
    orig = np.asarray(image, dtype=np.float32)
    out = np.asarray(result, dtype=np.float32)
    assert np.any(core)
    mean_diff = float(np.mean(np.abs(out[core] - orig[core])))
    assert mean_diff > 12.0


def test_rotated_rounded_rect_interior_is_strongly_blurred() -> None:
    image = _horizontal_stripes(300, 300)
    cover = BlurWindow(
        x=40,
        y=90,
        width=180,
        height=70,
        shape="rectangle",
        feather=12,
        radius=20.0,
        reason="rect",
        covers_unsafe=True,
        rotation_deg=40.0,
        corner_radius=10.0,
    )
    result = apply_blur_covers(image, [cover])
    core = core_mask_array(image.size, cover)
    orig = np.asarray(image, dtype=np.float32)
    out = np.asarray(result, dtype=np.float32)
    assert np.any(core)
    mean_diff = float(np.mean(np.abs(out[core] - orig[core])))
    assert mean_diff > 10.0


def test_out_of_bounds_vertices_still_blur_visible_region() -> None:
    image = _horizontal_stripes(240, 240)
    cover = BlurWindow(
        x=-70,
        y=-70,
        width=150,
        height=150,
        shape="ellipse",
        feather=10,
        radius=18.0,
        reason="oob",
        covers_unsafe=False,
        rotation_deg=15.0,
    )
    blurred = apply_blur_covers(image, [cover])
    core = core_mask_array(image.size, cover)
    assert np.any(core)
    ys, xs = np.where(core)
    py, px = int(ys[len(ys) // 2]), int(xs[len(xs) // 2])
    assert not np.array_equal(np.asarray(blurred)[py, px], np.asarray(image)[py, px])


def _apply_with_distance_transform_mask(image: Image.Image, cover: BlurWindow) -> Image.Image:
    """Reproduce post-#27 polygon distance-transform compositing (regressed)."""
    rgb = image if image.mode == "RGB" else image.convert("RGB")
    pad = int(cover.radius * 4) + cover.feather + 8
    padded = Image.new("RGB", (rgb.width + 2 * pad, rgb.height + 2 * pad))
    padded.paste(rgb, (pad, pad))
    blurred = padded.filter(ImageFilter.GaussianBlur(radius=cover.radius))
    shifted = BlurWindow(
        x=cover.x + pad,
        y=cover.y + pad,
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
    )
    outer = rasterize_polygon(padded.size, shape_polygon(shifted))
    feather = max(0, int(cover.feather))
    dist = cv2.distanceTransform(outer, cv2.DIST_L2, 5)
    alpha = np.zeros(outer.shape, dtype=np.float32)
    solid = dist >= feather
    ring = (dist > 0) & (dist < feather)
    alpha[solid] = 1.0
    if np.any(ring):
        alpha[ring] = 0.5 * (1.0 + np.cos(math.pi * dist[ring] / feather))
    mask = Image.fromarray((alpha * 255).astype(np.uint8), mode="L")
    result = Image.composite(blurred, padded, mask)
    return result.crop((pad, pad, pad + rgb.width, pad + rgb.height))


def test_broken_pixel_eroded_mask_would_fail_integrity() -> None:
    """Regression guard: #27 distance-transform masks must not pass publish checks."""
    image = _horizontal_stripes()
    cover = BlurWindow(
        x=80,
        y=200,
        width=240,
        height=180,
        shape="ellipse",
        feather=24,
        radius=28.0,
        reason="integrity",
        covers_unsafe=True,
    )
    good = apply_blur_covers(image, [cover])
    assert_publish_blur_integrity(
        original_rgb=image,
        publish_rgb=good,
        windows=[cover],
        crop_box=None,
    )

    regressed = _apply_with_distance_transform_mask(image, cover)
    try:
        assert_publish_blur_integrity(
            original_rgb=image,
            publish_rgb=regressed,
            windows=[cover],
            crop_box=None,
        )
        raised = False
    except Exception:
        raised = True
    assert raised


def test_rot0_mask_matches_legacy_ellipse_mask() -> None:
    size = (400, 600)
    cover = BlurWindow(
        x=80,
        y=200,
        width=240,
        height=180,
        shape="ellipse",
        feather=24,
        radius=28.0,
        reason="mask",
        covers_unsafe=True,
    )
    legacy = np.asarray(
        feathered_ellipse_mask(size, x=cover.x, y=cover.y, width=cover.width, height=cover.height, feather=cover.feather)
    )
    modern = np.asarray(feathered_shape_mask(size, cover))
    assert np.max(np.abs(legacy.astype(np.int16) - modern.astype(np.int16))) == 0
