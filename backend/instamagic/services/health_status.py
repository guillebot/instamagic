"""Aggregated operator health checks (cached, no secrets)."""

from __future__ import annotations

import os
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

import httpx
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from instamagic.config import Settings
from instamagic.db import get_engine, session_factory
from instamagic.errors import NotConfigured
from instamagic.models import Library
from instamagic.services.images.paths import is_mount_point
from instamagic.services.instagram.permissions import graph_auth_failure_detail
from instamagic.services.instagram.store import (
    build_instagram_client,
    connection_status_fields,
    load_cached_account,
    resolve_credentials,
)
from instamagic.services.worker_heartbeat import heartbeat_fresh
from redis import Redis
from rq import Queue, Worker
from sqlalchemy import select, text

HealthLevel = Literal["ok", "warning", "down", "unknown"]
_CHECK_TIMEOUT = 0.9
# The Instagram check runs a live Graph probe (cached 5 min); a cold probe takes ~1-2s.
_CHECK_TIMEOUT_OVERRIDES: dict[str, float] = {"instagram": 4.0}
_MAX_CHECK_TIMEOUT = max([_CHECK_TIMEOUT, *_CHECK_TIMEOUT_OVERRIDES.values()])
_CACHE_SECONDS = 25

_cache_lock = threading.Lock()
_cache_payload: tuple[float, dict[str, Any]] | None = None
_publish_probe_cache: tuple[float, HealthLevel, str] | None = None
_PUBLISH_PROBE_TTL = 600
_instagram_live_probe_cache: tuple[float, str | None, str | None] | None = None
_INSTAGRAM_LIVE_PROBE_TTL = 300
# Syntactically valid publish-asset token (40–1000 chars); signature is bogus.
_PUBLISH_PROBE_BOGUS_TOKEN = "A" * 60


def publish_assets_probe_path() -> str:
    return f"/api/public/publish-assets/{_PUBLISH_PROBE_BOGUS_TOKEN}"


PUBLISH_ASSET_REJECT_HEADER = "X-Instamagic-Asset"


def classify_publish_probe_status(status_code: int, app_marker: bool = False) -> tuple[HealthLevel, str]:
    """Map HEAD publish-assets probe HTTP status to health level.

    The app answers bad tokens with 404 plus the X-Instamagic-Asset marker header
    (identical for every bad token, so no oracle); a bare 404 means Traefik/nginx
    never reached the app.
    """
    if status_code == 409 or (status_code == 404 and app_marker):
        return "ok", "publish-assets route reachable (token rejected)"
    if status_code == 404:
        return "warning", "public route not reaching the app"
    if status_code >= 500:
        return "down", f"publish-assets probe returned {status_code}"
    if 400 <= status_code < 500:
        return "warning", f"publish-assets probe returned {status_code}"
    return "ok", f"publish-assets probe returned {status_code}"


@dataclass(frozen=True)
class HealthCheck:
    id: str
    label: str
    status: HealthLevel
    detail: str


def _worst(levels: list[HealthLevel]) -> HealthLevel:
    order = {"down": 0, "warning": 1, "unknown": 2, "ok": 3}
    return min(levels, key=lambda item: order[item])


def _format_bytes(num: int) -> str:
    value = float(num)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024.0 or unit == "TiB":
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024.0
    return f"{num} B"


def _disk_free_line(path: Path) -> str:
    try:
        usage = shutil.disk_usage(path)
        return f"free {_format_bytes(usage.free)} of {_format_bytes(usage.total)}"
    except OSError as exc:
        return f"disk usage unavailable ({exc.strerror or 'error'})"


def _check_library(settings: Settings) -> HealthCheck:
    session = session_factory()()
    try:
        library = session.scalar(select(Library).order_by(Library.created_at.desc()).limit(1))
        if library is None:
            return HealthCheck(
                id="library",
                label="Library",
                status="warning",
                detail="No photo library registered yet. Add one under Review.",
            )
        root = Path(library.root_path).expanduser()
        allowed = settings.library_roots()
        if not root.is_dir():
            return HealthCheck(
                id="library",
                label="Library",
                status="down",
                detail=f"Path missing or not a directory: {root}",
            )
        if allowed:
            root_resolved = root.resolve()
            under_allowed = any(
                root_resolved == ar.resolve() or str(root_resolved).startswith(str(ar.resolve()) + os.sep)
                for ar in allowed
            )
            if not under_allowed:
                return HealthCheck(
                    id="library",
                    label="Library",
                    status="down",
                    detail=f"Path outside allowed roots: {root_resolved}",
                )
        try:
            with os.scandir(root) as scan:
                next(iter(scan), None)
        except OSError as exc:
            return HealthCheck(
                id="library",
                label="Library",
                status="down",
                detail=f"Not readable: {root} ({exc.strerror or exc})",
            )
        mount_note = ""
        if settings.require_external_library_mounts:
            mount_root = allowed[0].resolve() if allowed else root.resolve()
            if not is_mount_point(mount_root):
                return HealthCheck(
                    id="library",
                    label="Library",
                    status="down",
                    detail=f"Expected external mount missing at {mount_root}",
                )
            mount_note = "mounted; "
        deriv = settings.derivatives_root
        deriv.mkdir(parents=True, exist_ok=True)
        probe = deriv / ".health_write_probe"
        try:
            probe.write_text("ok", encoding="utf-8")
            probe.unlink(missing_ok=True)
        except OSError as exc:
            return HealthCheck(
                id="library",
                label="Library",
                status="down",
                detail=f"Derivatives not writable ({deriv}): {exc.strerror or exc}",
            )
        space = _disk_free_line(root)
        return HealthCheck(
            id="library",
            label="Library",
            status="ok",
            detail=f"{mount_note}{root} — {space}",
        )
    finally:
        session.close()


