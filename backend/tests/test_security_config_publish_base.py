"""PUBLIC_BASE_URL requirements for stage Instagram publish."""

from __future__ import annotations

import pytest
from instamagic.config import Settings


def _settings(**kwargs) -> Settings:
    base = {
        "app_password": "test-password-123",
        "app_secret": "test-secret-value-123456",
        "safety_classifier": "onnx",
        "safety_model_sha256": "c15d8273adad2d0a92f014cc69ab2d6c311a06777a55545f2c4eb46f51911f0f",
        "gemini_provider": "fake",
        "instagram_provider": "graph",
        "environment": "stage",
        "frontend_origin": "https://instamagic.schimmel.homes",
        "public_base_url": "",
        "bind_host": "127.0.0.1",
    }
    base.update(kwargs)
    return Settings(**base)


def test_stage_warns_when_public_base_missing() -> None:
    settings = _settings()
    assert settings.publish_public_base_warning() is not None
    with pytest.raises(ValueError):
        settings.publish_public_base_url()


def test_stage_allows_explicit_tunnel() -> None:
    settings = _settings(public_base_url="https://tunnel.example.invalid")
    assert settings.publish_public_base_warning() is None
    assert settings.publish_public_base_url() == "https://tunnel.example.invalid"
