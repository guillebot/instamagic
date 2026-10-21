from __future__ import annotations

from instamagic.config import Settings
from instamagic.services.gemini.captions import FakeGeminiProvider, GeminiProvider
from instamagic.services.gemini.google import GoogleGeminiProvider
from instamagic.services.safety.classifier import OnnxYoloSafetyClassifier
from instamagic.services.safety.faces import YuNetFaceDetector
from instamagic.services.safety.fake import FakeFaceDetector, FakeSafetyClassifier


def build_classifier(settings: Settings) -> FakeSafetyClassifier | OnnxYoloSafetyClassifier:
    if settings.safety_classifier == "fake":
        return FakeSafetyClassifier()
    return OnnxYoloSafetyClassifier(settings.safety_model_path, settings.safety_model_sha256)


def build_face_detector(settings: Settings) -> FakeFaceDetector | YuNetFaceDetector:
    if settings.safety_classifier == "fake":
        return FakeFaceDetector()
    return YuNetFaceDetector()


def build_gemini(settings: Settings) -> GeminiProvider:
    if settings.gemini_provider == "fake":
        return FakeGeminiProvider()
    return GoogleGeminiProvider(settings.gemini_api_key, settings.gemini_model)