def probe_instagram_username_live(settings: Settings) -> tuple[str | None, str | None]:
    """Live Graph username probe. Returns (username, auth_error_detail). Never logs tokens."""
    session = session_factory()()
    try:
        client = build_instagram_client(settings, session)
        if not client.configured():
            return None, None
        fetch = getattr(client, "fetch_live_username", None)
        if not callable(fetch):
            return None, None
        try:
            username = fetch()
        except NotConfigured as exc:
            auth = graph_auth_failure_detail(exc.detail)
            return None, auth or "Instagram Graph auth failed (reconnect Meta OAuth)"
        return username, None
    finally:
        session.close()


def _cached_instagram_live_username(settings: Settings) -> tuple[str | None, str | None]:
    global _instagram_live_probe_cache
    now = time.time()
    if _instagram_live_probe_cache and now - _instagram_live_probe_cache[0] < _INSTAGRAM_LIVE_PROBE_TTL:
        return _instagram_live_probe_cache[1], _instagram_live_probe_cache[2]
    username, auth_error = probe_instagram_username_live(settings)
    _instagram_live_probe_cache = (now, username, auth_error)
    return username, auth_error


def _check_instagram(settings: Settings) -> HealthCheck:
    session = session_factory()()
    try:
        creds = resolve_credentials(settings, session)
        configured = creds is not None and creds.source != "none"
        fields = connection_status_fields(settings, session, configured=configured)
        cached = load_cached_account(session)
        expected = (settings.instagram_expected_username or "").strip().lstrip("@").lower()

        if settings.instagram_provider == "fake":
            return HealthCheck(
                id="instagram",
                label="Instagram",
                status="ok",
                detail="Fake provider (dev).",
            )
        if not configured:
            return HealthCheck(
                id="instagram",
                label="Instagram",
                status="warning",
                detail="No Graph credentials configured.",
            )

        live_username, auth_error = _cached_instagram_live_username(settings)
        username = ""
        if live_username:
            username = live_username.lstrip("@").lower()
            username_source = "live"
        elif isinstance(cached, dict):
            username = str(cached.get("username") or "").lstrip("@").lower()
            username_source = "cached"
        else:
            username_source = "unknown"

        detail_parts = [f"source={fields.get('credential_source', 'unknown')}"]
        status: HealthLevel = "ok"
        if auth_error:
            status = "down"
            detail_parts.append(auth_error)
        if username:
            detail_parts.append(f"@{username} ({username_source})")
        elif cached is None:
            detail_parts.append("profile not cached yet")
        if fields.get("account_fetched_at") and username_source != "live":
            detail_parts.append(f"profile cached {fields['account_fetched_at']}")
        if expected:
            if auth_error:
                pass
            elif not username:
                status = "warning"
                detail_parts.append(f"expected @{expected} (username unknown)")
            elif username != expected:
                status = "down"
                detail_parts.append(f"expected @{expected}")
        return HealthCheck(
            id="instagram",
            label="Instagram",
            status=status,
            detail="; ".join(detail_parts),
        )
    finally:
        session.close()


def _probe_public_url(settings: Settings) -> tuple[HealthLevel, str]:
    global _publish_probe_cache
    now = time.time()
    if _publish_probe_cache and now - _publish_probe_cache[0] < _PUBLISH_PROBE_TTL:
        return _publish_probe_cache[1], _publish_probe_cache[2]

    explicit = settings.public_base_url.strip().rstrip("/")
    if not explicit:
        result = ("unknown", "PUBLIC_BASE_URL not set; probe skipped")
        _publish_probe_cache = (now, result[0], result[1])
        return result

    url = f"{explicit.rstrip('/')}{publish_assets_probe_path()}"
    try:
        with httpx.Client(timeout=1.5, follow_redirects=True) as client:
            response = client.head(url)
        level, note = classify_publish_probe_status(
            response.status_code,
            app_marker=response.headers.get(PUBLISH_ASSET_REJECT_HEADER) == "rejected",
        )
    except (httpx.ConnectError, httpx.NetworkError, httpx.TimeoutException) as exc:
        level, note = "down", f"connection failed ({type(exc).__name__})"
    except httpx.HTTPError as exc:
        level, note = "down", f"request failed ({type(exc).__name__})"

    _publish_probe_cache = (now, level, note)
    return level, note


