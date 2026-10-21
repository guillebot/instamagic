from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
from instamagic.services.geometry import Box


class YuNetFaceDetector:
    def __init__(self) -> None:
        self.path = _yunet_path()

    def detect(self, image_bgr: np.ndarray) -> list[Box]:
        if self.path is None:
            return []
        height, width = image_bgr.shape[:2]
        detector = cv2.FaceDetectorYN.create(str(self.path), "", (width, height), 0.6, 0.3, 5000)
        detector.setInputSize((width, height))
        _, faces = detector.detect(image_bgr)
        if faces is None:
            return []
        boxes: list[Box] = []
        for face in faces:
            x, y, box_w, box_h = (int(value) for value in face[:4])
            boxes.append(Box(max(0, x), max(0, y), max(1, box_w), max(1, box_h)))
        return boxes


def _yunet_path() -> Path | None:
    candidate = Path(cv2.__file__).resolve().parent / "data" / "face_detection_yunet_2023mar.onnx"
    if candidate.is_file():
        return candidate
    return None
