"""Token scope helpers — no store imports."""

from __future__ import annotations

import json

from instamagic.services.instagram.graph_constants import INSIGHTS_PERMISSION


def parse_scopes_json(raw: str | None) -> set[str]:
    if not raw:
        return set()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return set()
    if isinstance(payload, list):
        return {str(item) for item in payload}
    return set()


def insights_permission_ok(scopes: set[str]) -> bool:
    return INSIGHTS_PERMISSION in scopes
