"""Background enrichment: central-gate eligibility, idempotency, pause/cap, 429 backoff, reuse."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID

from instamagic.config import get_settings
from instamagic.db import session_factory
from instamagic.errors import GeminiRateLimited
from instamagic.models import Caption, Image
from instamagic.services.factory import build_gemini, build_growth
from instamagic.services.recommendations import enrichment
from instamagic.services.recommendations.gemini_derivatives import curation_derivative_bytes
from PIL import Image as PILImage
from PIL import ImageDraw
from sqlalchemy import func, select
from tests.conftest import login

STATEMENT = "I confirm that every person depicted in this library is a consenting adult."


class FakeRedis:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    def get(self, key):
        return self.data.get(key)

    def set(self, key, value, ex=None, nx=False):
        if nx and key in self.data:
            return False
        self.data[key] = str(value)
        return True

    def incr(self, key):
        self.data[key] = str(int(self.data.get(key, 0)) + 1)
        return int(self.data[key])

    def expire(self, key, seconds):
        return True

    def delete(self, *keys):
        for key in keys:
            self.data.pop(key, None)

    def exists(self, key):
        return int(key in self.data)


def _marked(path: Path, color, box) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image = PILImage.new("RGB", (400, 600), color)
    ImageDraw.Draw(image).rectangle(box, fill=(230, 230, 230))
    image.save(path, format="JPEG", quality=90)


def _seed(client, library: Path) -> dict[str, str]:
    _marked(library / "s1" / "a-clean.jpg", (30, 30, 30), (20, 20, 80, 80))
    _marked(library / "s1" / "b-clean.jpg", (90, 30, 30), (60, 60, 120, 120))
    _marked(library / "s1" / "blocked-frame.jpg", (40, 40, 90), (40, 300, 180, 420))
    _marked(library / "s1" / "unrated.jpg", (10, 90, 30), (20, 20, 50, 50))
    login(client)
    created = client.post(
        "/api/libraries", json={"name": "A", "root_path": str(library), "confirmed": True, "statement": STATEMENT}
    )
    library_id = created.json()["id"]
    assert client.post(f"/api/libraries/{library_id}/scan").status_code == 202
    items = client.get("/api/images", params={"library_id": library_id, "limit": 50}).json()["items"]
    ids = {item["filename"]: item["id"] for item in items}
    for name, image_id in ids.items():
        if name != "unrated.jpg":
            assert client.post(f"/api/images/{image_id}/rating", json={"rating": "safe"}).status_code == 200
    return ids


def _deps(growth=None) -> enrichment.EnrichmentDeps:
    settings = get_settings()
    return enrichment.EnrichmentDeps(
        session_factory=session_factory,
        label_provider=build_gemini(settings),
        growth_provider=growth or build_growth(settings),
        preview_bytes_fn=lambda s, image: curation_derivative_bytes(s, settings, image),
        policy=settings.gemini_image_policy,
        account_voice=settings.account_voice,
        thinking_budget=128,
        max_output_tokens=4096,
    )


def _batch(redis=None, growth=None, batch_size=50):
    session = session_factory()()
    try:
        return enrichment.run_enrichment_batch(
            session,
            _deps(growth),
            redis=redis,
            concurrency=2,
            rpm=6000,
            batch_size=batch_size,
            limiter=enrichment.RateLimiter(60000, sleep=lambda _s: None),
        )
    finally:
        session.close()


def _image(image_id: str) -> Image:
    session = session_factory()()
    try:
        image = session.get(Image, UUID(image_id))
        session.expunge(image)
        return image
    finally:
        session.close()


def _caption_rows(image_id: str) -> list[tuple[str, str]]:
    session = session_factory()()
    try:
        rows = session.scalars(select(Caption).where(Caption.image_id == UUID(image_id))).all()
        return sorted((row.text, row.status) for row in rows)
    finally:
        session.close()


def test_eligibility_uses_central_gate_and_stores_hashtags(client, app_env) -> None:
    ids = _seed(client, app_env["library"])
    blocked = _image(ids["blocked-frame.jpg"])
    assert blocked.model_state == "DO_NOT_POST"
    assert blocked.blur_unlock_status != "verified"

    summary = _batch(redis=FakeRedis())
    assert summary["state"] == "running"

    for name in ("a-clean.jpg", "b-clean.jpg"):
        image = _image(ids[name])
        assert image.gemini_label_status == "ok"
        assert enrichment.stored_hashtags(image), name
    # DO_NOT_POST without verified blur unlock and unrated frames never reach Gemini.
    for name in ("blocked-frame.jpg", "unrated.jpg"):
        image = _image(ids[name])
        assert image.enrich_hashtags_json is None
        assert image.gemini_label_status is None
        assert _caption_rows(ids[name]) == []

    status = client.get("/api/enrichment/status").json()
    assert status["eligible"] == 2
    assert status["ready"] == 2
    assert status["pending"] == 0
    assert status["state"] == "idle"
    assert status["enabled"] is True

    # Hashtag endpoint returns the stored set without a new Gemini call.
    stored = client.post(f"/api/images/{ids['a-clean.jpg']}/hashtags", json={"confirmed": True}).json()
    assert stored["stored"] is True
    assert stored["tags"] == enrichment.stored_hashtags(_image(ids["a-clean.jpg"]))
    assert client.get(f"/api/images/{ids['a-clean.jpg']}").json()["enrich_hashtags"] == stored["tags"]


def test_never_overwrites_existing_captions(client, app_env) -> None:
    ids = _seed(client, app_env["library"])
    target = ids["a-clean.jpg"]
    session = session_factory()()
    image = session.get(Image, UUID(target))
    captions = session.scalars(select(Caption).where(Caption.image_id == image.id)).all()
    assert captions
    captions[0].text = "My own words."
    captions[0].status = "modified"
    # Force a re-label: enrichment must not add a second caption set.
    image.gemini_label_status = None
    image.gemini_label_attempts = 0
    session.commit()
    session.close()
    before = _caption_rows(target)

    _batch(redis=FakeRedis())
    assert _caption_rows(target) == before
    assert ("My own words.", "modified") in before
    assert _image(target).gemini_label_status == "ok"


def test_pause_disable_and_daily_cap(client, app_env) -> None:
    ids = _seed(client, app_env["library"])
    assert client.post("/api/enrichment/pause").json()["state"] == "paused"
    assert _batch(redis=FakeRedis())["state"] == "paused"
    assert _image(ids["a-clean.jpg"]).enrich_hashtags_json is None
    assert client.post("/api/enrichment/resume").json()["paused"] is False

    assert client.put("/api/enrichment/settings", json={"enabled": False}).json()["state"] == "disabled"
    assert _batch(redis=FakeRedis())["state"] == "disabled"
    client.put("/api/enrichment/settings", json={"enabled": True, "daily_cap": 1})

    redis = FakeRedis()
    summary = _batch(redis=redis)
    assert enrichment.calls_today(redis) <= 1
    assert summary["state"] == "capped"
    ready = [name for name in ("a-clean.jpg", "b-clean.jpg") if _image(ids[name]).enrich_hashtags_json]
    assert len(ready) == 1
    assert _batch(redis=redis)["state"] == "capped"


def test_rate_limit_backs_off_without_burning_attempts(client, app_env) -> None:
    ids = _seed(client, app_env["library"])

    class Limited:
        def suggest_hashtags(self, context, *, account_voice):
            raise GeminiRateLimited("Gemini rate limit reached.")

    redis = FakeRedis()
    summary = _batch(redis=redis, growth=Limited())
    assert summary["state"] == "backoff"
    assert enrichment.backoff_until(redis) > 0
    image = _image(ids["a-clean.jpg"])
    assert image.enrich_hashtags_json is None
    assert (image.enrich_attempts or 0) == 0
    assert _batch(redis=redis)["state"] == "backoff"


def test_errors_are_counted_and_retryable(client, app_env) -> None:
    ids = _seed(client, app_env["library"])

    class Broken:
        def suggest_hashtags(self, context, *, account_voice):
            raise RuntimeError("boom")

    for _ in range(enrichment.MAX_ATTEMPTS):
        _batch(redis=FakeRedis(), growth=Broken())
    status = client.get("/api/enrichment/status").json()
    assert status["errors"] == 2
    assert status["errors_exhausted"] == 2
    assert status["pending"] == 0
    retried = client.post("/api/enrichment/retry").json()
    assert retried["reset"] == 2
    assert retried["pending"] == 2
    _batch(redis=FakeRedis())
    assert enrichment.stored_hashtags(_image(ids["b-clean.jpg"]))


def test_curation_reuses_pregenerated_captions_and_hashtags(client, app_env) -> None:
    ids = _seed(client, app_env["library"])
    _batch(redis=FakeRedis())
    target = ids["a-clean.jpg"]
    before = _caption_rows(target)
    created = client.post("/api/curation", json={"limit": 0, "include_images": False, "confirmed": True})
    assert created.status_code == 201, created.text
    picks = {pick["image"]["id"]: pick for pick in created.json()["picks"]}
    assert target in picks
    assert _caption_rows(target) == before
    assert picks[target]["suggested_tags"] == enrichment.stored_hashtags(_image(target))
    session = session_factory()()
    try:
        count = session.scalar(select(func.count()).select_from(Caption).where(Caption.image_id == UUID(target)))
    finally:
        session.close()
    assert count == len(before)


def test_rate_limiter_spacing() -> None:
    now = [0.0]
    slept: list[float] = []

    def sleep(seconds: float) -> None:
        slept.append(seconds)

    limiter = enrichment.RateLimiter(30, clock=lambda: now[0], sleep=sleep)
    limiter.acquire()
    limiter.acquire()
    limiter.acquire()
    assert slept == [2.0, 4.0]


def test_hashtags_json_roundtrip() -> None:
    image = Image()
    image.enrich_hashtags_json = json.dumps(["a", " ", "b"])
    assert enrichment.stored_hashtags(image) == ["a", "b"]
    image.enrich_hashtags_json = "not json"
    assert enrichment.stored_hashtags(image) == []
