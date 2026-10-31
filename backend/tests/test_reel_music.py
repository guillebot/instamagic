"""Reel schedule: music library, per-Reel music, silent fallback, share_to_feed, auto proposals."""

from __future__ import annotations

import json
import random
import shutil
import subprocess
from pathlib import Path

import pytest
from tests.conftest import login
from tests.test_stories_reels import _approved_images

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


@pytest.fixture
def music_env(app_env, monkeypatch):
    from instamagic.config import get_settings

    root = app_env["root"] / "music"
    monkeypatch.setenv("MUSIC_ROOT", str(root))
    get_settings.cache_clear()
    return root


def _tone(path: Path, seconds: float = 6.0, freq: int = 440) -> bytes:
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={seconds}", str(path)],
        check=True,
    )
    return path.read_bytes()


def _upload(client, name: str, data: bytes):
    return client.post("/api/music", params={"filename": name}, content=data)


@needs_ffmpeg
def test_music_upload_list_preview_toggle_delete(client, app_env, music_env) -> None:
    login(client)
    empty = client.get("/api/music").json()
    assert empty["tracks"] == [] and empty["enabled_count"] == 0 and "royalty" in empty["license_note"].lower()
    data = _tone(app_env["root"] / "a.wav")
    up = _upload(client, "My Track (CC0).wav", data)
    assert up.status_code == 201, up.text
    track = up.json()
    assert track["enabled"] is True and 5.0 <= track["duration_s"] <= 7.0
    assert len(list(music_env.iterdir())) == 1
    # Same file twice -> refused as a duplicate.
    assert _upload(client, "again.wav", data).status_code in {400, 403, 409, 422}
    audio = client.get(track["preview_url"])
    assert audio.status_code == 200 and audio.content == data
    off = client.patch(f"/api/music/{track['id']}", json={"enabled": False})
    assert off.status_code == 200 and off.json()["enabled"] is False
    assert client.get("/api/music").json()["enabled_count"] == 0
    assert client.delete(f"/api/music/{track['id']}").status_code == 200
    assert client.get("/api/music").json()["tracks"] == [] and list(music_env.iterdir()) == []


@needs_ffmpeg
def test_music_upload_rejects_bad_files(client, app_env, music_env) -> None:
    login(client)
    assert _upload(client, "notes.txt", b"hello").status_code in {400, 403, 409, 422}
    assert _upload(client, "fake.mp3", b"not audio at all" * 100).status_code in {400, 403, 409, 422}
    short = _tone(app_env["root"] / "short.wav", seconds=1.0)
    assert _upload(client, "short.wav", short).status_code in {400, 403, 409, 422}
    assert _upload(client, "../../escape.wav", _tone(app_env["root"] / "e.wav")).status_code == 201
    assert all(p.parent == music_env for p in music_env.iterdir())  # stored by hash, never by name
    assert list(music_env.glob("*.part")) == []


def test_pick_track_rotation_and_random(app_env, music_env) -> None:
    from datetime import UTC, datetime, timedelta

    from instamagic.config import get_settings
    from instamagic.db import session_factory
    from instamagic.models import MusicTrack
    from instamagic.services.posting.music import pick_track

    settings = get_settings()
    music_env.mkdir(parents=True, exist_ok=True)
    session = session_factory()()
    try:
        now = datetime.now(UTC)
        rows = []
        for n, used in enumerate([now, None, now - timedelta(days=1)]):
            (music_env / f"t{n}.wav").write_bytes(b"x")
            row = MusicTrack(title=f"t{n}", relative_path=f"t{n}.wav", sha256=f"{n:064d}", size_bytes=1, duration_s=30, last_used_at=used)
            session.add(row)
            rows.append(row)
        session.commit()
        assert pick_track(session, settings, "rotate").title == "t1"  # never used first
        rows[1].last_used_at = now + timedelta(minutes=1)
        session.commit()
        assert pick_track(session, settings, "rotate").title == "t2"  # then least recently used
        rows[2].enabled = False
        session.commit()
        picks = {pick_track(session, settings, "random", rng=random.Random(seed)).title for seed in range(30)}
        assert picks == {"t0", "t1"}  # disabled track never picked
    finally:
        session.close()


