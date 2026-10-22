from __future__ import annotations

import logging

import structlog


def configure_logging(level: str) -> None:
    logging.basicConfig(format="%(message)s", level=getattr(logging, level.upper(), logging.INFO))
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    def scrub(_logger, _method, event_dict):  # type: ignore[no-untyped-def]
        redacted = dict(event_dict)
        for key in list(redacted):
            lowered = str(key).lower()
            if any(
                part in lowered
                for part in (
                    "password",
                    "token",
                    "secret",
                    "authorization",
                    "api_key",
                    "cookie",
                    "session",
                    "gemini_api",
                )
            ):
                redacted[key] = "[REDACTED]"
        return redacted

    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            scrub,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(getattr(logging, level.upper(), logging.INFO)),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str):  # type: ignore[no-untyped-def]
    return structlog.get_logger(name)
