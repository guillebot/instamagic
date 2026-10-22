from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from instamagic.api.routes import router
from instamagic.config import get_settings
from instamagic.db import get_engine, session_factory
from instamagic.errors import AppError
from instamagic.logging_config import configure_logging, get_logger
from instamagic.services.factory import (
    build_classifier,
    build_face_detector,
    build_gemini,
    build_growth,
    build_instagram,
)
from instamagic.services.pipeline import seed_tags


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings.log_level)
    if settings.require_external_library_mounts:
        from instamagic.services.images.paths import assert_configured_external_mounts

        assert_configured_external_mounts(settings.library_roots())
    settings.derivatives_root.mkdir(parents=True, exist_ok=True)
    classifier = build_classifier(settings)
    if settings.require_live_safety_classifier() and not classifier.available():
        raise RuntimeError(
            "Safety classifier failed to load (missing model or SHA-256 mismatch). "
            "Refusing to start in stage/production."
        )
    app.state.classifier = classifier
    app.state.face_detector = build_face_detector(settings)
    app.state.gemini = build_gemini(settings)
    app.state.growth = build_growth(settings)
    app.state.instagram = build_instagram(settings)
    app.state.login_failures = {}
    session = session_factory()()
    try:
        seed_tags(session)
    finally:
        session.close()
    get_logger("api").info(
        "startup",
        bind_host=settings.bind_host,
        policy=settings.gemini_image_policy,
        detector_available=bool(classifier.available()),
        cookie_secure=settings.session_cookie_secure(),
        instagram_configured=settings.instagram_configured(),
        auto_publish=settings.auto_publish,
    )
    yield
    get_engine().dispose()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="Instamagic", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.frontend_origin],
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["Content-Type"],
    )
    app.include_router(router)

    @app.exception_handler(AppError)
    async def app_error(_request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    return app
