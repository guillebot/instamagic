"""Token scope helpers — no store imports."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Literal

from instamagic.services.instagram.graph_constants import COMMENTS_PERMISSION, INSIGHTS_PERMISSION

GraphErrorKind = Literal["permission", "auth", "other"]

_GRAPH_CODE_RE = re.compile(r"\(#(\d+)\)")


@dataclass(frozen=True)
class FetchedScopes:
    """Result of a scope lookup. ``determined=False`` means pre-sync gates must probe Graph."""

    scopes: frozenset[str]
    determined: bool


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


def comments_permission_ok(scopes: set[str]) -> bool:
    return COMMENTS_PERMISSION in scopes


def scopes_from_debug_token_data(data: dict[str, Any]) -> set[str]:
    """Parse ``debug_token`` ``data`` payload (scopes + granular_scopes)."""
    granted: set[str] = set()
    raw_scopes = data.get("scopes")
    if isinstance(raw_scopes, list):
        granted.update(str(item).strip() for item in raw_scopes if str(item).strip())
    granular = data.get("granular_scopes")
    if isinstance(granular, list):
        for row in granular:
            if not isinstance(row, dict):
                continue
            scope = str(row.get("scope") or "").strip()
            if scope:
                granted.add(scope)
    return granted


def graph_error_code_from_detail(detail: str) -> int | None:
    match = _GRAPH_CODE_RE.search(detail)
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def classify_graph_error_detail(detail: str) -> GraphErrorKind:
    """Classify Meta Graph errors embedded in ``NotConfigured`` messages."""
    lowered = detail.lower()
    code = graph_error_code_from_detail(detail)
    if code in {190, 102, 463}:
        return "auth"
    if code in {10, 200}:
        return "permission"
    if "invalid oauth" in lowered or "expired" in lowered or "session has expired" in lowered:
        return "auth"
    if "permission" in lowered and ("denied" in lowered or "required" in lowered):
        return "permission"
    return "other"


def graph_permission_missing_detail(detail: str) -> bool:
    return classify_graph_error_detail(detail) == "permission"


def skip_insights_for_known_scopes(fetched: FetchedScopes) -> bool:
    if not fetched.determined or not fetched.scopes:
        return False
    return not insights_permission_ok(set(fetched.scopes))


def skip_comments_for_known_scopes(fetched: FetchedScopes) -> bool:
    if not fetched.determined or not fetched.scopes:
        return False
    return not comments_permission_ok(set(fetched.scopes))


def graph_auth_failure_detail(detail: str) -> str | None:
    kind = classify_graph_error_detail(detail)
    if kind != "auth":
        return None
    code = graph_error_code_from_detail(detail)
    if code == 190 or "expired" in detail.lower():
        return "access token expired or invalid (reconnect Meta OAuth)"
    return "access token invalid (reconnect Meta OAuth)"