def _check_publish_url(settings: Settings) -> HealthCheck:
    explicit = settings.public_base_url.strip().rstrip("/")
    front = settings.frontend_origin.strip().rstrip("/")
    warning = settings.publish_public_base_warning()

    if settings.instagram_provider != "graph":
        return HealthCheck(
            id="publish_url",
            label="Publish URL",
            status="ok",
            detail="Instagram Graph disabled; PUBLIC_BASE_URL optional.",
        )

    if not explicit:
        probe_level, probe_note = _probe_public_url(settings)
        return HealthCheck(
            id="publish_url",
            label="Publish URL",
            status="warning",
            detail=f"PUBLIC_BASE_URL unset (LAN UI: {front}). {probe_note}",
        )

    parsed = urlparse(explicit)
    if parsed.scheme != "https":
        return HealthCheck(
            id="publish_url",
            label="Publish URL",
            status="warning",
            detail=f"PUBLIC_BASE_URL should be https (got {parsed.scheme or 'none'}): {explicit}",
        )

    status: HealthLevel = "ok"
    detail = explicit
    if warning:
        status = "warning"
        detail = f"{explicit} — {warning}"
    else:
        front_host = urlparse(front).hostname or ""
        pub_host = parsed.hostname or ""
        if front_host and pub_host and front_host == pub_host:
            status = "warning"
            detail = f"{explicit} (same host as UI {front_host})"

    probe_level, probe_note = _probe_public_url(settings)
    if probe_level != "ok" and status == "ok":
        status = probe_level
    detail = f"{detail}; publish-assets probe: {probe_note}"
    return HealthCheck(id="publish_url", label="Publish URL", status=status, detail=detail)


def _check_database(settings: Settings) -> HealthCheck:
    engine = get_engine(settings)
    url = str(engine.url)
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
            if url.startswith("sqlite"):
                return HealthCheck(
                    id="database",
                    label="Database",
                    status="ok",
                    detail="SQLite reachable (dev; migrations not verified).",
                )
            ctx = MigrationContext.configure(conn)
            current = ctx.get_current_revision()
        config_path = Path(__file__).resolve().parents[2] / "alembic.ini"
        script = ScriptDirectory.from_config(Config(str(config_path)))
        heads = script.get_heads()
        head = heads[0] if len(heads) == 1 else ", ".join(heads)
        if current is None:
            return HealthCheck(
                id="database",
                label="Database",
                status="warning",
                detail=f"Postgres reachable but alembic not stamped (head {head}).",
            )
        if current not in heads:
            return HealthCheck(
                id="database",
                label="Database",
                status="down",
                detail=f"Migration {current} is not at head ({head}).",
            )
        return HealthCheck(
            id="database",
            label="Database",
            status="ok",
            detail=f"Postgres reachable; migrations at {current}.",
        )
    except Exception as exc:
        return HealthCheck(
            id="database",
            label="Database",
            status="down",
            detail=f"Database unreachable ({type(exc).__name__}).",
        )


def _queue_worker_alive(connection: Redis, name: str) -> tuple[bool, str]:
    if heartbeat_fresh(connection, name):
        return True, "worker heartbeat fresh"
    queue = Queue(name, connection=connection)
    stale = Worker.all(queue=queue, connection=connection)
    if stale:
        return False, f"{len(stale)} stale RQ registration(s), no fresh heartbeat"
    return False, "no worker"


def _check_queue(settings: Settings) -> HealthCheck:
    if settings.pipeline_mode == "inline":
        return HealthCheck(
            id="queue",
            label="Queue",
            status="ok",
            detail="PIPELINE_MODE=inline (Redis workers optional).",
        )
    try:
        connection = Redis.from_url(settings.redis_url, socket_connect_timeout=0.5, socket_timeout=0.5)
        connection.ping()
    except Exception as exc:
        return HealthCheck(
            id="queue",
            label="Queue",
            status="down",
            detail=f"Redis unreachable ({type(exc).__name__}).",
        )

    missing: list[str] = []
    notes: list[str] = []
    from instamagic.services.queues import QUEUE_GEMINI, QUEUE_IO, QUEUE_ML, QUEUE_SOCIAL

    for name in (QUEUE_IO, QUEUE_ML, QUEUE_GEMINI, QUEUE_SOCIAL):
        alive, note = _queue_worker_alive(connection, name)
        notes.append(f"{name}: {note}")
        if not alive:
            missing.append(name)
    if missing:
        return HealthCheck(
            id="queue",
            label="Queue",
            status="down",
            detail=f"Workers missing for {', '.join(missing)}. " + "; ".join(notes),
        )
    return HealthCheck(
        id="queue",
        label="Queue",
        status="ok",
        detail="Redis reachable; " + "; ".join(notes),
    )


