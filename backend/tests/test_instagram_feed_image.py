"""Instagram feed JPEG normalization (aspect ratio + size)."""

from __future__ import annotations

from pathlib import Path

from instamagic.services.instagram.feed_image import (
    enforce_instagram_feed_geometry,
    write_instagram_publish_jpeg,
)
from instamagic.services.instagram.graph_constants import (
    IG_FEED_MAX_ASPECT,
    IG_FEED_MAX_BYTES,
    IG_FEED_MIN_ASPECT,
)
from PIL import Image


def test_enforce_feed_geometry_center_crops_too_wide() -> None:
    wide = Image.new("RGB", (2000, 500), (10, 10, 10))
    out = enforce_instagram_feed_geometry(wide)
    ratio = out.size[0] / out.size[1]
    assert IG_FEED_MIN_ASPECT <= ratio <= IG_FEED_MAX_ASPECT


def test_write_instagram_publish_jpeg_under_size_limit(tmp_path: Path) -> None:
    tall = Image.new("RGB", (800, 2000), (20, 20, 20))
    dest = tmp_path / "feed.jpg"
    write_instagram_publish_jpeg(tall, dest)
    assert dest.is_file()
    assert dest.stat().st_size <= IG_FEED_MAX_BYTES
