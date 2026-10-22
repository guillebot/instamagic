import pytest
from instamagic.services.crops.generator import (
    generate_crops,
    validate_crop_window,
    viable_safety_crop,
    window_excludes_unsafe,
)
from instamagic.services.geometry import Box


def test_crop_excludes_unsafe_region_and_keeps_face() -> None:
    face = Box(400, 180, 200, 200)
    unsafe = Box(80, 760, 500, 200)
    crops = generate_crops(1000, 1000, [face], [unsafe])
    safety = [crop for crop in crops if crop.aspect_ratio != "original" and crop.contains_face and crop.excludes_unsafe]
    assert safety
    assert viable_safety_crop(crops)
    for crop in safety:
        window = crop.as_box()
        assert window.contains(face)
        assert not window.intersects(unsafe.expanded(8))


def test_crop_is_impossible_when_unsafe_covers_the_face() -> None:
    face = Box(400, 180, 200, 200)
    unsafe = Box(380, 160, 240, 240)
    crops = generate_crops(1000, 1000, [face], [unsafe])
    assert not viable_safety_crop(crops)


def test_validate_crop_window_keeps_aspect_and_bounds() -> None:
    window = validate_crop_window(
        image_width=1000,
        image_height=1000,
        x=10,
        y=20,
        width=400,
        height=500,
        aspect_ratio="4:5",
        reference_width=400,
        reference_height=500,
    )
    assert (window.x, window.y, window.width, window.height) == (10, 20, 400, 500)
    with pytest.raises(ValueError, match="inside"):
        validate_crop_window(
            image_width=1000,
            image_height=1000,
            x=900,
            y=0,
            width=400,
            height=500,
            aspect_ratio="4:5",
            reference_width=400,
            reference_height=500,
        )
    with pytest.raises(ValueError, match="aspect"):
        validate_crop_window(
            image_width=1000,
            image_height=1000,
            x=0,
            y=0,
            width=500,
            height=500,
            aspect_ratio="4:5",
            reference_width=400,
            reference_height=500,
        )


def test_moved_window_excludes_unsafe() -> None:
    unsafe = [Box(0, 800, 200, 200)]
    assert window_excludes_unsafe(Box(400, 0, 400, 500), unsafe)
    assert not window_excludes_unsafe(Box(0, 700, 400, 500), unsafe)
