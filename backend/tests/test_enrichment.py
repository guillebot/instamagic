"""Background enrichment: central-gate eligibility, idempotency, pause/cap, 429 backoff, reuse."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID

from instamagic.config import get_settings
from instamagic.db import session_factory
from instamagic.errors import GeminiRateLimited
from instamagic.models import Caption, Image, ImageScore, PublishingQueueItem
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
        if name == "unrated.jpg":
            continue
        response = client.post(f"/api/images/{image_id}/rating", json={"rating": "safe"})
        if name == "blocked-frame.jpg":
            # Flagged without a verified blur: rating safe is refused (blur and verify first).
            assert response.status_code == 409, response.text
            assert "Blur and verify first" in response.json()["detail"]
        else:
            assert response.status_code == 200
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


def _batch(redis=None, growth=None, batch_size=50, concurrency=2):
    session = session_factory()()
    try:
        return enrichment.run_enrichment_batch(
            session,
            _deps(growth),
            redis=redis,
            concurrency=concurrency,
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
    # Rated tier only here; the prefetch tier for unrated clean photos is covered below.
    assert client.put("/api/enrichment/settings", json={"prefetch_enabled": False}).status_code == 200

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
    image.enrich_hashtags_json = json.dumps(["street", " ", "night", "sexy"])
    assert enrichment.stored_hashtags(image) == ["street", "night"]
    image.enrich_hashtags_json = "not json"
    assert enrichment.stored_hashtags(image) == []


# --- Prefetch tier: unrated photos the local model left at REVIEW with no detections ---------------


def _seed_prefetch(client, library: Path) -> dict[str, str]:
    _marked(library / "s1" / "a-clean.jpg", (30, 30, 30), (20, 20, 80, 80))
    _marked(library / "s1" / "unrated.jpg", (10, 90, 30), (20, 20, 50, 50))
    _marked(library / "s1" / "unrated-two.jpg", (90, 90, 30), (30, 30, 70, 70))
    _marked(library / "s1" / "unsafe-unrated.jpg", (60, 20, 20), (40, 300, 180, 420))
    _marked(library / "s1" / "blocked-unrated.jpg", (20, 20, 60), (40, 300, 180, 420))
    _marked(library / "s2" / "rejected.jpg", (70, 70, 70), (10, 10, 40, 40))
    login(client)
    created = client.post(
        "/api/libraries", json={"name": "P", "root_path": str(library), "confirmed": True, "statement": STATEMENT}
    )
    library_id = created.json()["id"]
    assert client.post(f"/api/libraries/{library_id}/scan").status_code == 202
    items = client.get("/api/images", params={"library_id": library_id, "limit": 50}).json()["items"]
    ids = {item["filename"]: item["id"] for item in items}
    assert client.post(f"/api/images/{ids['a-clean.jpg']}/rating", json={"rating": "safe"}).status_code == 200
    assert client.post(f"/api/images/{ids['rejected.jpg']}/rating", json={"rating": "reject"}).status_code == 200
    return ids


def _queue_count() -> int:
    session = session_factory()()
    try:
        return int(session.scalar(select(func.count()).select_from(PublishingQueueItem)) or 0)
    finally:
        session.close()


def test_prefetch_generates_for_clean_unrated_but_never_makes_them_queueable(client, app_env) -> None:
    from instamagic.services.pipeline import is_attested
    from instamagic.services.recommendations.candidates_bulk import try_enqueue_image
    from instamagic.services.recommendations.gates import gemini_gate, publishing_gate
    from instamagic.services.recommendations.gemini_derivatives import has_approved_covering_blur
    from instamagic.services.recommendations.label_job import publishing_gate_kwargs

    ids = _seed_prefetch(client, app_env["library"])
    for name in ("unrated.jpg", "unrated-two.jpg"):
        image = _image(ids[name])
        assert image.human_rating is None and image.model_state == "REVIEW"
    assert _image(ids["unsafe-unrated.jpg"]).model_state != "REVIEW"

    status = client.get("/api/enrichment/status").json()
    assert status["prefetch_enabled"] is True
    assert status["prefetch_eligible"] == 2
    assert status["prefetched"] == 0

    queue_before = _queue_count()
    _batch(redis=FakeRedis())

    for name in ("unrated.jpg", "unrated-two.jpg"):
        image = _image(ids[name])
        assert image.human_rating is None  # ratings are never touched
        assert image.gemini_label_status == "ok"
        assert enrichment.stored_hashtags(image)
        assert _caption_rows(ids[name])
    # Detector hits, DO_NOT_POST / crop-needed and human-rejected photos are never sent.
    for name in ("unsafe-unrated.jpg", "blocked-unrated.jpg", "rejected.jpg"):
        image = _image(ids[name])
        assert image.gemini_label_status is None, name
        assert image.enrich_hashtags_json is None, name
        assert _caption_rows(ids[name]) == [], name

    status = client.get("/api/enrichment/status").json()
    assert status["prefetched"] == 2
    assert status["prefetch_pending"] == 0
    assert status["ready"] == 1 and status["eligible"] == 1  # rated-safe counts are separate

    # Captions on an unrated photo do NOT make it queueable / publishable / curatable.
    target = ids["unrated.jpg"]
    response = client.post("/api/queue", json={"image_id": target})
    assert response.status_code >= 400, response.text
    assert _queue_count() == queue_before
    session = session_factory()()
    try:
        image = session.get(Image, UUID(target))
        attested = is_attested(session, image.library_id)
        assert not publishing_gate(attested=attested, **publishing_gate_kwargs(session, image)).allowed
        ok, _reason = try_enqueue_image(session, image, covering_blur_fn=has_approved_covering_blur)
        assert ok is False
        session.rollback()
        assert target not in {str(row.id) for row in enrichment.eligible_images(session)}
        gate = gemini_gate(
            attested=attested,
            human_rating=image.human_rating,
            model_state=image.model_state,
            needs_crop=False,
            has_approved_crop=False,
            has_approved_covering_blur=False,
            has_approved_blur=False,
            do_not_post_blur_unlocked=False,
            policy="text_only",
            operator_confirmed=True,
        )
        assert not gate.allowed
    finally:
        session.close()
    assert _queue_count() == queue_before
    created = client.post("/api/curation", json={"limit": 0, "include_images": False, "confirmed": True})
    assert created.status_code == 201, created.text
    picked = {pick["image"]["id"] for pick in created.json()["picks"]}
    assert target not in picked and ids["unrated-two.jpg"] not in picked
    assert _queue_count() == queue_before


def test_prefetch_toggle_cap_priority_and_existing_captions(client, app_env) -> None:
    ids = _seed_prefetch(client, app_env["library"])
    # Toggle off: only the rated tier runs.
    assert client.put("/api/enrichment/settings", json={"prefetch_enabled": False}).json()["prefetch_enabled"] is False
    _batch(redis=FakeRedis())
    assert enrichment.stored_hashtags(_image(ids["a-clean.jpg"]))
    assert _image(ids["unrated.jpg"]).gemini_label_status is None

    # Best aesthetics first.
    session = session_factory()()
    for name, score in (("unrated.jpg", 0.2), ("unrated-two.jpg", 0.9)):
        row = session.scalar(select(ImageScore).where(ImageScore.image_id == UUID(ids[name])))
        if row is None:
            row = ImageScore(image_id=UUID(ids[name]))
            session.add(row)
        row.aesthetic_score = score
    session.commit()
    from instamagic.services.recommendations.prefetch import pending_prefetch_ids

    order = [str(i) for i in pending_prefetch_ids(session, max_score=0.25, limit=10)]
    session.close()
    assert order == [ids["unrated-two.jpg"], ids["unrated.jpg"]]

    # Prefetch cap: 2 calls = one photo (label + hashtags), the best one.
    assert client.put(
        "/api/enrichment/settings", json={"prefetch_enabled": True, "prefetch_daily_cap": 2}
    ).status_code == 200
    redis = FakeRedis()
    _batch(redis=redis, concurrency=1)
    assert enrichment.calls_today(redis, prefetch=True) == 2
    assert enrichment.stored_hashtags(_image(ids["unrated-two.jpg"]))
    assert _image(ids["unrated.jpg"]).enrich_hashtags_json is None
    assert client.get("/api/enrichment/status").json()["prefetched"] == 1

    # A photo that already has captions is not re-labeled by prefetch (no second caption set).
    session = session_factory()()
    image = session.get(Image, UUID(ids["unrated.jpg"]))
    session.add(Caption(image_id=image.id, style="short", language="en", text="Mine.", original_text="Mine.", status="modified"))
    session.commit()
    session.close()
    client.put("/api/enrichment/settings", json={"prefetch_daily_cap": 300})
    _batch(redis=FakeRedis())
    image = _image(ids["unrated.jpg"])
    assert image.gemini_label_status is None
    assert _caption_rows(ids["unrated.jpg"]) == [("Mine.", "modified")]


# --- Strict prefetch rule, pre-send re-check, Gemini refusals and the circuit breaker ----------------


def _add_region(image_id: str, label: str, score: float) -> None:
    from instamagic.models import SafetyAssessment, SafetyRegion

    session = session_factory()()
    try:
        assessment = session.scalar(
            select(SafetyAssessment).where(SafetyAssessment.image_id == UUID(image_id))
        )
        session.add(
            SafetyRegion(
                assessment_id=assessment.id,
                label=label,
                score=score,
                x=1,
                y=1,
                width=5,
                height=5,
                unsafe=False,
            )
        )
        session.commit()
    finally:
        session.close()


def _pending(max_score: float = 0.10) -> set[str]:
    from instamagic.services.recommendations.prefetch import pending_prefetch_ids

    session = session_factory()()
    try:
        return {str(i) for i in pending_prefetch_ids(session, max_score=max_score, limit=50)}
    finally:
        session.close()


def test_prefetch_strict_regions_threshold_clamp_and_folder_rules(client, app_env) -> None:
    from instamagic.services.recommendations.prefetch import effective_max_score

    ids = _seed_prefetch(client, app_env["library"])
    assert _pending() == {ids["unrated.jpg"], ids["unrated-two.jpg"]}
    # Any exposed part (even belly/armpits/feet) or covered intimate part excludes the photo.
    _add_region(ids["unrated.jpg"], "ARMPITS_EXPOSED", 0.3)
    _add_region(ids["unrated-two.jpg"], "FEMALE_BREAST_COVERED", 0.3)
    assert _pending() == set()
    assert client.get("/api/enrichment/status").json()["prefetch_eligible"] == 0
    # The threshold is clamped: it can never be raised above 0.25.
    assert effective_max_score(0.9) == 0.25 and effective_max_score(0.05) == 0.05
    assert _pending(max_score=0.9) == set()


def test_prefetch_folder_requires_safe_sibling_and_no_rejects(client, app_env) -> None:
    ids = _seed_prefetch(client, app_env["library"])
    # A human reject/review anywhere in the shoot folder makes the whole folder ineligible.
    client.post(f"/api/images/{ids['unsafe-unrated.jpg']}/rating", json={"rating": "review"})
    assert _pending() == set()
    client.post(f"/api/images/{ids['unsafe-unrated.jpg']}/rating", json={"rating": "safe"})
    assert ids["unrated.jpg"] in _pending()
    # Without any human safe photo in the folder, nothing is prefetched by default.
    client.post(f"/api/images/{ids['a-clean.jpg']}/rating", json={"rating": "reject"})
    client.post(f"/api/images/{ids['unsafe-unrated.jpg']}/rating", json={"rating": "reject"})
    assert _pending() == set()


def test_prefetch_presend_recheck_blocks_the_gemini_call(client, app_env, monkeypatch) -> None:
    from instamagic.services.recommendations import prefetch as prefetch_module

    ids = _seed_prefetch(client, app_env["library"])
    calls: list[str] = []
    deps = _deps()
    original = deps.label_provider.label_image

    def spy(*args, **kwargs):
        calls.append("label")
        return original(*args, **kwargs)

    monkeypatch.setattr(deps.label_provider, "label_image", spy)
    # The photo becomes ineligible between selection and the send.
    monkeypatch.setattr(prefetch_module, "is_prefetch_candidate", lambda *a, **k: True)
    real_assert = prefetch_module.assert_prefetch_safe

    def flip(session, image, *, max_score):
        raise prefetch_module.PrefetchIneligible(str(image.id))

    monkeypatch.setattr(prefetch_module, "assert_prefetch_safe", flip)
    monkeypatch.setattr(enrichment, "assert_prefetch_safe", flip)
    import instamagic.services.recommendations.label_job as label_job

    if hasattr(label_job, "assert_prefetch_safe"):
        monkeypatch.setattr(label_job, "assert_prefetch_safe", flip)
    session = session_factory()()
    try:
        enrichment.run_enrichment_batch(
            session,
            deps,
            redis=FakeRedis(),
            concurrency=1,
            rpm=6000,
            batch_size=50,
            limiter=enrichment.RateLimiter(60000, sleep=lambda _s: None),
        )
    finally:
        session.close()
    assert real_assert is not flip
    for name in ("unrated.jpg", "unrated-two.jpg"):
        assert _image(ids[name]).gemini_label_status is None
        assert _image(ids[name]).enrich_hashtags_json is None


class _RefusingGrowth:
    def __init__(self, inner):
        self.inner = inner

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def suggest_hashtags(self, context, **kwargs):
        from instamagic.errors import GeminiBlocked

        raise GeminiBlocked("Gemini refused", reason="PROHIBITED_CONTENT")


def test_gemini_refusal_marks_photo_never_resends_and_trips_breaker(client, app_env) -> None:
    ids = _seed_prefetch(client, app_env["library"])
    session = session_factory()()
    clean = session.get(Image, UUID(ids["a-clean.jpg"]))
    clean.gemini_label_status = "ok"
    clean.enrich_hashtags_json = json.dumps(["#x"])
    session.commit()
    session.close()
    queue_before = _queue_count()
    _batch(redis=FakeRedis(), growth=_RefusingGrowth(build_growth(get_settings())), concurrency=1)
    refused = [n for n in ("unrated.jpg", "unrated-two.jpg") if _image(ids[n]).gemini_refused_at]
    assert len(refused) == 1  # breaker (fail-safe without a window) + folder rule stop the rest
    other = ({"unrated.jpg", "unrated-two.jpg"} - set(refused)).pop()
    assert _image(ids[other]).gemini_label_status is None  # never sent
    assert _image(ids[refused[0]]).human_rating is None  # ratings untouched
    status = client.get("/api/enrichment/status").json()
    assert status["prefetch_enabled"] is False
    assert "refused" in (status["prefetch_paused_reason"] or "")
    assert status["gemini_refused"] == 1
    detail = client.get(f"/api/images/{ids[refused[0]]}").json()
    assert detail["gemini_refused"] is True
    # Turning prefetch back on clears the reason, but the folder with a refusal stays excluded.
    on = client.put("/api/enrichment/settings", json={"prefetch_enabled": True}).json()
    assert on["prefetch_enabled"] is True and on["prefetch_paused_reason"] is None
    assert _pending() == set()
    # Even rated safe later, a refused photo is never resent (not auto-eligible, captions blocked).
    target = ids[refused[0]]
    assert client.post(f"/api/images/{target}/rating", json={"rating": "safe"}).status_code == 200
    session = session_factory()()
    try:
        assert target not in {str(r.id) for r in enrichment.eligible_images(session)}
    finally:
        session.close()
    assert client.post(f"/api/images/{target}/captions").status_code >= 400
    assert _image(target).gemini_refused_at is not None
    assert _queue_count() == queue_before


class _WindowRedis(FakeRedis):
    def __init__(self) -> None:
        super().__init__()
        self.lists: dict[str, list[str]] = {}
        self.zsets: dict[str, dict[str, float]] = {}

    def pipeline(self):
        return self

    def execute(self):
        return []

    def lpush(self, key, value):
        self.lists.setdefault(key, []).insert(0, value)

    def ltrim(self, key, start, end):
        self.lists[key] = self.lists.get(key, [])[start : end + 1]

    def lrange(self, key, start, end):
        return list(self.lists.get(key, []))

    def zadd(self, key, mapping):
        self.zsets.setdefault(key, {}).update(mapping)

    def zremrangebyscore(self, key, low, high):
        self.zsets[key] = {k: v for k, v in self.zsets.get(key, {}).items() if not low <= v <= high}

    def zcard(self, key):
        return len(self.zsets.get(key, {}))

    def delete(self, *keys):
        for key in keys:
            self.lists.pop(key, None)
            self.zsets.pop(key, None)
            self.data.pop(key, None)


def test_breaker_trips_on_second_refusal_in_window() -> None:
    from instamagic.services.recommendations.refusals import note_prefetch_outcome, reset_breaker

    redis = _WindowRedis()
    assert note_prefetch_outcome(redis, refused=False) is False
    assert note_prefetch_outcome(redis, refused=True) is False  # one refusal: keep going
    for _ in range(5):
        assert note_prefetch_outcome(redis, refused=False) is False
    assert note_prefetch_outcome(redis, refused=True) is True  # second within window: trip
    reset_breaker(redis)
    assert note_prefetch_outcome(redis, refused=True) is False
    assert note_prefetch_outcome(None, refused=True) is True  # no redis: fail safe
