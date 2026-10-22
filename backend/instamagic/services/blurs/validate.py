"""Validation for blur cover geometry."""

from __future__ import annotations

from instamagic.services.blurs.generator import BlurWindow


def validate_blur_geometry(image_width: int, image_height: int, window: BlurWindow) -> None:
    if image_width <= 0 or image_height <= 0:
        raise ValueError("Image dimensions are unknown.")
    if window.width <= 0 or window.height <= 0:
        raise ValueError("Blur region size must be positive.")
    if window.shape not in {"ellipse", "rectangle"}:
        raise ValueError("Shape must be ellipse or rectangle.")
    if not -180.0 <= window.rotation_deg <= 180.0:
        raise ValueError("Rotation must be between -180 and 180 degrees.")
    if window.corner_radius < 0:
        raise ValueError("Corner radius cannot be negative.")
    if window.shape == "rectangle":
        max_r = min(window.width, window.height) / 2
        if window.corner_radius > max_r:
            raise ValueError("Corner radius is too large for this rectangle.")
    cx = window.x + window.width / 2
    cy = window.y + window.height / 2
    margin_w = float(image_width)
    margin_h = float(image_height)
    if not (-margin_w <= cx <= image_width + margin_w):
        raise ValueError("Blur center is too far outside the image horizontally.")
    if not (-margin_h <= cy <= image_height + margin_h):
        raise ValueError("Blur center is too far outside the image vertically.")
