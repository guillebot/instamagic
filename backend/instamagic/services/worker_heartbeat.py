"""Lightweight Redis heartbeats so the API can tell io/ml workers are alive."""

from __future__ import annotations

import time

from redis import Redis

HEARTBEAT_PREFIX = "instamagic:worker:heartbeat:"
HEARTBEAT_TTL_SECONDS = 90
HEARTBEAT_INTERVAL_SECONDS = 30


def heartbeat_key(queue_name: str) -> str:
    return f"{HEARTBEAT_PREFIX}{queue_name}"


def write_heartbeat(connection: Redis, queue_name: str) -> None:
    connection.set(heartbeat_key(queue_name), str(int(time.time())), ex=HEARTBEAT_TTL_SECONDS)


def heartbeat_fresh(connection: Redis, queue_name: str, *, max_age_seconds: int = HEARTBEAT_TTL_SECONDS) -> bool:
    raw = connection.get(heartbeat_key(queue_name))
    if not raw:
        return False
    try:
        stamp = int(raw.decode() if isinstance(raw, bytes) else raw)
    except (TypeError, ValueError):
        return False
    return int(time.time()) - stamp <= max_age_seconds
