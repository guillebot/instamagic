from __future__ import annotations

import numpy as np
from instamagic.services.geometry import Box
from instamagic.services.safety.signals import Region, SafetySignals


class FakeSafetyClassifier:
    """Development stand-in. Refused when ENVIRONMENT=production."""

    model_name = "fake"

    def available(self) -> bool:
        return True

    def classify(self, image_bgr: np.ndarray, hint: str = "") -> SafetySignals:
        height, width = image_bgr.shape[:2]
        name = hint.lower()
        if "blocked" in name:
            box = Box(int(width * 0.35), int(height * 0.12), int(width * 0.3), int(height * 0.28))
            regions = [Region("FEMALE_BREAST_EXPOSED", 0.95, box)]
        elif "unsafe" in name:
            box = Box(int(width * 0.15), int(height * 0.72), int(width * 0.6), int(height * 0.22))
            regions = [Region("FEMALE_BREAST_EXPOSED", 0.93, box)]
        else:
            regions = []
        return SafetySignals(regions=regions, detector_available=True, uncertain=False, model_name=self.model_name)


class FakeFaceDetector:
    def detect(self, image_bgr: np.ndarray) -> list[Box]:
        height, width = image_bgr.shape[:2]
        return [Box(int(width * 0.4), int(height * 0.14), int(width * 0.2), int(height * 0.22))]
