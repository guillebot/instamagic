from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
WEAK_PASSWORDS = {"change-me", "password", "admin", "instamagic"}
ATTESTATION_STATEMENT = (
    "I confirm that every person depicted in this library is a consenting adult."
)


def _load_dotenv_file(path: Path) -> None:
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    environment: str = "local"
    database_url: str = "postgresql+psycopg://instamagic:instamagic@127.0.0.1:5432/instamagic"
    redis_url: str = "redis://127.0.0.1:6379/0"
    derivatives_root: Path = Path("./data/derivatives")
    allowed_library_roots: str = ""
    suggested_library_root: str = ""
    gemini_api_key: str = ""
    gemini_image_policy: str = "text_only"
    gemini_model: str = "gemini-2.5-flash"
    gemini_provider: str = "google"
    auto_publish: bool = False
    app_password: str = Field(default="")
    app_secret: str = Field(default="")
    safety_model_path: str = ""
    safety_model_sha256: str = ""
    safety_classifier: str = "onnx"
    pipeline_mode: str = "queue"
    bind_host: str = "127.0.0.1"
    allow_container_bind: bool = False
    frontend_origin: str = "http://127.0.0.1:5173"
    session_ttl_seconds: int = 60 * 60 * 12
    log_level: str = "INFO"

    @model_validator(mode="after")
    def _check_security(self) -> Settings:
        if self.auto_publish:
            raise ValueError("AUTO_PUBLISH must stay false. This milestone has no publisher.")
        password = self.app_password.strip()
        if len(password) < 12 or password.lower() in WEAK_PASSWORDS:
            raise ValueError("APP_PASSWORD must be at least 12 characters and not a placeholder.")
        if len(self.app_secret.strip()) < 16:
            raise ValueError("APP_SECRET must be at least 16 characters.")
        if self.bind_host not in LOOPBACK_HOSTS and not self.allow_container_bind:
            raise ValueError("BIND_HOST must be loopback unless ALLOW_CONTAINER_BIND=true.")
        if self.environment == "production" and self.safety_classifier == "fake":
            raise ValueError("SAFETY_CLASSIFIER=fake is refused when ENVIRONMENT=production.")
        if self.gemini_image_policy not in {"text_only", "safe_previews", "manual_only"}:
            raise ValueError("GEMINI_IMAGE_POLICY is invalid.")
        if self.gemini_provider not in {"google", "fake"}:
            raise ValueError("GEMINI_PROVIDER must be google or fake.")
        if self.safety_classifier not in {"onnx", "fake"}:
            raise ValueError("SAFETY_CLASSIFIER must be onnx or fake.")
        if self.pipeline_mode not in {"queue", "inline"}:
            raise ValueError("PIPELINE_MODE must be queue or inline.")
        return self

    def library_roots(self) -> list[Path]:
        return [Path(part) for part in self.allowed_library_roots.split(os.pathsep) if part]


@lru_cache
def get_settings() -> Settings:
    repo_env = Path(__file__).resolve().parents[2] / ".env"
    _load_dotenv_file(repo_env)
    return Settings()
