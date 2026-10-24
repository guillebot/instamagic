# Environment must be set before the application settings are first loaded.
# ruff: noqa: E402
import os
from pathlib import Path

os.environ.setdefault("APP_PASSWORD", "test-password-123")
os.environ.setdefault("APP_SECRET", "test-secret-value-123456")
os.environ.setdefault("SAFETY_CLASSIFIER", "fake")
os.environ.setdefault("GEMINI_PROVIDER", "fake")
os.environ.setdefault("INSTAGRAM_PROVIDER", "fake")
os.environ.setdefault("PIPELINE_MODE", "inline")
os.environ.setdefault("GEMINI_IMAGE_POLICY", "text_only")
os.environ.setdefault("ENVIRONMENT", "local")
os.environ.setdefault("BIND_HOST", "127.0.0.1")
os.environ.setdefault("AUTO_PUBLISH", "false")

import pytest
from fastapi.testclient import TestClient
from instamagic.config import get_settings
from instamagic.db import get_engine, reset_engine
from instamagic.main import create_app
from instamagic.models import Base
from PIL import Image


@pytest.fixture
def app_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    database = tmp_path / "test.db"
    derivatives = tmp_path / "derivatives"
    library = tmp_path / "library"
    library.mkdir()
    monkeypatch.setenv("DATABASE_URL", f"sqlite+pysqlite:///{database}")
    monkeypatch.setenv("DERIVATIVES_ROOT", str(derivatives))
    monkeypatch.setenv("ALLOWED_LIBRARY_ROOTS", str(tmp_path))
    monkeypatch.setenv("APP_PASSWORD", "test-password-123")
    monkeypatch.setenv("APP_SECRET", "test-secret-value-123456")
    monkeypatch.setenv("SAFETY_CLASSIFIER", "fake")
    monkeypatch.setenv("GEMINI_PROVIDER", "fake")
    monkeypatch.setenv("INSTAGRAM_PROVIDER", "fake")
    monkeypatch.setenv("PIPELINE_MODE", "inline")
    monkeypatch.setenv("GEMINI_IMAGE_POLICY", "text_only")
    monkeypatch.setenv("AUTO_PUBLISH", "false")
    monkeypatch.setenv("PUBLIC_BASE_URL", "http://testserver")
    get_settings.cache_clear()
    reset_engine()
    Base.metadata.create_all(get_engine())
    yield {"library": library, "derivatives": derivatives, "root": tmp_path}
    reset_engine()
    get_settings.cache_clear()


@pytest.fixture
def client(app_env):
    with TestClient(create_app()) as test_client:
        yield test_client


def login(client: TestClient) -> None:
    response = client.post("/api/auth/login", json={"password": "test-password-123"})
    assert response.status_code == 200


def write_jpeg(path: Path, size: tuple[int, int] = (400, 600), color: tuple[int, int, int] = (20, 40, 80)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color).save(path, format="JPEG", quality=90)


def legacy_rate(image_id: str, rating: str) -> None:
    """Rate an image bypassing the blur-verify-first API gate.

    Simulates rows rated before the gate existed (the audit demotes those); tests use it to
    prove every downstream gate still blocks such legacy rows on its own.
    """
    from uuid import UUID

    from instamagic.db import session_factory
    from instamagic.models import Image
    from instamagic.services.pipeline import rate_image

    session = session_factory()()
    try:
        image = session.get(Image, UUID(str(image_id)))
        assert image is not None
        rate_image(session, image, rating)
        session.commit()
    finally:
        session.close()
