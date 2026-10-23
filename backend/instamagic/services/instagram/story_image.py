"""Render 9:16 Instagram Story JPEGs from the exact feed publish derivative only."""

from __future__ import annotations

from pathlib import Path

import numpy as np
from instamagic.errors import NotAllowed, NotConfigured
from instamagic.models.enums import StoryRenderMode
from instamagic.services.images.derivatives import oriented_rgb, read_image, save_derivative
from instamagic.services.instagram.graph_constants import IG_FEED_JPEG_QUALITY, IG_FEED_MAX_BYTES
from PIL import Image, ImageEnhance, ImageFilter

STORY_WIDTH = 1080
STORY_HEIGHT = 1920
STORY_ASPECT = STORY_WIDTH / STORY_HEIGHT


def _center_crop_to_aspect(image: Image.Image, target_aspect: float) -> Image.Image:
    width, height = image.size
    current = width / height
    if abs(current - target_aspect) < 0.001:
        return image
    if current > target_aspect:
        new_width = max(1, int(round(height * target_aspect)))
        left = (width - new_width) // 2
        return image.crop((left, 0, left + new_width, height))
    new_height = max(1, int(round(width / target_aspect)))
    top = (height - new_height) // 2
    return image.crop((0, top, width, top + new_height))


def _smart_crop_box(image: Image.Image) -> tuple[int, int, int, int]:
    """Center crop to 9:16, biased toward the largest detected face when present."""
    width, height = image.size
    target_aspect = STORY_ASPECT
    if width / height > target_aspect:
        crop_h = height
        crop_w = max(1, int(round(height * target_aspect)))
    else:
        crop_w = width
        crop_h = max(1, int(round(width / target_aspect)))
    left = (width - crop_w) // 2
    top = (height - crop_h) // 2
    import cv2
    import numpy as np
    from instamagic.services.safety.faces import YuNetFaceDetector

    bgr = cv2.cvtColor(np.asarray(image.convert("RGB")), cv2.COLOR_RGB2BGR)
    faces = YuNetFaceDetector().detect(bgr)
    if faces:
        primary = max(faces, key=lambda box: box.area)
        face_cx = primary.cx
        face_cy = primary.cy
        left = int(round(face_cx - crop_w / 2))
        top = int(round(face_cy - crop_h / 2))
        left = max(0, min(left, width - crop_w))
        top = max(0, min(top, height - crop_h))
    return left, top, crop_w, crop_h


def _fit_contain(image: Image.Image, box: tuple[int, int]) -> Image.Image:
    max_w, max_h = box
    copy = image.copy()
    copy.thumbnail((max_w, max_h), Image.Resampling.LANCZOS)
    return copy


def render_story_from_feed_file(
    feed_path: Path,
    *,
    mode: str,
    border_color: str = "black",
) -> Image.Image:
    if not feed_path.is_file():
        raise NotConfigured("Feed publish file was not found for story render.")
    source = oriented_rgb(read_image(feed_path))
    render_mode = StoryRenderMode(mode) if mode in StoryRenderMode._value2member_map_ else StoryRenderMode.BLUR_BACKDROP

    if render_mode == StoryRenderMode.CROP_FILL:
        box = _smart_crop_box(source)
        cropped = source.crop(box)
        return cropped.resize((STORY_WIDTH, STORY_HEIGHT), Image.Resampling.LANCZOS)

    if render_mode == StoryRenderMode.FIT_BORDER:
        fill = (0, 0, 0) if border_color.strip().lower() != "white" else (255, 255, 255)
        canvas = Image.new("RGB", (STORY_WIDTH, STORY_HEIGHT), fill)
        inner = _fit_contain(source, (STORY_WIDTH, STORY_HEIGHT))
        x = (STORY_WIDTH - inner.width) // 2
        y = (STORY_HEIGHT - inner.height) // 2
        canvas.paste(inner, (x, y))
        return canvas

    # blur_backdrop (default)
    backdrop = source.copy()
    backdrop = backdrop.resize((STORY_WIDTH, STORY_HEIGHT), Image.Resampling.LANCZOS)
    backdrop = backdrop.filter(ImageFilter.GaussianBlur(radius=42))
    backdrop = ImageEnhance.Brightness(backdrop).enhance(0.72)
    foreground = _fit_contain(source, (STORY_WIDTH, STORY_HEIGHT))
    x = (STORY_WIDTH - foreground.width) // 2
    y = (STORY_HEIGHT - foreground.height) // 2
    canvas = backdrop.copy()
    canvas.paste(foreground, (x, y))
    return canvas


def write_story_jpeg(image: Image.Image, dest: Path) -> None:
    quality = IG_FEED_JPEG_QUALITY
    while quality >= 60:
        save_derivative(image, dest, STORY_HEIGHT, quality=quality, optimize=True)
        if dest.stat().st_size <= IG_FEED_MAX_BYTES:
            return
        quality -= 5
    raise NotConfigured("Story image is too large for Instagram after compression (max 8MB).")


