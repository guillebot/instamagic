from __future__ import annotations

from instamagic.config import Settings
from instamagic.services.gemini.captions import FakeGeminiProvider, GeminiProvider
from instamagic.services.gemini.google import GoogleGeminiProvider
from instamagic.services.gemini.growth import FakeGrowthDraftProvider, GrowthDraftProvider
from instamagic.services.instagram.client import InstagramClient
from instamagic.services.instagram.store import build_instagram_client
from instamagic.services.safety.classifier import OnnxYoloSafetyClassifier
from instamagic.services.safety.faces import YuNetFaceDetector
from instamagic.services.safety.fake import FakeFaceDetector, FakeSafetyClassifier
from sqlalchemy.orm import Session


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
    return GoogleGeminiProvider(
        settings.gemini_api_key,
        settings.gemini_model,
        timeout=settings.gemini_request_timeout,
    )


def build_growth(settings: Settings) -> GrowthDraftProvider:
    if settings.gemini_provider == "fake":
        return FakeGrowthDraftProvider()
    # Reuse Google provider growth methods when live Gemini is configured.
    return GoogleGeminiProvider(
        settings.gemini_api_key,
        settings.gemini_model,
        timeout=settings.gemini_request_timeout,
    )


def build_instagram(settings: Settings, session: Session | None = None) -> InstagramClient:
    """Env tokens win when both env and DB are set; otherwise use sealed DB credentials."""
    return build_instagram_client(settings, session)
