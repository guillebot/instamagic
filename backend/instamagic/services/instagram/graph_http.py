"""Shared Graph HTTP helpers (pagination, JSON errors). Tokens are never logged."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import httpx
from instamagic.errors import NotConfigured


def graph_error_fields(payload: dict[str, Any]) -> tuple[int | None, str]:
    error = payload.get("error")
    if not isinstance(error, dict):
        return None, "request failed"
    code_raw = error.get("code")
    code = int(code_raw) if isinstance(code_raw, int) else None
    message = str(error.get("message") or "request failed")
    if code is not None:
        return code, f"{message} (#{code})"
    return None, message


def graph_json_or_raise(response: httpx.Response, *, action: str) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError as exc:
        raise NotConfigured(f"Instagram {action} returned non-JSON.") from exc
    if response.status_code >= 400:
        if isinstance(payload, dict):
            _code, message = graph_error_fields(payload)
        else:
            message = "request failed"
        raise NotConfigured(f"Instagram {action} failed: {message}")
    if not isinstance(payload, dict):
        raise NotConfigured(f"Instagram {action} returned an unexpected payload.")
    return payload


def graph_get_page(
    url: str,
    *,
    params: dict[str, Any] | None,
    timeout: float,
    action: str,
) -> dict[str, Any]:
    try:
        response = httpx.get(url, params=params, timeout=timeout)
    except httpx.HTTPError as exc:
        raise NotConfigured(f"Instagram {action} request failed.") from exc
    return graph_json_or_raise(response, action=action)


def graph_paginate(
    base_url: str,
    path: str,
    *,
    access_token: str,
    params: dict[str, Any] | None,
    timeout: float,
    action: str,
    max_pages: int = 20,
) -> Iterator[dict[str, Any]]:
    url = f"{base_url.rstrip('/')}/{path.lstrip('/')}"
    query = dict(params or {})
    query["access_token"] = access_token
    pages = 0
    while url and pages < max_pages:
        payload = graph_get_page(
            url,
            params=query if pages == 0 else None,
            timeout=timeout,
            action=action,
        )
        for row in payload.get("data") or []:
            if isinstance(row, dict):
                yield row
        pages += 1
        paging = payload.get("paging")
        url = paging.get("next") if isinstance(paging, dict) else None
        query = {}
