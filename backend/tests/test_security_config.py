"""Settings fail-closed guards (AUTO_PUBLISH, fake classifier, model SHA)."""

from __future__ import annotations

import pytest
from instamagic.config import Settings
from pydantic import ValidationError


def _base(**overrides: object) -> dict[str, object]:
    data: dict[str, object] = {
        "app_password": "test-password-123",
        "app_secret": "test-secret-value-123456",
        "safety_classifier": "fake",
        "gemini_provider": "fake",
        "instagram_provider": "fake",
        "auto_publish": False,
        "environment": "local",
        "bind_host": "127.0.0.1",
        "allow_container_bind": False,
    }
    data.update(overrides)
    return data


def test_auto_publish_requires_ack() -> None:
    with pytest.raises(ValidationError, match="AUTO_PUBLISH"):
        Settings(**_base(auto_publish=True, auto_publish_ack=""))
    from instamagic.config import AUTO_PUBLISH_ACK_PHRASE

    enabled = Settings(**_base(auto_publish=True, auto_publish_ack=AUTO_PUBLISH_ACK_PHRASE, instagram_provider="fake"))
    assert enabled.auto_publish is True


def test_fake_classifier_refused_in_stage_and_production() -> None:
    with pytest.raises(ValidationError, match="fake"):
        Settings(**_base(environment="stage", safety_classifier="fake"))
    with pytest.raises(ValidationError, match="fake"):
        Settings(**_base(environment="production", safety_classifier="fake"))


def test_onnx_requires_model_sha256() -> None:
    with pytest.raises(ValidationError, match="SAFETY_MODEL_SHA256"):
        Settings(**_base(safety_classifier="onnx", safety_model_sha256=""))
    settings = Settings(
        **_base(
            safety_classifier="onnx",
            safety_model_sha256="c" * 64,
            safety_model_path="/models/missing.onnx",
        )
    )
    assert settings.require_live_safety_classifier() is False
    stage = Settings(
        **_base(
            environment="stage",
            safety_classifier="onnx",
            safety_model_sha256="c" * 64,
            allow_container_bind=True,
            bind_host="0.0.0.0",
        )
    )
    assert stage.require_live_safety_classifier() is True


def test_weak_password_and_secret_rejected() -> None:
    with pytest.raises(ValidationError, match="APP_PASSWORD"):
        Settings(**_base(app_password="password"))
    with pytest.raises(ValidationError, match="APP_SECRET"):
        Settings(**_base(app_secret="short"))


def test_non_loopback_bind_requires_flag() -> None:
    with pytest.raises(ValidationError, match="BIND_HOST"):
        Settings(**_base(bind_host="0.0.0.0", allow_container_bind=False))


def test_cookie_secure_derives_from_https_origin() -> None:
    http = Settings(**_base(frontend_origin="http://127.0.0.1:5173"))
    assert http.session_cookie_secure() is False
    https = Settings(**_base(frontend_origin="https://instamagic.schimmel.homes"))
    assert https.session_cookie_secure() is True
    forced = Settings(**_base(frontend_origin="http://127.0.0.1:5173", cookie_secure=True))
    assert forced.session_cookie_secure() is True
