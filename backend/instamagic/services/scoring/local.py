from __future__ import annotations

import cv2
import numpy as np
from instamagic.services.geometry import Box
from PIL import Image


def score_image(image: Image.Image, face: Box | None) -> dict[str, float]:
    sample = image.copy()
    sample.thumbnail((256, 256))
    array = np.asarray(sample.convert("RGB"))
    gray = cv2.cvtColor(array, cv2.COLOR_RGB2GRAY)
    variance = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    sharpness = 1.0 - float(np.exp(-variance / 400.0))
    clipped = float(np.mean((gray <= 5) | (gray >= 250)))
    exposure = 1.0 - min(1.0, clipped * 4)
    technical = 0.7 * sharpness + 0.3 * exposure
    composition = _composition(sample.size, face, image.size)
    face_quality = 0.0 if face is None else min(1.0, face.area / max(1, image.size[0] * image.size[1]) * 12)
    if face is not None:
        face_quality = max(face_quality, 0.55 if sharpness > 0.4 else 0.35)
    aesthetic = 0.5 * technical + 0.3 * composition + 0.2 * face_quality
    return {
        "sharpness": round(sharpness, 4),
        "composition": round(composition, 4),
        "face_quality": round(face_quality, 4),
        "technical_quality": round(technical, 4),
        "aesthetic_score": round(aesthetic, 4),
    }


def is_monochrome(image: Image.Image) -> bool:
    sample = image.copy()
    sample.thumbnail((64, 64))
    hsv = cv2.cvtColor(np.asarray(sample.convert("RGB")), cv2.COLOR_RGB2HSV)
    return float(np.mean(hsv[:, :, 1])) < 18


def _composition(sample_size: tuple[int, int], face: Box | None, full_size: tuple[int, int]) -> float:
    if face is None or full_size[0] == 0 or full_size[1] == 0:
        return 0.45
    scale_x = sample_size[0] / full_size[0]
    scale_y = sample_size[1] / full_size[1]
    fx = (face.cx * scale_x) / sample_size[0]
    fy = (face.cy * scale_y) / sample_size[1]
    vertical = 1.0 - min(1.0, abs(fy - 0.33) * 2)
    horizontal = 1.0 - min(1.0, min(abs(fx - 0.33), abs(fx - 0.66)) * 2)
    return max(0.0, min(1.0, 0.6 * vertical + 0.4 * horizontal))
