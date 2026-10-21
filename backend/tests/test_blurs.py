import numpy as np
from instamagic.services.blurs.generator import (
    BlurWindow,
    apply_blur_covers,
    covers_unsafe_boxes,
    feathered_ellipse_mask,
    suggest_blurs,
)
from instamagic.services.geometry import Box
from PIL import Image


def test_feathered_composite_blurs_inside_and_spares_outside() -> None:
    # High-frequency checkerboard so blur changes are measurable.
    base = np.zeros((200, 200, 3), dtype=np.uint8)
    base[::2, ::2] = 255
    base[1::2, 1::2] = 255
    image = Image.fromarray(base, mode="RGB")
    cover = BlurWindow(
        x=50,
        y=50,
        width=100,
        height=100,
        shape="ellipse",
        feather=20,
        radius=18.0,
        reason="test",
        covers_unsafe=True,
    )
    result = apply_blur_covers(image, [cover])
    out = np.asarray(result)
    # Well inside the opaque core: pixels should differ from the sharp checkerboard.
    assert not np.array_equal(out[100, 100], base[100, 100])
    # Far outside the ellipse: unchanged.
    assert np.array_equal(out[10, 10], base[10, 10])
    assert np.array_equal(out[190, 190], base[190, 190])


def test_mask_boundary_is_feathered_not_binary() -> None:
    mask = feathered_ellipse_mask((120, 120), x=20, y=20, width=80, height=80, feather=16)
    arr = np.asarray(mask)
    assert arr[60, 60] == 255
    assert arr[5, 5] == 0
    ring = arr[(arr > 0) & (arr < 255)]
    assert ring.size > 0
    assert ring.min() < 200
    assert ring.max() > 50


def test_cover_check_passes_when_core_contains_unsafe_box() -> None:
    unsafe = Box(40, 40, 20, 20)
    covering = suggest_blurs(200, 200, [unsafe], strength="standard")
    assert covering
    assert covers_unsafe_boxes(covering, [unsafe])


def test_cover_check_fails_when_blur_misses_region() -> None:
    unsafe = Box(40, 40, 30, 30)
    miss = BlurWindow(
        x=140,
        y=140,
        width=40,
        height=40,
        shape="ellipse",
        feather=8,
        radius=20.0,
        reason="miss",
        covers_unsafe=False,
    )
    assert not covers_unsafe_boxes([miss], [unsafe])


def test_suggested_sigma_is_strong_not_beauty_soft() -> None:
    unsafe = Box(80, 80, 100, 80)
    covers = suggest_blurs(400, 400, [unsafe], strength="subtle")
    assert covers[0].radius >= 14
    strong = suggest_blurs(400, 400, [unsafe], strength="strong")
    assert strong[0].radius > covers[0].radius
