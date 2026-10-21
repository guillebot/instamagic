from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np
from instamagic.models.enums import SafetyState
from instamagic.services.crops.generator import CropWindow, generate_crops, viable_safety_crop
from instamagic.services.geometry import Box
from instamagic.services.safety.decision import DecisionInput, decide
from instamagic.services.safety.signals import SafetySignals, score_summary
from instamagic.services.scoring.local import is_monochrome, score_image
from PIL import Image


@dataclass
class Analysis:
    state: SafetyState
    signals: SafetySignals
    faces: list[Box]
    crops: list[CropWindow]
    scores: dict[str, float]
    summary: dict[str, float]
    monochrome: bool
    clear_exposed: bool


def analyze_image(
    image: Image.Image,
    *,
    filename: str,
    attested: bool,
    classifier: object,
    face_detector: object,
) -> Analysis:
    rgb = image if image.mode == "RGB" else image.convert("RGB")
    bgr = cv2.cvtColor(np.asarray(rgb), cv2.COLOR_RGB2BGR)
    working, scale = _downscale(bgr, 1280)
    signals = classifier.classify(working, hint=filename)  # type: ignore[attr-defined]
    if scale != 1.0:
        signals.regions = [
            type(region)(region.label, region.score, _scale_box(region.box, scale)) for region in signals.regions
        ]
    faces = [_scale_box(box, scale) for box in face_detector.detect(working)]  # type: ignore[attr-defined]
    clear = signals.clear_exposed()
    unsafe_boxes = [region.box for region in clear]
    crops = generate_crops(rgb.size[0], rgb.size[1], faces, unsafe_boxes)
    viable = viable_safety_crop(crops) if clear else False
    uncertain = signals.uncertain or (signals.detector_available and signals.has_ambiguous_exposure())
    state = decide(
        DecisionInput(
            attested=attested,
            detector_available=signals.detector_available,
            uncertain=uncertain,
            clear_exposed=bool(clear),
            viable_safety_crop=viable,
        )
    )
    primary = max(faces, key=lambda box: box.area) if faces else None
    scores = score_image(rgb, primary)
    summary = score_summary(signals, rgb.size[0] * rgb.size[1])
    return Analysis(
        state=state,
        signals=signals,
        faces=faces,
        crops=crops,
        scores=scores,
        summary=summary,
        monochrome=is_monochrome(rgb),
        clear_exposed=bool(clear),
    )


def _downscale(image: np.ndarray, max_edge: int) -> tuple[np.ndarray, float]:
    height, width = image.shape[:2]
    longest = max(height, width)
    if longest <= max_edge:
        return image, 1.0
    scale = longest / max_edge
    resized = cv2.resize(image, (int(width / scale), int(height / scale)))
    return resized, scale


def _scale_box(box: Box, scale: float) -> Box:
    return Box(
        int(round(box.x * scale)),
        int(round(box.y * scale)),
        int(round(box.width * scale)),
        int(round(box.height * scale)),
    )
