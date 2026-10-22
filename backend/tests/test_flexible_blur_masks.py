"""Rotated / out-of-bounds blur geometry."""

import numpy as np
from instamagic.services.blurs.generator import BlurWindow, apply_blur_covers, region_coverages
from instamagic.services.blurs.masks import feathered_shape_mask, window_for_crop
from instamagic.services.geometry import Box
from PIL import Image


def _checker(size: int = 200) -> Image.Image:
    base = np.zeros((size, size, 3), dtype=np.uint8)
    base[::2, ::2] = 255
    base[1::2, 1::2] = 255
    return Image.fromarray(base, mode="RGB")


def test_rotated_rectangle_mask_changes_center_pixels() -> None:
    image = _checker()
    cover = BlurWindow(
        x=60,
        y=60,
        width=120,
        height=40,
        shape="rectangle",
        feather=10,
        radius=18.0,
        reason="rotated",
        covers_unsafe=True,
        rotation_deg=45.0,
        corner_radius=6.0,
    )
    mask = np.asarray(feathered_shape_mask(image.size, cover)) > 127
    ys, xs = np.where(mask)
    assert ys.size > 0
    py, px = int(ys[len(ys) // 2]), int(xs[len(xs) // 2])
    result = apply_blur_covers(image, [cover])
    assert not np.array_equal(np.asarray(result)[py, px], np.asarray(image)[py, px])


def test_out_of_bounds_cover_still_blurs_visible_corner() -> None:
    image = _checker(240)
    # Center mostly outside top-left; only lower-right quadrant intersects image.
    cover = BlurWindow(
        x=-80,
        y=-80,
        width=160,
        height=160,
        shape="rectangle",
        feather=8,
        radius=16.0,
        reason="oob",
        covers_unsafe=False,
        rotation_deg=0.0,
        corner_radius=0.0,
    )
    target = Box(0, 0, 40, 40)
    rows = region_coverages([cover], [target], (240, 240), min_ratio=0.95, padding=0)
    assert rows[0].coverage >= 0.95
    blurred = apply_blur_covers(image, [cover])
    assert not np.array_equal(np.asarray(blurred)[20, 20], np.asarray(image)[20, 20])


def test_ellipse_rotation_changes_mask() -> None:
    size = (200, 200)
    base = BlurWindow(
        x=50,
        y=80,
        width=100,
        height=40,
        shape="ellipse",
        feather=8,
        radius=12.0,
        reason="e",
        covers_unsafe=True,
        rotation_deg=0.0,
    )
    rotated = BlurWindow(**{**base.__dict__, "rotation_deg": 60.0})
    m0 = np.asarray(feathered_shape_mask(size, base))
    m1 = np.asarray(feathered_shape_mask(size, rotated))
    assert not np.array_equal(m0, m1)


def test_crop_mapping_preserves_rotation() -> None:
    window = BlurWindow(
        x=100,
        y=120,
        width=80,
        height=60,
        shape="rectangle",
        feather=6,
        radius=10.0,
        reason="c",
        covers_unsafe=False,
        rotation_deg=33.0,
        corner_radius=4.0,
    )
    mapped = window_for_crop(window, 50, 40)
    assert mapped.x == 50
    assert mapped.y == 80
    assert mapped.rotation_deg == 33.0
