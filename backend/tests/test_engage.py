from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from instamagic.db import session_factory
from instamagic.errors import NotConfigured
from instamagic.models import EngageRun, EngageTarget, RelatedAccount
from instamagic.services.engage import service as engage
from instamagic.services.gemini.growth import (
    EngageCommentBatch,
    EngageTargetText,
    FakeGrowthDraftProvider,
)
from instamagic.services.instagram.client import FakeInstagramClient
from sqlalchemy import select

from conftest import login

NOW = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)


def _ts(hours_ago: float) -> str:
    return (NOW - timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%S+0000")


class EngageFakeClient(FakeInstagramClient):
    def __init__(self, *, deny_hashtags: bool = False) -> None:
        super().__init__()
        self.deny_hashtags = deny_hashtags
        self.read_calls: list[str] = []

    def search_hashtag(self, hashtag: str) -> str:
        self.read_calls.append(f"search:{hashtag}")
        if self.deny_hashtags:
            raise NotConfigured("Instagram search hashtag failed: requires Instagram Public Content Access (#10)")
        return f"hid-{hashtag}"

    def _media(self, hashtag_id: str, edge: str) -> list[dict]:
        rows = []
        for i in range(8):
            rows.append(
                {
                    "id": f"{hashtag_id}-{edge}-{i}",
                    "caption": f"Morning fog over the river #{hashtag_id} with @friend_{i}",
                    "like_count": 20 + i,
                    "comments_count": 2,
                    "timestamp": _ts(3 + i),
                    "media_type": "IMAGE",
                    "media_url": f"https://cdn.example/{hashtag_id}/{edge}/{i}.jpg",
                    "permalink": f"https://www.instagram.com/p/{hashtag_id}-{edge}-{i}/",
                }
            )
        rows.append({"id": "reel", "media_type": "VIDEO", "permalink": "https://www.instagram.com/reel/x/"})
        rows.append(
            {"id": "spam", "media_type": "IMAGE", "caption": "follow for follow!!", "permalink": "https://www.instagram.com/p/spam/"}
        )
        return rows

    def fetch_hashtag_top_media(self, hashtag_id: str) -> list[dict]:
        self.read_calls.append(f"top:{hashtag_id}")
        return self._media(hashtag_id, "top")

    def fetch_hashtag_recent_media(self, hashtag_id: str) -> list[dict]:
        self.read_calls.append(f"recent:{hashtag_id}")
        return self._media(hashtag_id, "recent")

    def fetch_business_discovery(self, username: str) -> dict:
        self.read_calls.append(f"bd:{username}")
        return {
            "username": username,
            "followers_count": 1200,
            "media": {
                "data": [
                    {
                        "id": f"{username}-1",
                        "caption": "Late light on the old pier.",
                        "like_count": 40,
                        "timestamp": _ts(10),
                        "media_type": "IMAGE",
                        "media_url": f"https://cdn.example/{username}.jpg",
                        "permalink": f"https://www.instagram.com/p/{username}-1/",
                    }
                ]
            },
        }


@pytest.fixture(autouse=True)
def _no_pause(monkeypatch):
    monkeypatch.setattr(engage, "GRAPH_PAUSE_SECONDS", 0)


def _seed_accounts(session, handles):
    for handle in handles:
        session.add(RelatedAccount(handle=handle, why_relevant="moody street"))
    session.commit()


def test_run_builds_daily_list_without_sending_anything(app_env) -> None:
    del app_env
    session = session_factory()()
    _seed_accounts(session, ["alice.photo", "bob_frames"])
    client = EngageFakeClient()
    run = engage.run_engage(session, client, FakeGrowthDraftProvider(), account_voice="quiet", now=NOW)
    assert run.status == "ok", run.error
    targets = session.scalars(select(EngageTarget)).all()
    assert len(targets) == 18
    assert {t.source for t in targets} == {"account", "hashtag"}
    assert all(t.suggestion for t in targets)
    assert all(t.media_type in {"IMAGE", "CAROUSEL_ALBUM"} for t in targets)
    assert not any("spam" in t.permalink for t in targets)
    assert len({t.permalink for t in targets}) == len(targets)
    acct = [t for t in targets if t.source == "account"]
    assert {t.username for t in acct} == {"alice.photo", "bob_frames"}
    assert "Similar account" in acct[0].reason
    # Never publishes, likes, follows, or replies: only read calls.
    assert client.calls == [] and client.carousel_calls == [] and client.story_calls == []
    assert all(c.split(":")[0] in {"search", "top", "recent", "bd"} for c in client.read_calls)
    detail = json.loads(run.detail_json)
    assert "friend_0" in detail["suggested_accounts"]
    assert len(detail["hashtags"]) == 3
    session.close()


def test_accounts_not_repeated_within_cooldown(app_env) -> None:
    del app_env
    session = session_factory()()
    _seed_accounts(session, ["alice.photo"])
    engage.run_engage(session, EngageFakeClient(), FakeGrowthDraftProvider(), account_voice="q", now=NOW)
    client = EngageFakeClient()
    engage.run_engage(session, client, FakeGrowthDraftProvider(), account_voice="q", now=NOW + timedelta(days=1))
    assert "bd:alice.photo" not in client.read_calls
    rows = session.scalars(select(EngageTarget).where(EngageTarget.username == "alice.photo")).all()
    assert len(rows) == 1
    session.close()


def test_hashtag_permission_missing_is_graceful(app_env) -> None:
    del app_env
    session = session_factory()()
    run = engage.run_engage(
        session, EngageFakeClient(deny_hashtags=True), FakeGrowthDraftProvider(), account_voice="q", now=NOW
    )
    assert run.status == "blocked"
    assert run.error_code == "hashtag_permission"
    assert "Reconnect Instagram" in (run.error or "")
    session.close()


def test_budget_reserve_limits_new_hashtags(app_env) -> None:
    del app_env
    session = session_factory()()
    from instamagic.models import HashtagResearchEvent

    for i in range(30 - engage.BUDGET_RESERVE):
        session.add(HashtagResearchEvent(hashtag=f"used{i}", action="search"))
    session.commit()
    niche = [{"tag": "newtag", "why": "x"}, {"tag": "used1", "why": "y"}]
    picked = engage.pick_run_hashtags(session, niche, 3, 0)
    assert [p["tag"] for p in picked] == ["used1"]
    session.close()


def test_niche_hashtags_skip_generic(app_env) -> None:
    del app_env
    session = session_factory()()
    row = engage.update_settings(session, seed_hashtags=["#Photography", "#ParisStreet"])
    tags = [t["tag"] for t in engage.niche_hashtags(session, row)]
    assert tags[0] == "parisstreet"
    assert "photography" not in tags
    session.close()


def test_engage_gemini_payload_text_only(monkeypatch) -> None:
    from instamagic.services.gemini.google import GoogleGeminiProvider

    captured = {}

    def fake_post(self, body):
        captured["body"] = body
        return json.dumps(
            {"comments": [{"id": "0", "text": "The fog layering here is lovely."}, {"id": "1", "text": "Follow me back!"}]}
        )

    monkeypatch.setattr(GoogleGeminiProvider, "_post", fake_post)
    provider = GoogleGeminiProvider("key", "gemini-2.5-flash")
    batch = provider.draft_engage_comments(
        [EngageTargetText(id="0", caption="fog"), EngageTargetText(id="1", caption="pier")], account_voice="q"
    )
    blob = json.dumps(captured["body"]).lower()
    assert "inline_data" not in blob and "file_data" not in blob
    assert isinstance(batch, EngageCommentBatch)
    assert [c.id for c in batch.comments] == ["0"]  # bait draft dropped


def test_sanitize_caption_avoids_forbidden_markers() -> None:
    text = engage._sanitize_caption("See my Originals at https://x.y/photos/1 and /photos/ file://z")
    assert "originals" not in text.lower() and "/photos/" not in text and "file://" not in text


def test_engage_api_flow(client) -> None:
    assert client.get("/api/engage").status_code == 401
    login(client)
    client.app.state.instagram = EngageFakeClient()
    data = client.get("/api/engage").json()
    assert data["targets"] == [] and data["permission"]["ok"] is True
    response = client.post("/api/engage/refresh")
    assert response.status_code == 202
    data = client.get("/api/engage").json()
    assert data["last_run"]["status"] == "ok"
    assert len(data["targets"]) > 0
    target = data["targets"][0]
    assert target["permalink"].startswith("https://www.instagram.com/")
    marked = client.patch(f"/api/engage/targets/{target['id']}", json={"status": "done"})
    assert marked.json()["status"] == "done"
    data = client.get("/api/engage").json()
    assert data["days"][0]["done"] == 1
    bad = client.patch(f"/api/engage/targets/{target['id']}", json={"status": "liked"})
    assert bad.status_code == 422
    saved = client.patch("/api/engage/settings", json={"daily_target": 15, "seed_hashtags": ["streetportrait"]})
    assert saved.json()["daily_target"] == 15 and saved.json()["seed_hashtags"] == ["streetportrait"]


def test_engage_due_respects_hour_and_once_per_day(app_env) -> None:
    del app_env
    from instamagic.workers.social_loop import engage_due

    session = session_factory()()
    early = datetime(2026, 10, 24, 9, 0, tzinfo=UTC)  # 06:00 ART
    later = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)  # 09:00 ART
    assert engage_due(session, early) is False
    assert engage_due(session, later) is True
    session.add(EngageRun(run_date="2026-10-24", trigger="schedule", status="ok", started_at=later))
    session.commit()
    assert engage_due(session, later) is False
    session.close()
