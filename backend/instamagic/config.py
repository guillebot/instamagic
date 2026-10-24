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
    # Product default: derivative previews may go to Gemini for gated Safe/Publishable frames.
    # Opt out with text_only. Curate UI can still toggle include_images per run under this ceiling.
    gemini_image_policy: str = "preview"
    gemini_model: str = "gemini-2.5-flash"
    gemini_provider: str = "google"
    gemini_request_timeout: float = 90.0
    gemini_label_concurrency: int = 3
    gemini_label_thinking_budget: int = 128
    gemini_curation_thinking_budget: int = 128
    gemini_label_max_output_tokens: int = 4096
    # Background enrichment (labels + captions + hashtags for safe photos) on the gemini lane.
    enrich_concurrency: int = 2
    enrich_rpm: int = 30
    enrich_batch_size: int = 12
    # Prefetch (unrated photos) is clearly-safe only: every NSFW score and every intimate body-part
    # detection must stay below this. Hard ceiling 0.25 (the review threshold); higher values are clamped.
    enrich_prefetch_max_score: float = Field(default=0.10, ge=0.0)
    # Prefetch only photos whose shoot folder already has a human safe/excellent rating.
    enrich_prefetch_require_safe_folder: bool = True
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
    instagram_graph_version: str = "v25.0"
    # When multiple Pages have IG accounts, prefer this @username (without @).
    instagram_expected_username: str = ""
    # Optional Meta OAuth (Facebook Login → Page token + IG business account).
    meta_app_id: str = ""
    meta_app_secret: str = ""
    # Facebook Login for Business configuration id (uses config_id instead of scope when set).
    meta_oauth_config_id: str = ""
    # Defaults to {FRONTEND_ORIGIN}/api/instagram/oauth/callback (not PUBLIC_BASE_URL).
    meta_oauth_redirect_uri: str = ""

    account_voice: str = (
        "Quiet, craft-first photography captions. Mood, light, and composition. No engagement bait."
    )
    # Blend historical IG engagement into Curate/local ranking (0 = off, max 0.5).
    curate_performance_weight: float = 0.15
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
        from instamagic.services.gemini.context import GEMINI_IMAGE_POLICIES

        if self.gemini_image_policy not in GEMINI_IMAGE_POLICIES:
            raise ValueError(
                "GEMINI_IMAGE_POLICY must be text_only, safe_previews, preview, selected, or manual_only."
            )
        if self.gemini_provider not in {"google", "fake"}:
            raise ValueError("GEMINI_PROVIDER must be google or fake.")
        if self.instagram_provider not in {"graph", "fake"}:
            raise ValueError("INSTAGRAM_PROVIDER must be graph or fake.")
        if self.safety_classifier not in {"onnx", "fake"}:
            raise ValueError("SAFETY_CLASSIFIER must be onnx or fake.")
        if self.pipeline_mode not in {"queue", "inline"}:
            raise ValueError("PIPELINE_MODE must be queue or inline.")
        if not 0.0 <= self.curate_performance_weight <= 0.5:
            raise ValueError("CURATE_PERFORMANCE_WEIGHT must be between 0 and 0.5.")
        return self

    def instagram_configured(self) -> bool:
        if self.instagram_provider == "fake":
            return True
        return bool(self.instagram_access_token.strip() and self.instagram_business_account_id.strip())

    def resolve_public_base_url(self) -> str:
        """Display / legacy fallback — prefer ``publish_public_base_url()`` for Graph publish."""
        explicit = self.public_base_url.strip().rstrip("/")
        if explicit:
            return explicit
        return self.frontend_origin.strip().rstrip("/")

    def publish_public_base_warning(self) -> str | None:
        if self.instagram_provider != "graph":
            return None
        explicit = self.public_base_url.strip().rstrip("/")
        front = self.frontend_origin.strip().rstrip("/")
        if self.environment in {"stage", "production"} and not explicit:
            return (
                "PUBLIC_BASE_URL is not set. Meta cannot fetch publish JPEGs from a LAN-only "
                "Traefik host. Expose only https://<host>/api/public/publish-assets/ via "
                "Cloudflare Tunnel (or similar) and set PUBLIC_BASE_URL to that public origin."
            )
        if explicit and front and explicit == front and self.environment in {"stage", "production"}:
            return (
                "PUBLIC_BASE_URL matches FRONTEND_ORIGIN. If that host is LAN-restricted, "
                "Instagram publish will fail until PUBLIC_BASE_URL points at a publicly "
                "reachable tunnel origin serving /api/public/publish-assets/."
            )
        return None

    def publish_public_base_url(self) -> str:
        """HTTPS origin Meta's crawler uses for publish-assets tokens."""
        warning = self.publish_public_base_warning()
        explicit = self.public_base_url.strip().rstrip("/")
        if warning and not explicit:
            raise ValueError(warning)
        if explicit:
            if warning:
                raise ValueError(warning)
            return explicit
        base = self.frontend_origin.strip().rstrip("/")
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
