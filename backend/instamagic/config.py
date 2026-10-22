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
# Exact phrase required before AUTO_PUBLISH=true is accepted. Human-gated publish remains the default.
AUTO_PUBLISH_ACK_PHRASE = "I_UNDERSTAND_AUTO_PUBLISH_RISKS"


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

    # Build id injected at image build (YYYYMMDD-XX). Default for local/dev.
    app_version: str = "dev"
    environment: str = "local"
    database_url: str = "postgresql+psycopg://instamagic:instamagic@127.0.0.1:5432/instamagic"
    redis_url: str = "redis://127.0.0.1:6379/0"
    derivatives_root: Path = Path("./data/derivatives")
    allowed_library_roots: str = ""
    suggested_library_root: str = ""
    # When true (Compose), every ALLOWED_LIBRARY_ROOTS entry must be a real host bind mount.
    require_external_library_mounts: bool = False
    gemini_api_key: str = ""
    gemini_image_policy: str = "text_only"
    gemini_model: str = "gemini-2.5-flash"
    gemini_provider: str = "google"
    # Fail-closed: false by default. True only with AUTO_PUBLISH_ACK exact phrase.
    # Phase 2 still expects human Publish clicks; auto-fire is reserved and documented.
    auto_publish: bool = False
    auto_publish_ack: str = ""
    app_password: str = Field(default="")
    app_secret: str = Field(default="")
    safety_model_path: str = ""
    safety_model_sha256: str = ""
    safety_classifier: str = "onnx"
    pipeline_mode: str = "queue"
    bind_host: str = "127.0.0.1"
    allow_container_bind: bool = False
    frontend_origin: str = "http://127.0.0.1:5173"
    # Meta Graph must fetch publish images from the public internet. LAN-only stage is not enough.
    public_base_url: str = ""
    instagram_provider: str = "graph"
    instagram_access_token: str = ""
    instagram_business_account_id: str = ""
    instagram_graph_version: str = "v21.0"
    # Optional Meta OAuth (Facebook Login → Page token + IG business account).
    meta_app_id: str = ""
    meta_app_secret: str = ""
    # Defaults to {PUBLIC_BASE_URL|FRONTEND_ORIGIN}/api/instagram/oauth/callback
    meta_oauth_redirect_uri: str = ""

    account_voice: str = (
        "Quiet, craft-first photography captions. Mood, light, and composition. No engagement bait."
    )
    # None derives from FRONTEND_ORIGIN (https → Secure cookies). Explicit true/false overrides.
    cookie_secure: bool | None = None
    session_ttl_seconds: int = 60 * 60 * 12
    log_level: str = "INFO"

    @model_validator(mode="after")
    def _check_security(self) -> Settings:
        if self.auto_publish and self.auto_publish_ack.strip() != AUTO_PUBLISH_ACK_PHRASE:
            raise ValueError(
                "AUTO_PUBLISH=true requires AUTO_PUBLISH_ACK="
                f"{AUTO_PUBLISH_ACK_PHRASE}. Leave AUTO_PUBLISH=false for human-gated publish."
            )
        password = self.app_password.strip()
        if len(password) < 12 or password.lower() in WEAK_PASSWORDS:
            raise ValueError("APP_PASSWORD must be at least 12 characters and not a placeholder.")
        if len(self.app_secret.strip()) < 16:
            raise ValueError("APP_SECRET must be at least 16 characters.")
        if self.bind_host not in LOOPBACK_HOSTS and not self.allow_container_bind:
            raise ValueError("BIND_HOST must be loopback unless ALLOW_CONTAINER_BIND=true.")
        if self.environment in {"production", "stage"} and self.safety_classifier == "fake":
            raise ValueError("SAFETY_CLASSIFIER=fake is refused when ENVIRONMENT is production or stage.")
        if self.safety_classifier == "onnx" and not self.safety_model_sha256.strip():
            raise ValueError("SAFETY_MODEL_SHA256 is required when SAFETY_CLASSIFIER=onnx.")
        if self.gemini_image_policy not in {"text_only", "safe_previews", "manual_only"}:
            raise ValueError("GEMINI_IMAGE_POLICY is invalid.")
        if self.gemini_provider not in {"google", "fake"}:
            raise ValueError("GEMINI_PROVIDER must be google or fake.")
        if self.instagram_provider not in {"graph", "fake"}:
            raise ValueError("INSTAGRAM_PROVIDER must be graph or fake.")
        if self.safety_classifier not in {"onnx", "fake"}:
            raise ValueError("SAFETY_CLASSIFIER must be onnx or fake.")
        if self.pipeline_mode not in {"queue", "inline"}:
            raise ValueError("PIPELINE_MODE must be queue or inline.")
        return self

    def instagram_configured(self) -> bool:
        if self.instagram_provider == "fake":
            return True
        return bool(self.instagram_access_token.strip() and self.instagram_business_account_id.strip())

    def resolve_public_base_url(self) -> str:
        base = (self.public_base_url or self.frontend_origin).strip().rstrip("/")
        return base

    def session_cookie_secure(self) -> bool:
        if self.cookie_secure is not None:
            return self.cookie_secure
        return self.frontend_origin.strip().lower().startswith("https://")

    def require_live_safety_classifier(self) -> bool:
        return self.environment in {"production", "stage"} and self.safety_classifier == "onnx"

    def library_roots(self) -> list[Path]:
        return [Path(part) for part in self.allowed_library_roots.split(os.pathsep) if part]


@lru_cache
def get_settings() -> Settings:
    repo_env = Path(__file__).resolve().parents[2] / ".env"
    _load_dotenv_file(repo_env)
    return Settings()
