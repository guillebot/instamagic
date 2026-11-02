"""Royalty-free music import: licence policy (CC0 / CC BY only), CC BY caption credit, import, refill."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import httpx
import pytest
from tests.conftest import login
from tests.test_reel_music import music_env  # noqa: F401  (fixture)
from tests.test_stories_reels import _approved_images

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")

ID_BY = "11111111-1111-1111-1111-111111111111"
ID_CC0 = "22222222-2222-2222-2222-222222222222"
ID_NC = "33333333-3333-3333-3333-333333333333"
ID_ND = "44444444-4444-4444-4444-444444444444"
ID_SHORT = "55555555-5555-5555-5555-555555555555"
ID_SA = "66666666-6666-6666-6666-666666666666"


def _record(rid, title, artist, lic, url, *, duration_ms=95_000, tags=("instrumental",), version="4.0"):
    return {
        "id": rid,
        "title": title,
        "creator": artist,
        "license": lic,
        "license_version": version,
        "license_url": url,
        "url": f"https://files.example/{rid}.mp3",
        "foreign_landing_url": f"https://www.jamendo.com/track/{rid[:4]}",
        "attribution": f'"{title}" by {artist} is licensed under {lic}.',
        "duration": duration_ms,
        "filetype": "mp3",
        "mature": False,
        "genres": ["ambient"],
        "tags": [{"name": t} for t in tags],
        "provider": "jamendo",
    }


RECORDS = {
    ID_BY: _record(ID_BY, "Night Glass", "Ada Lune", "by", "https://creativecommons.org/licenses/by/4.0/"),
    ID_CC0: _record(ID_CC0, "Still Water", "Kai", "cc0", "https://creativecommons.org/publicdomain/zero/1.0/", version="1.0"),
    ID_NC: _record(ID_NC, "No Money", "X", "by-nc", "https://creativecommons.org/licenses/by-nc/4.0/"),
    ID_ND: _record(ID_ND, "Untouched", "Y", "by-nd", "https://creativecommons.org/licenses/by-nd/4.0/"),
    ID_SA: _record(ID_SA, "Share", "Z", "by-sa", "https://creativecommons.org/licenses/by-sa/4.0/"),
    ID_SHORT: _record(ID_SHORT, "Blip", "W", "cc0", "https://creativecommons.org/publicdomain/zero/1.0/", duration_ms=8_000),
}


def _mp3(path: Path, seconds: float = 32.0, bitrate: str = "192k", freq: int = 330) -> bytes:
    subprocess.run(
        [
            "ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={seconds}",
            "-ac", "2", "-b:a", bitrate, str(path),
        ],
        check=True,
    )
    return path.read_bytes()


def _client(tmp: Path, *, bitrate: str = "192k"):
    from instamagic.services.posting.music_sources import OpenverseClient

    calls: list[httpx.Request] = []
    files: dict[str, bytes] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        path = request.url.path
        if request.url.host == "api.openverse.org" and path == "/v1/audio/":
            return httpx.Response(200, json={"results": list(RECORDS.values())})
        if request.url.host == "api.openverse.org" and path.startswith("/v1/audio/"):
            rid = path.rstrip("/").rsplit("/", 1)[-1]
            return httpx.Response(200, json=RECORDS[rid]) if rid in RECORDS else httpx.Response(404)
        if request.url.host == "files.example":
            rid = Path(path).stem
            if rid not in files:
                files[rid] = _mp3(tmp / f"{rid}.mp3", bitrate=bitrate, freq=200 + len(files) * 50)
            return httpx.Response(200, content=files[rid], headers={"content-type": "audio/mpeg"})
        return httpx.Response(404)

    return OpenverseClient(httpx.Client(transport=httpx.MockTransport(handler))), calls


def test_license_filter_rejects_nc_nd_sa_and_unknown() -> None:
    from instamagic.services.posting.music_sources import _parse, license_allowed

    assert license_allowed("cc0", "https://creativecommons.org/publicdomain/zero/1.0/")
    assert license_allowed("by", "https://creativecommons.org/licenses/by/4.0/")
    assert license_allowed("BY", "https://creativecommons.org/licenses/by/3.0/")
    for bad in ("by-nc", "by-nd", "by-nc-nd", "by-nc-sa", "by-sa", "nc", "pdm", "sampling+", "", None):
        assert not license_allowed(bad), bad
    # Code and deed must agree (never guess from one of them).
    assert not license_allowed("by", "https://creativecommons.org/licenses/by-nc/4.0/")
    assert not license_allowed("by", "https://example.com/licenses/by/4.0/")
    assert not license_allowed("cc0", "https://creativecommons.org/licenses/by/4.0/")
    assert _parse(RECORDS[ID_NC]) is None and _parse(RECORDS[ID_ND]) is None and _parse(RECORDS[ID_SA]) is None
    assert _parse({**RECORDS[ID_BY], "mature": True}) is None
    assert _parse(RECORDS[ID_BY]).license == "by"


def test_credit_line_and_append_credit() -> None:
    from instamagic.services.posting.music_sources import append_credit, credit_line

    assert credit_line("Still Water", "Kai", "cc0", "1.0") is None
    credit = credit_line("Night #Glass", "@Ada\nLune", "by", "4.0")
    assert credit == "🎵 Night Glass — Ada Lune (CC BY 4.0)"
    caption = "Fog over the river.\n\n#fineartphotography #blackandwhite"
    out = append_credit(caption, credit)
    assert out == f"Fog over the river.\n\n{credit}\n\n#fineartphotography #blackandwhite"
    assert append_credit(out, credit) == out  # idempotent
    assert out.count("#") == 2  # the credit adds no hashtags
    long = append_credit("x" * 2190 + "\n\n#portrait", credit)
    assert len(long) <= 2200 and credit in long and long.endswith("#portrait")
    assert append_credit(caption, None) == caption


@needs_ffmpeg
def test_search_filters_and_import_verifies_licence(app_env, music_env) -> None:  # noqa: F811
    from instamagic.config import get_settings
    from instamagic.db import session_factory
    from instamagic.errors import NotAllowed
    from instamagic.services.posting.music_sources import import_track, search

    client, calls = _client(app_env["root"])
    session = session_factory()()
    settings = get_settings()
    try:
        results = search(session, "ambient", client=client)
        assert {r["id"] for r in results} == {ID_BY, ID_CC0}  # NC/ND/SA dropped, 8 s track dropped
        search_call = next(c for c in calls if c.url.path == "/v1/audio/")
        assert search_call.url.params["license"] == "cc0,by"
        assert "license_type" not in search_call.url.params and "mature" not in search_call.url.params
        assert search_call.url.params["category"] == "music"
        track = import_track(session, settings, "openverse", ID_BY, client=client)
        assert track.license == "by" and track.license_version == "4.0"
        assert track.license_url == "https://creativecommons.org/licenses/by/4.0/"
        assert track.artist == "Ada Lune" and track.title == "Night Glass" and track.enabled
        assert track.source_url.startswith("https://www.jamendo.com/") and track.attribution
        assert track.downloaded_at is not None and track.duration_s >= 30 and track.bit_rate >= 128_000
        for bad in (ID_NC, ID_ND, ID_SA):
            with pytest.raises(NotAllowed):
                import_track(session, settings, "openverse", bad, client=client)
        with pytest.raises(NotAllowed):  # same record again
            import_track(session, settings, "openverse", ID_BY, client=client)
        assert {r["id"]: r["imported"] for r in search(session, "ambient", client=client)}[ID_BY] is True
    finally:
        session.close()


@needs_ffmpeg
def test_import_rejects_low_bitrate(app_env, music_env) -> None:  # noqa: F811
    from instamagic.config import get_settings
    from instamagic.db import session_factory
    from instamagic.errors import NotAllowed
    from instamagic.services.posting.music_sources import import_track

    client, _ = _client(app_env["root"], bitrate="64k")
    session = session_factory()()
    try:
        with pytest.raises(NotAllowed):
            import_track(session, get_settings(), "openverse", ID_CC0, client=client)
        assert list(music_env.glob("*.mp3")) == []
    finally:
        session.close()


@needs_ffmpeg
def test_cc_by_reel_caption_gets_credit(client, app_env, music_env) -> None:  # noqa: F811
    from instamagic.config import get_settings
    from instamagic.db import session_factory
    from instamagic.services.posting.music_sources import import_track
    from instamagic.services.posting.reel_candidates import silent_auto_candidates

    [image_id] = _approved_images(client, app_env["library"], 1)
    reel = client.post("/api/queue/reel", json={"image_ids": [image_id]}).json()
    assert client.post(f"/api/queue/{reel['id']}/reel-render").status_code == 202
    session = session_factory()()
    try:
        assert [str(i.id) for i in silent_auto_candidates(session)] == [reel["id"]]
        ov, _ = _client(app_env["root"])
        import_track(session, get_settings(), "openverse", ID_BY, client=ov)
    finally:
        session.close()
    assert client.post(f"/api/queue/{reel['id']}/reel-render").status_code == 202
    row = next(i for i in client.get("/api/queue").json() if i["id"] == reel["id"])
    assert row["reel_music"]["credit"] == "🎵 Night Glass — Ada Lune (CC BY 4.0)"
    session = session_factory()()
    try:
        assert silent_auto_candidates(session) == []
    finally:
        session.close()
    captions = client.post(f"/api/images/{image_id}/captions", json={"confirmed": True}).json()
    assert client.post(f"/api/queue/{reel['id']}/approve").status_code == 200
    pub = client.post(f"/api/queue/{reel['id']}/publish", json={"caption_id": captions[0]["id"]})
    assert pub.status_code == 200, pub.text
    sent = client.app.state.instagram.reel_calls[-1]["caption"]
    assert "🎵 Night Glass — Ada Lune (CC BY 4.0)" in sent and len(sent) <= 2200
    tags = sent.rsplit("\n\n", 1)[-1]
    assert "🎵" not in tags


@needs_ffmpeg
def test_music_api_lists_licence_and_autorefill(client, app_env, music_env) -> None:  # noqa: F811
    from datetime import UTC, datetime, timedelta

    from instamagic.config import get_settings
    from instamagic.db import session_factory
    from instamagic.services.posting.music_sources import run_autorefill

    login(client)
    view = client.get("/api/music/autorefill").json()
    assert view["enabled"] is False and view["queries"]
    session = session_factory()()
    settings = get_settings()
    try:
        ov, _ = _client(app_env["root"])
        assert run_autorefill(session, settings, client=ov) == []  # default off
        saved = client.put(
            "/api/music/autorefill", json={"enabled": True, "target": 2, "queries": ["ambient", "piano"]}
        )
        assert saved.status_code == 200 and saved.json()["target"] == 2
        session.expire_all()
        now = datetime.now(UTC)
        made = run_autorefill(session, settings, now=now, client=ov)
        assert {t.license for t in made} <= {"cc0", "by"} and 1 <= len(made) <= 2
        assert run_autorefill(session, settings, now=now + timedelta(days=1), client=ov) == []  # weekly
    finally:
        session.close()
    tracks = client.get("/api/music").json()["tracks"]
    assert tracks and all(t["license_label"] in {"CC BY 4.0", "CC0 1.0"} for t in tracks)
    assert all(t["source"] == "openverse" and t["license_url"] for t in tracks)


def test_detail_falls_back_to_recent_search_record_only() -> None:
    from instamagic.services.posting import music_sources as ms

    ms._RECENT.clear()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/audio/":
            return httpx.Response(200, json={"results": [RECORDS[ID_CC0], RECORDS[ID_NC]]})
        return httpx.Response(500)

    client = ms.OpenverseClient(httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(httpx.HTTPStatusError):
        client.detail(ID_CC0)  # nothing cached yet -> error, never a guess
    client.search("ambient")
    assert client.detail(ID_CC0).license == "cc0"
    with pytest.raises(httpx.HTTPStatusError):
        client.detail(ID_NC)  # NC records are never cached
    assert ID_NC not in ms._RECENT


def test_search_retries_then_friendly_error_and_rechecks_response(monkeypatch) -> None:
    import httpx
    import pytest
    from instamagic.errors import NotAllowed
    from instamagic.services.posting import music_sources as ms

    monkeypatch.setattr(ms, "_sleep", lambda s: None)
    seen = []

    def slow(request):
        seen.append(request)
        if len(seen) == 1:
            raise httpx.ReadTimeout("slow", request=request)
        return httpx.Response(504, request=request)

    client = ms.OpenverseClient(httpx.Client(transport=httpx.MockTransport(slow)))
    with pytest.raises(NotAllowed) as err:
        client.search("ambient", attempts=3)
    assert len(seen) == 3 and "Openverse is slow" in str(err.value)

    rows = [
        {**RECORDS[ID_BY]},
        {**RECORDS[ID_BY], "id": "aaaaaaaa-0000-0000-0000-000000000001", "mature": True},
        {**RECORDS[ID_BY], "id": "aaaaaaaa-0000-0000-0000-000000000002", "license": "by-nc"},
    ]
    calls = []

    def flaky(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(504, request=request)
        return httpx.Response(200, json={"results": rows}, request=request)

    client = ms.OpenverseClient(httpx.Client(transport=httpx.MockTransport(flaky)))
    assert [c.source_id for c in client.search("ambient")] == [ID_BY]
    assert len(calls) == 2
