from __future__ import annotations

import logging
import re

import structlog

# Query parameters whose values must never reach access logs.
_SENSITIVE_QUERY_KEYS = (
    "access_token",
    "input_token",
    "fb_exchange_token",
    "token",
    "client_secret",
    "pending",
)
# OAuth callback carries a one-time code and signed state (nonce) in the query.
_OAUTH_CALLBACK_KEYS = ("code", "state")
_OAUTH_CALLBACK_PATH = "/api/instagram/oauth/callback"
_REDACTED = "[REDACTED]"


def _key_pattern(keys: tuple[str, ...]) -> re.Pattern[str]:
    alternatives = "|".join(re.escape(k) for k in keys)
    return re.compile(rf"(?i)([?&](?:{alternatives})=)[^&#\s]*")


_SENSITIVE_RE = _key_pattern(_SENSITIVE_QUERY_KEYS)
_CALLBACK_RE = _key_pattern(_OAUTH_CALLBACK_KEYS + _SENSITIVE_QUERY_KEYS)


def mask_url_for_log(url: str) -> str:
    """Mask secret query values (tokens, OAuth code/state) in a logged path or URL."""
    if not url or "?" not in url:
        return url
    path = url.split("?", 1)[0]
    pattern = _CALLBACK_RE if path.rstrip("/").endswith(_OAUTH_CALLBACK_PATH) else _SENSITIVE_RE
    return pattern.sub(rf"\1{_REDACTED}", url)


class AccessLogQueryMask(logging.Filter):
    """Rewrite uvicorn access-log records so query secrets are not written."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) >= 3 and isinstance(args[2], str):
            record.args = (*args[:2], mask_url_for_log(args[2]), *args[3:])
        return True


def install_access_log_mask() -> None:
    logger = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, AccessLogQueryMask) for f in logger.filters):
        logger.addFilter(AccessLogQueryMask())


def configure_logging(level: str) -> None:
    logging.basicConfig(format="%(message)s", level=getattr(logging, level.upper(), logging.INFO))
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    install_access_log_mask()

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
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str):  # type: ignore[no-untyped-def]
    return structlog.get_logger(name)
