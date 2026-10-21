from __future__ import annotations

import hashlib
from pathlib import Path

import cv2
import numpy as np
from instamagic.services.geometry import Box
from instamagic.services.safety.labels import MODEL_LABELS
from instamagic.services.safety.signals import Region, SafetySignals


class OnnxYoloSafetyClassifier:
    def __init__(self, model_path: str, expected_sha256: str = "") -> None:
        self.model_name = "onnx-yolo"
        self._session = None
        self._input_name = ""
        self._input_size = 320
        path = Path(model_path) if model_path else None
        if path is None or not path.is_file():
            return
        if expected_sha256 and _sha256(path) != expected_sha256.lower():
            return
        try:
            import onnxruntime as ort
        except ImportError:
            return
        self._session = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
        model_input = self._session.get_inputs()[0]
        self._input_name = model_input.name
        shape = model_input.shape
        if len(shape) == 4 and isinstance(shape[2], int):
            self._input_size = int(shape[2])

    def available(self) -> bool:
        return self._session is not None

    def classify(self, image_bgr: np.ndarray, hint: str = "") -> SafetySignals:
        del hint
        if self._session is None:
            return SafetySignals(detector_available=False, model_name="missing")
        blob, scale, pad_x, pad_y = _letterbox(image_bgr, self._input_size)
        chw = blob.astype(np.float32).transpose(2, 0, 1)[None] / 255.0
        outputs = self._session.run(None, {self._input_name: chw})
        regions = _decode(outputs[0], scale, pad_x, pad_y, image_bgr.shape[1], image_bgr.shape[0])
        if regions is None:
            return SafetySignals(detector_available=False, uncertain=True, model_name="shape_mismatch")
        return SafetySignals(regions=regions, detector_available=True, model_name=self.model_name)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _letterbox(image: np.ndarray, size: int) -> tuple[np.ndarray, float, int, int]:
    height, width = image.shape[:2]
    scale = min(size / height, size / width)
    resized_w = max(1, int(round(width * scale)))
    resized_h = max(1, int(round(height * scale)))
    resized = cv2.resize(image, (resized_w, resized_h))
    canvas = np.full((size, size, 3), 114, dtype=np.uint8)
    pad_x = (size - resized_w) // 2
    pad_y = (size - resized_h) // 2
    canvas[pad_y : pad_y + resized_h, pad_x : pad_x + resized_w] = resized
    return canvas, scale, pad_x, pad_y


def _decode(
    output: np.ndarray,
    scale: float,
    pad_x: int,
    pad_y: int,
    image_width: int,
    image_height: int,
) -> list[Region] | None:
    data = np.squeeze(output)
    if data.ndim != 2:
        return None
    if data.shape[0] == 4 + len(MODEL_LABELS):
        data = data.T
    if data.shape[1] != 4 + len(MODEL_LABELS):
        return None
    boxes: list[list[float]] = []
    scores: list[float] = []
    class_ids: list[int] = []
    for row in data:
        class_scores = row[4:]
        class_id = int(np.argmax(class_scores))
        score = float(class_scores[class_id])
        if score < 0.25:
            continue
        cx, cy, width, height = (float(value) for value in row[:4])
        x = (cx - width / 2 - pad_x) / scale
        y = (cy - height / 2 - pad_y) / scale
        width = width / scale
        height = height / scale
        x = min(max(0.0, x), float(image_width - 1))
        y = min(max(0.0, y), float(image_height - 1))
        width = min(width, image_width - x)
        height = min(height, image_height - y)
        if width < 2 or height < 2:
            continue
        boxes.append([x, y, width, height])
        scores.append(score)
        class_ids.append(class_id)
    if not boxes:
        return []
    indices = cv2.dnn.NMSBoxes(boxes, scores, 0.25, 0.45)
    if len(indices) == 0:
        return []
    regions: list[Region] = []
    for index in np.array(indices).reshape(-1):
        x, y, width, height = boxes[int(index)]
        regions.append(
            Region(
                label=MODEL_LABELS[class_ids[int(index)]],
                score=scores[int(index)],
                box=Box(int(x), int(y), int(width), int(height)),
            )
        )
    return regions
