from __future__ import annotations

import logging

from instamagic.logging_config import AccessLogQueryMask, install_access_log_mask, mask_url_for_log


def test_callback_code_and_state_are_masked() -> None:
    out = mask_url_for_log(
        "/api/instagram/oauth/callback?code=AQabc-_123&state=1792.ig-oauth%7Cxyz.ff"
    )
    assert "AQabc" not in out and "xyz" not in out
    assert out == "/api/instagram/oauth/callback?code=[REDACTED]&state=[REDACTED]"


def test_callback_error_params_kept() -> None:
    out = mask_url_for_log("/api/instagram/oauth/callback?error=access_denied&state=s1")
    assert out == "/api/instagram/oauth/callback?error=access_denied&state=[REDACTED]"


def test_code_not_masked_outside_callback_but_tokens_are() -> None:
    out = mask_url_for_log("/api/x?code=keep&access_token=EAAsecret&token=t&pending=p&page=2")
    assert (
        out == "/api/x?code=keep&access_token=[REDACTED]&token=[REDACTED]&pending=[REDACTED]&page=2"
    )


def test_plain_paths_untouched() -> None:
    assert mask_url_for_log("/api/queue") == "/api/queue"
    assert mask_url_for_log("/api/images?limit=5") == "/api/images?limit=5"


def test_uvicorn_access_record_rewritten() -> None:
    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        1,
        '%s - "%s %s HTTP/%s" %d',
        ("1.2.3.4:5", "GET", "/api/instagram/oauth/callback?code=SECRET&state=S2", "1.1", 302),
        None,
    )
    assert AccessLogQueryMask().filter(record)
    msg = record.getMessage()
    assert "SECRET" not in msg and "S2" not in msg
    assert "code=[REDACTED]" in msg


def test_install_is_idempotent() -> None:
    install_access_log_mask()
    install_access_log_mask()
    filters = [
        f for f in logging.getLogger("uvicorn.access").filters if isinstance(f, AccessLogQueryMask)
    ]
    assert len(filters) == 1