@needs_ffmpeg
def test_reel_render_bakes_music_and_silent_fallback(client, app_env, music_env) -> None:
    ids = _approved_images(client, app_env["library"], 3)
    reel = client.post("/api/queue/reel", json={"image_ids": ids}).json()
    # Empty library -> silent render with a warning badge.
    assert client.post(f"/api/queue/{reel['id']}/reel-render").status_code == 202
    row = next(i for i in client.get("/api/queue").json() if i["id"] == reel["id"])
    assert row["reel_status"] == "ready", row.get("reel_error")
    assert row["reel_music"]["silent"] is True and row["reel_music"]["warning"]
    # Upload a track -> re-render picks it automatically and bakes it into the MP4.
    track = _upload(client, "Calm loop.wav", _tone(app_env["root"] / "m.wav", seconds=4.0)).json()
    assert client.post(f"/api/queue/{reel['id']}/reel-render").status_code == 202
    row = next(i for i in client.get("/api/queue").json() if i["id"] == reel["id"])
    assert row["reel_status"] == "ready", row.get("reel_error")
    assert row["reel_music"]["silent"] is False and row["reel_music"]["track"]["id"] == track["id"]
    video = client.get(row["reel_video_url"])
    path = app_env["root"] / "out.mp4"
    path.write_bytes(video.content)
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,duration", "-of", "json", str(path)],
        capture_output=True,
        text=True,
        check=True,
    )
    streams = json.loads(probe.stdout)["streams"]
    audio = [s for s in streams if s["codec_type"] == "audio"]
    video_s = [s for s in streams if s["codec_type"] == "video"]
    assert audio and video_s
    assert abs(float(audio[0]["duration"]) - float(video_s[0]["duration"])) < 0.5  # looped/trimmed to video
    # Choosing "silent" marks the rendered Reel stale until it is rendered again.
    chosen = client.put(f"/api/queue/{reel['id']}/music", json={"choice": "none"})
    assert chosen.status_code == 200, chosen.text
    assert chosen.json()["reel_status"] == "stale" and chosen.json()["music_choice"] == "none"
    assert client.post(f"/api/queue/{reel['id']}/approve").status_code in {400, 403, 409, 422}
    assert client.put(f"/api/queue/{reel['id']}/music", json={"choice": "nope"}).status_code in {400, 404, 422}


def test_music_choice_matches() -> None:
    from types import SimpleNamespace

    from instamagic.services.posting.music import music_choice_matches

    assert music_choice_matches(SimpleNamespace(music_choice=None), None)
    assert music_choice_matches(SimpleNamespace(music_choice=None), {"id": "a"})
    assert music_choice_matches(SimpleNamespace(music_choice="none"), None)
    assert not music_choice_matches(SimpleNamespace(music_choice="none"), {"id": "a"})
    assert music_choice_matches(SimpleNamespace(music_choice="a"), {"id": "a"})
    assert not music_choice_matches(SimpleNamespace(music_choice="a"), {"id": "b"})
    assert not music_choice_matches(SimpleNamespace(music_choice="a"), None)


@needs_ffmpeg
def test_publish_reel_passes_share_to_feed(client, app_env, music_env) -> None:
    [image_id] = _approved_images(client, app_env["library"], 1)
    reel = client.post("/api/queue/reel", json={"image_ids": [image_id]}).json()
    client.post(f"/api/queue/{reel['id']}/reel-render")
    assert client.put("/api/posting-schedule/config", json={"reel_share_to_feed": False}).status_code == 200
    captions = client.post(f"/api/images/{image_id}/captions", json={"confirmed": True}).json()
    assert client.post(f"/api/queue/{reel['id']}/approve").status_code == 200
    pub = client.post(f"/api/queue/{reel['id']}/publish", json={"caption_id": captions[0]["id"]})
    assert pub.status_code == 200, pub.text
    assert client.app.state.instagram.reel_calls[-1]["share_to_feed"] is False


def test_proposer_builds_reel_candidates_needing_approval(client, app_env) -> None:
    from instamagic.db import session_factory
    from instamagic.services.posting.reel_candidates import propose_reel_candidates

    ids = _approved_images(client, app_env["library"], 7)
    # A feed photo already queued may still appear in a Reel (different format).
    assert client.post("/api/queue", json={"image_id": ids[0]}).status_code in {200, 201}
    created = client.post("/api/reel-candidates/propose")
    assert created.status_code == 200, created.text
    data = created.json()
    assert data["created"] == 1
    reel = data["items"][0]
    assert reel["media_type"] == "REELS" and reel["state"] == "CANDIDATE" and reel["reel_auto"] is True
    assert 3 <= len(reel["slides"]) <= 6
    assert reel["schedule_mode"] in {None, ""} and reel["scheduled_at"] is None  # nothing scheduled/approved
    used = {slide["image_id"] for slide in reel["slides"]}
    session = session_factory()()
    try:
        more = propose_reel_candidates(session, render=False)
        for item in more:
            assert item.state == "CANDIDATE"
            assert not used & {slide.image_id.__str__() for slide in item.slides}  # never reuse a Reel photo
    finally:
        session.close()
    # Nothing was approved or published by the proposer.
    queue = client.get("/api/queue").json()
    assert all(row["state"] != "APPROVED" for row in queue if row["media_type"] == "REELS")
    assert client.app.state.instagram.reel_calls == []


def test_proposer_respects_disabled_schedule_and_stock(client, app_env) -> None:
    from instamagic.db import session_factory
    from instamagic.services.posting.reel_candidates import propose_reel_candidates

    _approved_images(client, app_env["library"], 12)
    assert client.put("/api/posting-schedule/config", json={"reel_slot_enabled": False}).status_code == 200
    session = session_factory()()
    try:
        assert propose_reel_candidates(session, render=False) == []
    finally:
        session.close()
    assert client.put("/api/posting-schedule/config", json={"reel_slot_enabled": True, "reels_per_day": 1}).status_code == 200
    session = session_factory()()
    try:
        total = 0
        for _ in range(5):
            total += len(propose_reel_candidates(session, render=False, max_new=5))
        assert total == 2  # stock = reels_per_day * 2, then it stops proposing
    finally:
        session.close()
