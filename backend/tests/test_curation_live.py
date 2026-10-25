"""Live "Ready to post" pool: rated safe photos appear in Curate without a Gemini curation run."""

from tests.test_curation import _seed_rated_library


def _live(client) -> dict:
    response = client.get("/api/curation/live")
    assert response.status_code == 200, response.text
    return response.json()


def test_live_pool_lists_rated_safe_without_run(client, app_env) -> None:
    _library_id, clean_id, blocked_id = _seed_rated_library(client, app_env["library"])

    live = _live(client)
    assert live["live"] is True
    pick_ids = {pick["image"]["id"] for pick in live["picks"]}
    assert clean_id in pick_ids
    # Flagged photo without a verified blur stays out and is explained under Skipped.
    assert blocked_id not in pick_ids
    skipped = {row["image_id"]: row for row in (live.get("skipped") or {}).get("items", [])}
    assert blocked_id in skipped
    assert clean_id not in skipped

    # The live batch never shows up in the manual run history.
    assert all(row["id"] != live["id"] for row in client.get("/api/curation").json())

    # Re-sync is idempotent (same batch, no duplicate picks).
    again = _live(client)
    assert again["id"] == live["id"]
    assert len(again["picks"]) == len(live["picks"])


def test_live_pool_queue_then_manual_caption(client, app_env) -> None:
    _seed_rated_library(client, app_env["library"])
    live = _live(client)
    pick = live["picks"][0]
    image_id = pick["image"]["id"]

    manual = client.post(f"/api/images/{image_id}/captions/manual", json={"text": "Late light, quiet street."})
    assert manual.status_code == 201, manual.text
    assert manual.json()["text"] == "Late light, quiet street."
    assert client.post(f"/api/images/{image_id}/captions/manual", json={"text": "   "}).status_code in {400, 422}

    queued = client.post("/api/curation/queue", json={"pick_ids": [pick["id"]]})
    assert queued.status_code == 200, queued.text
    assert queued.json()[0]["state"] == "CANDIDATE"  # never auto-approved

    after = _live(client)
    row = next(p for p in after["picks"] if p["image"]["id"] == image_id)
    assert row["status"] == "queued"

    # Rating change removes a pending pick from the pool.
    other = next((p for p in after["picks"] if p["status"] == "suggested"), None)
    if other is not None:
        client.post(f"/api/images/{other['image']['id']}/rating", json={"rating": "review"})
        final = _live(client)
        assert other["image"]["id"] not in {
            p["image"]["id"] for p in final["picks"] if p["status"] in {"suggested", "accepted"}
        }


def test_live_requires_auth(client) -> None:
    assert client.get("/api/curation/live").status_code == 401


def test_verified_blur_then_safe_shows_blurred_and_leaves_on_blur_edit(client, app_env) -> None:
    from tests.test_blur_rating_gate import _cover, _image_id

    image_id = _image_id(client, app_env["library"])
    _cover(client, image_id)
    assert client.post(f"/api/images/{image_id}/blurs/approve").status_code == 200
    assert client.post(f"/api/images/{image_id}/rating", json={"rating": "safe"}).status_code == 200

    live = _live(client)
    pick = next(p for p in live["picks"] if p["image"]["id"] == image_id)
    assert "blurred" in (pick["image"]["thumbnail_url"] or "")
    assert any("Blurred (verified)" in reason for reason in pick["reasons"])

    # Editing the blur resets the unlock: the pending pick leaves the pool (original never used).
    assert client.post(f"/api/images/{image_id}/blurs/strength", json={"strength": "subtle"}).status_code == 200
    after = _live(client)
    assert image_id not in {p["image"]["id"] for p in after["picks"]}