def assert_story_uses_feed_source(feed_path: Path, story_image: Image.Image, mode: str) -> None:
    """Ensure story pixels are derived from the feed publish file (never a sharper original)."""
    feed = oriented_rgb(read_image(feed_path))
    render_mode = StoryRenderMode(mode) if mode in StoryRenderMode._value2member_map_ else StoryRenderMode.BLUR_BACKDROP
    if render_mode == StoryRenderMode.CROP_FILL:
        box = _smart_crop_box(feed)
        expected = feed.crop(box).resize((STORY_WIDTH, STORY_HEIGHT), Image.Resampling.LANCZOS)
        if list(story_image.convert("RGB").getdata()) != list(expected.getdata()):
            raise NotAllowed("Story render does not match the feed publish derivative.")
        return
    if render_mode == StoryRenderMode.FIT_BORDER:
        inner = _fit_contain(feed, (STORY_WIDTH, STORY_HEIGHT))
        # Foreground region must match feed fit-contain pixels.
        x = (STORY_WIDTH - inner.width) // 2
        y = (STORY_HEIGHT - inner.height) // 2
        region = story_image.crop((x, y, x + inner.width, y + inner.height))
        if list(region.getdata()) != list(inner.getdata()):
            raise NotAllowed("Story render does not match the feed publish derivative.")
        return
    inner = _fit_contain(feed, (STORY_WIDTH, STORY_HEIGHT))
    x = (STORY_WIDTH - inner.width) // 2
    y = (STORY_HEIGHT - inner.height) // 2
    region = story_image.crop((x, y, x + inner.width, y + inner.height))
    if list(region.getdata()) != list(inner.getdata()):
        raise NotAllowed("Story foreground does not match the feed publish derivative.")


# Final-file check. Encoding noise is high-frequency and JPEG removes detail rather than adding it,
# so compare (a) low-passed content per tile and (b) per-tile sharpness, which must never exceed
# the verified render's. A sharper / unblurred source inside a blurred cover fails (b); a
# different picture fails (a).
_STORY_DOWNSCALE = 4
_STORY_TILE = 8  # in downscaled pixels (32 px at full size)
_STORY_MAX_TILE_MEAN_ABS_DIFF = 10.0
_STORY_MAX_GLOBAL_MEAN_ABS_DIFF = 6.0
_STORY_SHARPNESS_RATIO = 1.25
_STORY_SHARPNESS_MARGIN = 3.0


def _gray(image: Image.Image) -> np.ndarray:
    return np.asarray(image.convert("L"), dtype=np.float32)


def _tile_mean(values: np.ndarray, tile: int) -> np.ndarray:
    rows = values.shape[0] // tile
    cols = values.shape[1] // tile
    return values[: rows * tile, : cols * tile].reshape(rows, tile, cols, tile).mean(axis=(1, 3))


def _laplacian_abs(gray: np.ndarray) -> np.ndarray:
    lap = np.zeros_like(gray)
    lap[1:-1, 1:-1] = np.abs(
        4 * gray[1:-1, 1:-1] - gray[:-2, 1:-1] - gray[2:, 1:-1] - gray[1:-1, :-2] - gray[1:-1, 2:]
    )
    return lap


def assert_story_file_matches_feed(
    story_path: Path,
    feed_path: Path,
    *,
    mode: str,
    border_color: str = "black",
) -> None:
    """Verify the FINAL encoded story JPEG is the render of the verified feed publish file.

    The feed publish file was blur-integrity-verified against the original; this proves the
    uploaded story pixels (foreground and backdrop) come from it and contain nothing sharper.
    """
    expected = render_story_from_feed_file(feed_path, mode=mode, border_color=border_color).convert("RGB")
    story = oriented_rgb(read_image(story_path)).convert("RGB")
    if story.size != expected.size:
        raise NotAllowed("Story file size does not match the expected 9:16 render.")
    small = (expected.width // _STORY_DOWNSCALE, expected.height // _STORY_DOWNSCALE)
    got_low = np.asarray(story.resize(small, Image.Resampling.BOX), dtype=np.float32)
    want_low = np.asarray(expected.resize(small, Image.Resampling.BOX), dtype=np.float32)
    diff = np.abs(got_low - want_low).mean(axis=2)
    if float(diff.mean()) > _STORY_MAX_GLOBAL_MEAN_ABS_DIFF:
        raise NotAllowed("Story file does not match the verified feed publish derivative.")
    if float(_tile_mean(diff, _STORY_TILE).max()) > _STORY_MAX_TILE_MEAN_ABS_DIFF:
        raise NotAllowed("Story file does not match the verified feed publish derivative in some areas.")
    full_tile = _STORY_TILE * _STORY_DOWNSCALE
    got_sharp = _tile_mean(_laplacian_abs(_gray(story)), full_tile)
    want_sharp = _tile_mean(_laplacian_abs(_gray(expected)), full_tile)
    if bool((got_sharp > want_sharp * _STORY_SHARPNESS_RATIO + _STORY_SHARPNESS_MARGIN).any()):
        raise NotAllowed("Story file is sharper than the verified feed derivative; refusing to upload.")
