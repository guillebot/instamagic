"""Growth features: collaborators payload, Reel slot, 1-in-5 skin/body cap, content ranking."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from instamagic.errors import NotAllowed
from instamagic.services.instagram import client as client_mod
from instamagic.services.posting import slots as slots_mod
from instamagic.services.posting.collaborators import (
    normalize_collaborators,
    validate_collaborators,
)
from instamagic.services.posting.content_mix import (
    SENSITIVE_ONE_IN,
    classify_image,
    violates_one_in_n,
)
from instamagic.services.recommendations.rank import CandidateInput, rank_candidates
from tests.conftest import login
from tests.test_publish import _attested_library, _marked
from tests.test_slot_gap_fill import DAYS, _config, _local, _patch

# --- collaborators -------------------------------------------------------------------------


def test_collaborator_handles_validated_and_capped() -> None:
    assert validate_collaborators(["@Model.One", "https://www.instagram.com/model_two/", " "]) == [
        "model.one",
        "model_two",
    ]
    for bad in (["bad handle"], ["a..b"], [".dot"], ["x" * 31], ["a", "b", "c", "d"]):
        with pytest.raises(NotAllowed):
            validate_collaborators(bad)
    assert normalize_collaborators(["a", "b", "c", "d", "bad handle"]) == ["a", "b", "c"]


def _graph(monkeypatch) -> tuple[client_mod.GraphInstagramClient, list[dict]]:
    posted: list[dict] = []

    class _Resp:
        status_code = 200

        def __init__(self, n: int) -> None:
            self._n = n

        def json(self) -> dict:
            return {"id": f"c{self._n}"}

    def fake_post(url, data=None, timeout=None, **_kw):
        posted.append({"url": url, **{k: v for k, v in (data or {}).items() if k != "access_token"}})
        return _Resp(len(posted))

    monkeypatch.setattr(client_mod.httpx, "post", fake_post)
    monkeypatch.setattr(client_mod, "_json_or_raise", lambda response, action: response.json())
    graph = client_mod.GraphInstagramClient(access_token="t", business_account_id="1")
    monkeypatch.setattr(graph, "_wait_for_container", lambda *a, **k: None)
    monkeypatch.setattr(graph, "_publish_container", lambda cid: "m1")
    monkeypatch.setattr(graph, "_permalink", lambda mid: None)
    return graph, posted


def test_graph_collaborators_payload_feed_carousel_reel_not_story(monkeypatch) -> None:
    graph, posted = _graph(monkeypatch)
    graph.publish_image(image_url="https://x/a.jpg", caption="c", collaborators=["@Model.One", "two"])
    assert json.loads(posted[-1]["collaborators"]) == ["model.one", "two"]

    posted.clear()
    graph.publish_carousel(image_urls=["https://x/a.jpg", "https://x/b.jpg"], caption="c", collaborators=["one"])
    children, parent = posted[:2], posted[-1]
    assert all("collaborators" not in child for child in children)
    assert parent["media_type"] == "CAROUSEL" and json.loads(parent["collaborators"]) == ["one"]

    posted.clear()
    graph.publish_reel(video_url="https://x/r.mp4", caption="c", cover_url="https://x/cover.jpg", collaborators=["one"])
    reel = posted[-1]
    assert reel["media_type"] == "REELS" and reel["share_to_feed"] == "true"
    assert reel["cover_url"] == "https://x/cover.jpg" and json.loads(reel["collaborators"]) == ["one"]

    posted.clear()
    graph.publish_story(image_url="https://x/s.jpg")
    assert "collaborators" not in posted[-1]

    posted.clear()
    graph.publish_image(image_url="https://x/a.jpg", caption="c")
    assert "collaborators" not in posted[-1]


def test_api_collaborators_published_and_remembered_for_shoot(client, app_env) -> None:
    library: Path = app_env["library"]
    _marked(library / "session" / "one.jpg", (30, 120, 30), (20, 20, 80, 80))
    _marked(library / "session" / "two.jpg", (120, 30, 30), (20, 20, 80, 80))
    login(client)
    library_id = _attested_library(client, library)
    ids = [i["id"] for i in client.get("/api/images", params={"library_id": library_id}).json()["items"]]
    for image_id in ids:
        client.post(f"/api/images/{image_id}/rating", json={"rating": "excellent"})
    item = client.post("/api/queue", json={"image_id": ids[0]}).json()
    assert item["collaborators"] == [] and item["collaborators_allowed"] is True
    bad = client.put(f"/api/queue/{item['id']}/collaborators", json={"usernames": ["not valid!"]})
    assert bad.status_code in {400, 403, 409, 422}
    ok = client.put(f"/api/queue/{item['id']}/collaborators", json={"usernames": ["@Model.One"]})
    assert ok.status_code == 200, ok.text
    assert ok.json()["collaborators"] == ["model.one"]
    # Same shoot -> pre-filled.
    other = client.post("/api/queue", json={"image_id": ids[1]}).json()
    assert other["collaborators"] == ["model.one"]
    assert client.post(f"/api/queue/{item['id']}/approve").status_code == 200
    caption_id = client.post(f"/api/images/{ids[0]}/captions", json={"confirmed": True}).json()[0]["id"]
    published = client.post(f"/api/queue/{item['id']}/publish", json={"caption_id": caption_id})
    assert published.status_code == 200, published.text
    assert client.app.state.instagram.calls[-1]["collaborators"] == ["model.one"]


def test_carousel_suggestions_same_shoot(client, app_env) -> None:
    library: Path = app_env["library"]
    for n, colour in enumerate([(30, 120, 30), (120, 30, 30), (30, 30, 120), (90, 90, 20)]):
        _marked(library / "shootA" / f"{n}.jpg", colour, (20, 20, 80 + n * 10, 80))
    login(client)
    library_id = _attested_library(client, library)
    ids = [i["id"] for i in client.get("/api/images", params={"library_id": library_id}).json()["items"]]
    for image_id in ids:
        client.post(f"/api/images/{image_id}/rating", json={"rating": "safe"})
    data = client.get("/api/carousel-suggestions").json()
    assert len(data["suggestions"]) == 1
    group = data["suggestions"][0]
    assert 3 <= len(group["images"]) <= 5
    created = client.post("/api/queue/carousel", json={"image_ids": [i["id"] for i in group["images"]]})
    assert created.status_code == 201, created.text
    assert client.get("/api/carousel-suggestions").json()["suggestions"] == []  # already queued


# --- 1-in-5 cap and ranking ----------------------------------------------------------------


def test_one_in_five_cap_window() -> None:
    assert SENSITIVE_ONE_IN == 5
    assert not violates_one_in_n([False] * 4, [False] * 4)
    assert violates_one_in_n([True, False, False, False], [False] * 4)  # 4 back
    assert violates_one_in_n([False] * 4, [False, False, False, True])  # 4 ahead
    assert not violates_one_in_n([True, False, False, False, False], [False] * 4)  # 5 back is OK
    assert not violates_one_in_n([False] * 4, [False, False, False, False, True])
    # Simulate a feed: a sensitive post is only placed when the window is clear -> <= 1 in any 5.
    feed: list[bool] = []
    for want in [True] * 30:
        feed.append(want and not violates_one_in_n(feed, []))
    assert all(sum(feed[i : i + 5]) <= 1 for i in range(len(feed) - 4))
    assert sum(feed) == 6


def _img(**kw):
    base = dict(
        gemini_description="", gemini_mood="", tags=[], enrich_hashtags_json=None, model_state="SAFE", assessments=[]
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _assessment(**kw):
    base = dict(skin_exposure_score=0.0, nudity_score=0.0, lingerie_score=0.0, face_count=0, assessed_at=None)
    base.update(kw)
    return SimpleNamespace(**base)


def test_content_classifier_uses_local_signals() -> None:
    still = classify_image(_img(gemini_description="A still life of bread and a vase on a table."))
    assert still.still_life and not still.sensitive and still.boost == 1.0
    face = classify_image(_img(assessments=[_assessment(face_count=1)]))
    assert face.portrait and face.boost > 0.5
    skin = classify_image(_img(assessments=[_assessment(skin_exposure_score=0.6, face_count=1)]))
    assert skin.sensitive and skin.boost == -1.0
    tattoo = classify_image(_img(gemini_description="Close detail of a tattoo on the shoulder."))
    assert tattoo.sensitive
    projected = classify_image(_img(gemini_description="Projected light pattern across a figure."))
    assert projected.sensitive
    blurred = classify_image(_img(model_state="DO_NOT_POST"))
    assert blurred.sensitive
    mono = classify_image(_img(gemini_description="Black and white portrait, soft light."))
    assert mono.mono and not mono.sensitive
    colour = classify_image(_img(gemini_description="Portrait in warm window light."))
    assert colour.boost > mono.boost


def test_rank_prefers_growth_mix_over_skin_body() -> None:
    def inp(image_id: str, boost: float) -> CandidateInput:
        return CandidateInput(image_id, 0.7, "excellent", 0.5, None, True, None, "SAFE", 0.5, 0.5, content_boost=boost)

    ranked = rank_candidates([inp("skin", -1.0), inp("still", 1.0), inp("plain", 0.0)], limit=3)
    assert [r.image_id for r in ranked] == ["still", "plain", "skin"]


# --- Reel schedule (own lane) --------------------------------------------------------------


def test_reel_lane_uses_own_slots_and_never_takes_feed_slots(monkeypatch) -> None:
    now = _local(2026, 5, 4, 8)  # Monday 08:00 local
    _patch(monkeypatch, busy=[])
    config = _config()
    assert config.reel_slot_enabled and config.reel_slots == ("15:00", "21:00") and config.reels_per_day == 2
    # Feed photos keep the weekday slots (the 11:00 photo slot is never reserved/converted).
    assert slots_mod.next_available_slot(None, config, now=now) == _local(2026, 5, 4, 9)
    assert slots_mod.next_available_slot(None, config, now=now, media_type="IMAGE") == _local(2026, 5, 4, 9)
    # Reels go to the Reel slots only.
    assert slots_mod.next_available_slot(None, config, now=now, media_type="REELS") == _local(2026, 5, 4, 15)
    reel_slots = slots_mod.best_slots(config, from_day=now, days=2, lane="reel")
    assert reel_slots == [_local(2026, 5, d, h) for d in (4, 5) for h in (15, 21)]
    # Disabled Reel schedule -> no Reel slots at all (feed unchanged).
    off = replace(config, reel_slot_enabled=False)
    assert slots_mod.next_available_slot(None, off, now=now, media_type="REELS") is None
    assert slots_mod.next_available_slot(None, off, now=now, media_type="IMAGE") == _local(2026, 5, 4, 9)
    assert not hasattr(slots_mod, "is_reel_slot")


def test_reel_lane_editable_slots(monkeypatch) -> None:
    now = _local(2026, 5, 4, 16)
    _patch(monkeypatch, busy=[])
    config = replace(_config(), reel_slots=("10:30", "18:45", "22:00"), reels_per_day=3)
    assert slots_mod.next_available_slot(None, config, now=now, media_type="REELS") == _local(2026, 5, 4, 18, 45)


def _feed_and_reel_items(client, app_env, count: int = 8) -> list:
    library: Path = app_env["library"]
    for n in range(count):
        _marked(library / "shoot" / f"{n}.jpg", (30 + n * 20, 120, 30), (20, 20, 80 + n * 5, 80))
    login(client)
    library_id = _attested_library(client, library)
    ids = [i["id"] for i in client.get("/api/images", params={"library_id": library_id}).json()["items"]]
    for image_id in ids:
        client.post(f"/api/images/{image_id}/rating", json={"rating": "safe"})
    return ids


def test_reels_do_not_count_toward_feed_cap_or_gap(client, app_env) -> None:
    from uuid import UUID

    from instamagic.db import session_factory
    from instamagic.models import PublishingQueueItem
    from instamagic.services.posting.schedule_config import config_view, update_config

    ids = _feed_and_reel_items(client, app_env, 4)
    session = session_factory()()
    try:
        update_config(
            session,
            weekday_slots={d: ["11:00", "19:00"] for d in DAYS},
            max_posts_per_day=2,
            min_gap_hours=6,
        )
        config = config_view(session)
        assert config.reel_slots == ("15:00", "21:00")
        now = _local(2026, 5, 4, 8)
        # Two Reels already on Monday at 15:00 and 21:00.
        for hour in (15, 21):
            session.add(
                PublishingQueueItem(
                    image_id=UUID(ids[0]), media_type="REELS", state="APPROVED", scheduled_at=_local(2026, 5, 4, hour)
                )
            )
        session.commit()
        # Feed still gets both Monday photo slots (Reels are not in the feed cap or 6 h gap).
        first = slots_mod.next_available_slot(session, config, now=now, media_type="IMAGE")
        assert first == _local(2026, 5, 4, 11)
        session.add(PublishingQueueItem(image_id=UUID(ids[1]), media_type="IMAGE", state="APPROVED", scheduled_at=first))
        session.commit()
        second = slots_mod.next_available_slot(session, config, now=now, media_type="IMAGE")
        assert second == _local(2026, 5, 4, 19)
        session.add(PublishingQueueItem(image_id=UUID(ids[2]), media_type="IMAGE", state="APPROVED", scheduled_at=second))
        session.commit()
        # Feed full Monday -> Tuesday 11:00; Reel lane full Monday -> Tuesday 15:00.
        assert slots_mod.next_available_slot(session, config, now=now, media_type="IMAGE") == _local(2026, 5, 5, 11)
        assert slots_mod.next_available_slot(session, config, now=now, media_type="REELS") == _local(2026, 5, 5, 15)
        # Reels per day = 1 -> still Tuesday (Monday has 2 already).
        update_config(session, reels_per_day=1)
        config = config_view(session)
        assert slots_mod.next_available_slot(session, config, now=now, media_type="REELS") == _local(2026, 5, 5, 15)
    finally:
        session.close()


def test_schedule_config_reel_fields_roundtrip(client, app_env) -> None:
    login(client)
    data = client.get("/api/posting-schedule/config").json()
    assert data["reel_slots"] == ["15:00", "21:00"] and data["reels_per_day"] == 2
    assert data["reel_share_to_feed"] is True and data["music_pick_mode"] == "rotate"
    ok = client.put(
        "/api/posting-schedule/config",
        json={"reel_slots": ["21:00", "14:30"], "reels_per_day": 3, "reel_share_to_feed": False, "music_pick_mode": "random"},
    )
    assert ok.status_code == 200, ok.text
    out = ok.json()
    assert out["reel_slots"] == ["14:30", "21:00"] and out["reels_per_day"] == 3
    assert out["reel_share_to_feed"] is False and out["music_pick_mode"] == "random"
    assert out["weekday_slots"] == data["weekday_slots"]  # feed slots untouched
    for bad in ({"reel_slots": ["25:00"]}, {"reels_per_day": 0}, {"music_pick_mode": "loud"}):
        assert client.put("/api/posting-schedule/config", json=bad).status_code in {400, 403, 409, 422}
