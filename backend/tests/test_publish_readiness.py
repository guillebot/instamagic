"""Publish-readiness summary for Review classify modal."""

from __future__ import annotations

from pathlib import Path

from tests.conftest import login

ATTESTATION = "I confirm that every person depicted in this library is a consenting adult."


def _attested_library(client, library: Path) -> str:
    created = client.post(
        "/api/libraries",
        json={
            "name": "Archive",
            "root_path": str(library),
            "confirmed": True,
            "statement": ATTESTATION,
        },
    )
    assert created.status_code == 201, created.text
    library_id = created.json()["id"]
    assert client.post(f"/api/libraries/{library_id}/scan").status_code == 202
    return library_id


def test_publish_readiness_blocks_do_not_post(client, app_env) -> None:
    from PIL import Image, ImageDraw

    library: Path = app_env["library"]
    path = library / "session" / "blocked.jpg"
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (400, 600), (40, 40, 90))
    ImageDraw.Draw(image).rectangle((40, 300, 180, 420), fill=(230, 230, 230))
    image.save(path, format="JPEG", quality=90)
    login(client)
    library_id = _attested_library(client, library)
    items = client.get("/api/images", params={"library_id": library_id}).json()["items"]
    blocked = next(item for item in items if item["model_state"] == "DO_NOT_POST")
    client.post(f"/api/images/{blocked['id']}/rating", json={"rating": "safe"})
    ready = client.get(f"/api/images/{blocked['id']}/publish-readiness")
    assert ready.status_code == 200, ready.text
    body = ready.json()
    assert body["publishing_allowed"] is False
    assert any("DO_NOT_POST" in reason for reason in body["publishing_reasons"])


def test_publish_readiness_allows_excellent(client, app_env) -> None:
    from PIL import Image

    library: Path = app_env["library"]
    path = library / "session" / "clean.jpg"
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (200, 200), (30, 30, 30)).save(path, format="JPEG")
    login(client)
    library_id = _attested_library(client, library)
    image_id = client.get("/api/images", params={"library_id": library_id}).json()["items"][0]["id"]
    client.post(f"/api/images/{image_id}/rating", json={"rating": "excellent"})
    ready = client.get(f"/api/images/{image_id}/publish-readiness")
    assert ready.status_code == 200, ready.text
    body = ready.json()
    assert body["publishing_allowed"] is True
    assert body["publishing_reasons"] == []
