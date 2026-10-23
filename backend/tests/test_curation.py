"""Gemini curation drafts + human send-to-queue (fake provider)."""

from pathlib import Path

from PIL import Image, ImageDraw
from tests.conftest import login


def _marked(path: Path, color: tuple[int, int, int], box: tuple[int, int, int, int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (400, 600), color)
    ImageDraw.Draw(image).rectangle(box, fill=(230, 230, 230))
    image.save(path, format="JPEG", quality=90)


def _seed_rated_library(client, library: Path) -> tuple[str, str, str]:
    session_dir = library / "session-001"
    _marked(session_dir / "a-clean.jpg", (30, 30, 30), (20, 20, 80, 80))
    _marked(session_dir / "unsafe-portrait.jpg", (90, 40, 40), (200, 40, 280, 120))
    _marked(session_dir / "blocked-frame.jpg", (40, 40, 90), (40, 300, 180, 420))

    login(client)
    statement = "I confirm that every person depicted in this library is a consenting adult."
    created = client.post(
        "/api/libraries",
        json={"name": "Archive", "root_path": str(library), "confirmed": True, "statement": statement},
    )
    assert created.status_code == 201, created.text
    library_id = created.json()["id"]
    scanned = client.post(f"/api/libraries/{library_id}/scan")
    assert scanned.status_code == 202, scanned.text

    items = client.get("/api/images", params={"library_id": library_id, "limit": 50}).json()["items"]
    by_name = {item["filename"]: item for item in items}
    clean_id = by_name["a-clean.jpg"]["id"]
    unsafe_id = by_name["unsafe-portrait.jpg"]["id"]
    blocked_id = by_name["blocked-frame.jpg"]["id"]

    assert client.post(f"/api/images/{clean_id}/rating", json={"rating": "excellent"}).status_code == 200
    assert client.post(f"/api/images/{unsafe_id}/rating", json={"rating": "safe"}).status_code == 200
    crops = client.get(f"/api/images/{unsafe_id}/crops").json()
    crop = next(item for item in crops if item["aspect_ratio"] == "4:5" and item["excludes_unsafe"])
    approved = client.post(
        f"/api/images/{unsafe_id}/crops/{crop['id']}/approve",
        json={"x": crop["x"], "y": crop["y"], "width": crop["width"], "height": crop["height"]},
    )
    assert approved.status_code == 200
    assert client.post(f"/api/images/{blocked_id}/rating", json={"rating": "safe"}).status_code == 200
    return library_id, clean_id, blocked_id


def test_curation_drafts_then_human_queues(client, app_env) -> None:
    library_id, clean_id, blocked_id = _seed_rated_library(client, app_env["library"])

    created = client.post(
        "/api/curation",
        json={"library_id": library_id, "limit": 0, "include_images": False, "confirmed": True},
    )
    assert created.status_code == 201, created.text
    batch = created.json()
    assert batch["status"] == "ready"
    assert batch["pick_count"] >= 1
    pick_ids = {pick["image"]["id"] for pick in batch["picks"]}
    assert clean_id in pick_ids
    assert blocked_id not in pick_ids
    for pick in batch["picks"]:
        assert pick["status"] == "suggested"
        assert pick["suggested_tags"]
        assert pick["captions"]
        assert pick["preferred_caption_id"]

    listed = client.get("/api/curation")
    assert listed.status_code == 200
    assert any(row["id"] == batch["id"] for row in listed.json())

    first = batch["picks"][0]
    accepted = client.patch(
        f"/api/curation/picks/{first['id']}",
        json={"status": "accepted", "suggested_tags": ["portraitmood", "availablelight"]},
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["status"] == "accepted"
    assert accepted.json()["suggested_tags"] == ["portraitmood", "availablelight"]

    reordered_ids = [pick["id"] for pick in reversed(batch["picks"])]
    reordered = client.patch(
        f"/api/curation/{batch['id']}/reorder",
        json={"pick_ids": reordered_ids},
    )
    assert reordered.status_code == 200, reordered.text
    assert [pick["id"] for pick in reordered.json()["picks"]] == reordered_ids

    queued = client.post("/api/curation/queue", json={"pick_ids": [first["id"]]})
    assert queued.status_code == 200, queued.text
    assert len(queued.json()) == 1
    assert queued.json()[0]["state"] == "CANDIDATE"
    assert queued.json()[0]["image"]["id"] == first["image"]["id"]

    refreshed = client.get(f"/api/curation/{batch['id']}").json()
    queued_pick = next(pick for pick in refreshed["picks"] if pick["id"] == first["id"])
    assert queued_pick["status"] == "queued"

    # Still not published — Instagram publish remains a separate human click on Queue.
    queue = client.get("/api/queue").json()
    assert any(item["image"]["id"] == first["image"]["id"] for item in queue)

    undone = client.post(f"/api/curation/picks/{first['id']}/undo-queue")
    assert undone.status_code == 200, undone.text
    assert undone.json()["status"] in {"suggested", "accepted"}
    refreshed_after_undo = client.get(f"/api/curation/{batch['id']}").json()
    assert next(pick for pick in refreshed_after_undo["picks"] if pick["id"] == first["id"])["status"] in {
        "suggested",
        "accepted",
    }
    queue_after = client.get("/api/queue").json()
    assert not any(item["image"]["id"] == first["image"]["id"] for item in queue_after)


def test_curation_requires_auth(client) -> None:
    assert client.get("/api/curation").status_code == 401
    assert client.post("/api/curation", json={"limit": 4}).status_code == 401


def test_rejected_pick_cannot_queue(client, app_env) -> None:
    library_id, _clean_id, _blocked_id = _seed_rated_library(client, app_env["library"])
    batch = client.post("/api/curation", json={"library_id": library_id, "limit": 8}).json()
    pick = batch["picks"][0]
    assert client.patch(f"/api/curation/picks/{pick['id']}", json={"status": "rejected"}).status_code == 200
    refused = client.post("/api/curation/queue", json={"pick_ids": [pick["id"]]})
    assert refused.status_code == 409