def _check_gemini(settings: Settings) -> HealthCheck:
    if settings.gemini_provider == "fake":
        return HealthCheck(
            id="gemini",
            label="Gemini",
            status="ok",
            detail="Fake provider (dev).",
        )
    if settings.gemini_api_key.strip():
        return HealthCheck(
            id="gemini",
            label="Gemini",
            status="ok",
            detail=f"API key configured ({settings.gemini_model}).",
        )
    return HealthCheck(
        id="gemini",
        label="Gemini",
        status="warning",
        detail="GEMINI_API_KEY not configured.",
    )


def _check_data_disk(settings: Settings) -> HealthCheck:
    root = settings.derivatives_root
    try:
        root.mkdir(parents=True, exist_ok=True)
        usage = shutil.disk_usage(root)
        free = usage.free
        status: HealthLevel = "ok"
        if free < 512 * 1024 * 1024:
            status = "warning"
        if free < 128 * 1024 * 1024:
            status = "down"
        return HealthCheck(
            id="data_disk",
            label="Data disk",
            status=status,
            detail=f"{root.resolve()} — {_format_bytes(free)} free",
        )
    except OSError as exc:
        return HealthCheck(
            id="data_disk",
            label="Data disk",
            status="down",
            detail=f"Cannot read data path ({exc.strerror or exc}).",
        )


def _run_bounded(fn, *args, timeout: float = _CHECK_TIMEOUT) -> HealthCheck:
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(fn, *args)
        try:
            return future.result(timeout=timeout)
        except FuturesTimeoutError:
            return HealthCheck(
                id=getattr(fn, "__name__", "check").removeprefix("_check_"),
                label="Check",
                status="unknown",
                detail="Timed out.",
            )
        except Exception as exc:
            label = getattr(fn, "__name__", "check").removeprefix("_check_").replace("_", " ").title()
            return HealthCheck(
                id=getattr(fn, "__name__", "check").removeprefix("_check_"),
                label=label,
                status="down",
                detail=f"Check failed ({type(exc).__name__}).",
            )


def _run_check(fn, settings: Settings) -> HealthCheck:
    check_id = fn.__name__.removeprefix("_check_")
    labels = {
        "library": "Library",
        "instagram": "Instagram",
        "publish_url": "Publish URL",
        "database": "Database",
        "queue": "Queue",
        "gemini": "Gemini",
        "data_disk": "Data disk",
    }
    try:
        result = _run_bounded(
            fn, settings, timeout=_CHECK_TIMEOUT_OVERRIDES.get(check_id, _CHECK_TIMEOUT)
        )
        if result.id == "check" and check_id:
            return HealthCheck(id=check_id, label=labels.get(check_id, check_id), status=result.status, detail=result.detail)
        return result
    except Exception as exc:
        return HealthCheck(
            id=check_id,
            label=labels.get(check_id, check_id),
            status="down",
            detail=f"Check failed ({type(exc).__name__}).",
        )


def build_health_status(settings: Settings, *, bypass_cache: bool = False) -> dict[str, Any]:
    global _cache_payload
    now = time.time()
    with _cache_lock:
        if (
            not bypass_cache
            and _cache_payload is not None
            and now - _cache_payload[0] < _CACHE_SECONDS
        ):
            return _cache_payload[1]

    check_fns = (
        _check_library,
        _check_instagram,
        _check_publish_url,
        _check_database,
        _check_queue,
        _check_gemini,
        _check_data_disk,
    )
    checks: list[HealthCheck] = []
    with ThreadPoolExecutor(max_workers=len(check_fns)) as pool:
        futures = [pool.submit(_run_check, fn, settings) for fn in check_fns]
        for future in futures:
            checks.append(future.result(timeout=_MAX_CHECK_TIMEOUT + 0.2))

    overall = _worst([check.status for check in checks])
    payload = {
        "overall": overall,
        "checks": [
            {"id": c.id, "label": c.label, "status": c.status, "detail": c.detail} for c in checks
        ],
        "cached_seconds": _CACHE_SECONDS,
    }
    with _cache_lock:
        _cache_payload = (now, payload)
    return payload


def reset_health_cache() -> None:
    global _cache_payload, _publish_probe_cache, _instagram_live_probe_cache
    with _cache_lock:
        _cache_payload = None
        _publish_probe_cache = None
        _instagram_live_probe_cache = None
