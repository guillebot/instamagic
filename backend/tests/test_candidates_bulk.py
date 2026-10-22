"""Bulk candidate actions and paginated candidates API."""

from __future__ import annotations

from pathlib import Path

from instamagic.models.enums import SafetyState
from tests.conftest import login

ATTESTATION = "I confirm that every person depicted in this library is a consenting adult."


def _marked(path: Path, color: tuple[int, int, int], box: tuple[int, int, int, int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (400, 600), color)
    ImageDraw.Draw(image).rectangle(box, fill=(230, 230, 230))
    image.save(path, format="JPEG", quality=90)


def _library_with_images(client, app_env) -> tuple[str, str, str]:
    library: Path = app_env["library"]
    _marked(library / "session" / "clean.jpg", (30, 30, 30), (20, 20, 80, 80))
    _marked(library / "session" / "blocked-frame.jpg", (40, 40, 90), (40, 300, 180, 420))
    login(client)
    created = client.post(
        "/api/libraries",
        json={"name": "BulkCandidates", "root_path": str(library), "confirmed": True, "statement": ATTESTATION},
    )
    assert created.status_code == 201
    library_id = created.json()["id"]
    assert client.post(f"/api/libraries/{library_id}/scan").status_code == 202
    items = client.get("/api/images", params={"library_id": library_id}).json()["items"]
    clean = next(row for row in items if row["model_state"] != SafetyState.DO_NOT_POST.value)
    blocked = next(row for row in items if row["model_state"] == SafetyState.DO_NOT_POST.value)
    client.post(f"/api/images/{clean['id']}/rating", json={"rating": "excellent"})
    client.post(f"/api/images/{blocked['id']}/rating", json={"rating": "safe"})
    return library_id, clean["id"], blocked["id"]


def test_candidates_page_shape(client, app_env) -> None:
    _library_with_images(client, app_env)
    page = client.get("/api/candidates", params={"limit": 5, "offset": 0})
    assert page.status_code == 200
    body = page.json()
    assert "items" in body
    assert isinstance(body["items"], list)
    if body["items"]:
        row = body["items"][0]
        assert "has_tags" in row
        assert "has_caption" in row
        assert "published" in row


def test_bulk_queue_enforces_gates_per_image(client, app_env) -> None:
    _, clean_id, blocked_id = _library_with_images(client, app_env)
    response = client.post(
        "/api/candidates/queue",
        json={"image_ids": [clean_id, blocked_id]},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["queued_count"] == 1
    by_id = {row["image_id"]: row for row in body["results"]}
    assert by_id[clean_id]["ok"] is True
    assert by_id[blocked_id]["ok"] is False
    assert by_id[blocked_id]["error"]


def test_bulk_queue_requires_attestation(client, app_env) -> None:
    library: Path = app_env["library"]
    _marked(library / "session" / "solo.jpg", (30, 30, 30), (20, 20, 80, 80))
    login(client)
    created = client.post(
        "/api/libraries",
        json={"name": "NoAttest", "root_path": str(library), "confirmed": False, "statement": ""},
    )
    library_id = created.json()["id"]
    assert client.post(f"/api/libraries/{library_id}/scan").status_code == 202
    image_id = client.get("/api/images", params={"library_id": library_id}).json()["items"][0]["id"]
    client.post(f"/api/images/{image_id}/rating", json={"rating": "safe"})
    result = client.post("/api/candidates/queue", json={"image_ids": [image_id]}).json()
    assert result["queued_count"] == 0
    assert result["results"][0]["ok"] is False
    assert "attestation" in (result["results"][0]["error"] or "").lower()


def test_bulk_reject_removes_from_candidates(client, app_env) -> None:
    _, clean_id, _ = _library_with_images(client, app_env)
    before = client.get("/api/candidates").json()["items"]
    assert any(row["image"]["id"] == clean_id for row in before)
    rejected = client.post("/api/candidates/reject", json={"image_ids": [clean_id]})
    assert rejected.status_code == 200
    assert rejected.json()[0]["ok"] is True
    after = client.get("/api/candidates").json()["items"]
    assert not any(row["image"]["id"] == clean_id for row in after)
