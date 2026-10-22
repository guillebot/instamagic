"""Normalize derivatives for Instagram feed aspect ratio and size limits."""

from __future__ import annotations

from pathlib import Path

from instamagic.errors import NotConfigured
from instamagic.services.images.derivatives import oriented_rgb, read_image, save_derivative
from instamagic.services.instagram.graph_constants import (
    IG_FEED_JPEG_QUALITY,
    IG_FEED_LONG_EDGE,
    IG_FEED_MAX_ASPECT,
    IG_FEED_MAX_BYTES,
    IG_FEED_MIN_ASPECT,
)
from PIL import Image


def _center_crop_to_aspect(image: Image.Image, target_aspect: float) -> Image.Image:
    width, height = image.size
    current = width / height
    if abs(current - target_aspect) < 0.001:
        return image
    if current > target_aspect:
        # Too wide — crop width.
        new_width = max(1, int(round(height * target_aspect)))
        left = (width - new_width) // 2
        return image.crop((left, 0, left + new_width, height))
    # Too tall — crop height.
    new_height = max(1, int(round(width / target_aspect)))
    top = (height - new_height) // 2
    return image.crop((0, top, width, top + new_height))


def enforce_instagram_feed_geometry(image: Image.Image) -> Image.Image:
    width, height = image.size
    if width < 1 or height < 1:
        raise NotConfigured("Image has invalid dimensions for Instagram publish.")
    aspect = width / height
    if IG_FEED_MIN_ASPECT <= aspect <= IG_FEED_MAX_ASPECT:
        return image
    if aspect < IG_FEED_MIN_ASPECT:
        return _center_crop_to_aspect(image, IG_FEED_MIN_ASPECT)
    return _center_crop_to_aspect(image, IG_FEED_MAX_ASPECT)


def write_instagram_publish_jpeg(image: Image.Image, dest: Path) -> None:
    """Center-crop to IG feed aspect bounds, resize, and stay under Meta's 8MB JPEG cap."""
    working = enforce_instagram_feed_geometry(image)
    quality = IG_FEED_JPEG_QUALITY
    while quality >= 60:
        save_derivative(working, dest, IG_FEED_LONG_EDGE, quality=quality, optimize=True)
        if dest.stat().st_size <= IG_FEED_MAX_BYTES:
            return
        quality -= 5
    raise NotConfigured(
        "Publish image is too large for Instagram after compression (max 8MB). "
        "Use a smaller source or approve a tighter crop."
    )


def prepare_publish_file_from_path(source: Path, dest: Path) -> None:
    rgb = oriented_rgb(read_image(source))
    write_instagram_publish_jpeg(rgb, dest)
