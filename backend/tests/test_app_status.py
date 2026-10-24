"""GET /api/status — auth, shape, no secrets; uniqueness math; social lane plumbing."""

from __future__ import annotations

import json
import random
import re
from datetime import UTC, datetime

from instamagic.config import ATTESTATION_STATEMENT, get_settings
from instamagic.models import ProcessingJob
from instamagic.services.app_status import reset_app_status_cache, scan_progress
from instamagic.services.images.hashes import hamming
from instamagic.services.pipeline import min_hamming_distances
from instamagic.services.queues import QUEUE_NAMES, QUEUE_SOCIAL
from tests.conftest import login, write_jpeg

SECTIONS = ("health", "scan", "photos", "enrichment", "queue", "publish", "instagram", "sync", "engage", "workers")


def test_status_requires_session(client) -> None:
    assert client.get("/api/status").status_code == 401


def test_status_shape_and_no_secrets(client, app_env) -> None:
    library = app_env["library"]
    for index in range(3):
        write_jpeg(library / f"img-{index}.jpg", color=(20 + index * 60, 40, 80))
    login(client)
    created = client.post(
        "/api/libraries",
        json={"name": "L", "root_path": str(library), "confirmed": True, "statement": ATTESTATION_STATEMENT},
    )
    assert created.status_code == 201
    client.post(f"/api/libraries/{created.json()['id']}/scan")
    reset_app_status_cache()

    response = client.get("/api/status")
    assert response.status_code == 200
    body = response.json()
    for name in SECTIONS:
        assert name in body, name
        assert "error" not in body[name] or name == "health", (name, body[name])
    assert body["photos"]["total"] == 3
    assert body["photos"]["active"] == 3
    assert body["scan"]["current"]["status"] == "succeeded"
    assert len(body["publish"]["per_day"]) == 14
    assert isinstance(body["queue"]["open_slots"], list)

    raw = json.dumps(body)
    settings = get_settings()
    assert settings.app_secret not in raw
    assert settings.app_password not in raw
    assert "access_token" not in raw.lower()
    assert "token_masked" not in raw
    assert not re.search(r"EAA[A-Za-z0-9]{20,}", raw)


def test_scan_progress_eta_and_uniqueness() -> None:
    now = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)
    job = ProcessingJob(
        kind="scan",
        status="running",
        files_seen=500,
        files_processed=100,
        files_skipped=400,
        files_failed=0,
        current_stage="scanning",
        started_at=datetime(2026, 10, 24, 11, 50, tzinfo=UTC),
    )
    progress = scan_progress(job, 1000, now)
    assert progress is not None
    assert progress["expected_total"] == 1000
    assert progress["eta_seconds"] == 600
    job.current_stage = "uniqueness"
    job.detail = "Checking similar photos 320/78636"
    progress = scan_progress(job, 1000, now)
    assert progress["uniqueness"] == {"done": 320, "total": 78636}


def test_min_hamming_matches_pairwise() -> None:
    rng = random.Random(7)
    hashes: list[str | None] = [f"{rng.getrandbits(64):016x}" for _ in range(120)] + ["", None]
    hashes.append(hashes[3])  # exact duplicate → distance 0
    result = min_hamming_distances(hashes)
    for i, left in enumerate(hashes):
        usable = [d for j, right in enumerate(hashes) if j != i if (d := hamming(left, right)) is not None]
        assert result[i] == (min(usable) if usable else None)
    assert result[3] == 0


def test_social_queue_registered() -> None:
    assert QUEUE_SOCIAL in QUEUE_NAMES


def test_slow_section_served_stale_and_refreshed_in_background(monkeypatch):
    from instamagic.services import app_status

    app_status.reset_app_status_cache()
    calls: list[str] = []
    started: list[str] = []

    def fake(settings, session, redis):
        calls.append("x")
        return {"value": len(calls)}

    monkeypatch.setattr(app_status, "_refresh_in_background", lambda name, fn, settings: started.append(name))
    first = app_status._slow_section("enrichment", fake, None, None, None, 1000.0)
    assert first["value"] == 1 and "as_of" in first
    fresh = app_status._slow_section("enrichment", fake, None, None, None, 1010.0)
    assert fresh["value"] == 1 and len(calls) == 1
    stale = app_status._slow_section("enrichment", fake, None, None, None, 1000.0 + 301)
    assert stale["value"] == 1 and len(calls) == 1 and started == ["enrichment"]
    app_status._slow_section("enrichment", fake, None, None, None, 1000.0 + 302)
    assert started == ["enrichment"]
    app_status.reset_app_status_cache()
