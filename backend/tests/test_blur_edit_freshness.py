"""Moving/resizing/rotating a blur must always produce a fresh, uncacheable-when-stale render.

Regression for "when I move the blurred area after first attempt, the blur does not update":
the render was rewritten in place behind a stable ``immutable`` URL, and the first cover's
render was skipped because the loaded ``image.blurs`` collection did not include the new row.
"""

from __future__ import annotations

import io
from pathlib import Path

import numpy as np
from PIL import Image as PilImage
from tests.conftest import login

ATTESTATION = "I confirm that every person depicted in this library is a consenting adult."
LAN = {"X-Forwarded-For": "127.0.0.1"}


def _checkerboard(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image = PilImage.new("RGB", (240, 240))
    pixels = image.load()
    for y in range(240):
        for x in range(240):
            pixels[x, y] = (255, 255, 255) if (x // 6 + y // 6) % 2 else (0, 0, 0)
    image.save(path, format="JPEG", quality=95)


def _setup(client, app_env) -> str:
    library = app_env["library"]
    _checkerboard(library / "frame.jpg")
    login(client)
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
    image_id = client.get("/api/images", params={"library_id": library_id}).json()["items"][0]["id"]
    client.post(f"/api/images/{image_id}/rating", json={"rating": "safe"})
    return image_id


def _sharpness(jpeg: bytes, x: int, y: int, size: int = 24) -> float:
    arr = np.asarray(PilImage.open(io.BytesIO(jpeg)).convert("L"), dtype=np.float32)
    return float(arr[y : y + size, x : x + size].std())


def _blurs(client, image_id: str) -> list[dict]:
    return client.get(f"/api/images/{image_id}/blurs").json()


def test_first_blur_is_rendered_immediately(client, app_env) -> None:
    image_id = _setup(client, app_env)
    created = client.post(
        f"/api/images/{image_id}/blurs",
        json={
            "x": 20,
            "y": 20,
            "width": 80,
            "height": 80,
            "shape": "rectangle",
            "strength": "strong",
        },
    )
    assert created.status_code == 201, created.text
    url = created.json()["preview_url"]
    assert url and "?v=" in url
    body = client.get(url).content
    assert _sharpness(body, 48, 48) < 40  # blurred where the cover is
    assert _sharpness(body, 180, 180) > 80  # sharp elsewhere
    assert (app_env["derivatives"] / f"{image_id}/blurs/thumb.jpg").is_file()


def test_move_changes_url_and_pixels(client, app_env) -> None:
    image_id = _setup(client, app_env)
    blur = client.post(
        f"/api/images/{image_id}/blurs",
        json={
            "x": 20,
            "y": 20,
            "width": 80,
            "height": 80,
            "shape": "rectangle",
            "strength": "strong",
        },
    ).json()
    first_url = blur["preview_url"]
    first = client.get(first_url)
    assert "immutable" in first.headers["cache-control"]

    moved = client.patch(f"/api/images/{image_id}/blurs/{blur['id']}", json={"x": 140, "y": 140})
    assert moved.status_code == 200, moved.text
    second_url = moved.json()["preview_url"]
    assert second_url != first_url
    body = client.get(second_url).content
    assert _sharpness(body, 48, 48) > 80  # old position is sharp again
    assert _sharpness(body, 168, 168) < 40  # new position is blurred

    listed = _blurs(client, image_id)
    assert listed[0]["preview_url"] == second_url
    detail = client.get(f"/api/images/{image_id}").json()
    assert "?v=" in (detail["publish_preview_url"] or "")


def test_rotation_shape_and_corner_change_the_version(client, app_env) -> None:
    image_id = _setup(client, app_env)
    blur = client.post(
        f"/api/images/{image_id}/blurs",
        json={
            "x": 40,
            "y": 40,
            "width": 120,
            "height": 60,
            "shape": "rectangle",
            "strength": "strong",
        },
    ).json()
    seen = {blur["preview_url"]}
    for patch in (
        {"rotation_deg": 30},
        {"shape": "ellipse"},
        {"shape": "rectangle", "corner_radius": 0.4},
    ):
        response = client.patch(f"/api/images/{image_id}/blurs/{blur['id']}", json=patch)
        assert response.status_code == 200, response.text
        url = response.json()["preview_url"]
        assert url not in seen, patch
        seen.add(url)


def test_unversioned_render_urls_are_not_cached(client, app_env) -> None:
    image_id = _setup(client, app_env)
    blur = client.post(
        f"/api/images/{image_id}/blurs",
        json={"x": 20, "y": 20, "width": 80, "height": 80, "strength": "strong"},
    ).json()
    bare = client.get(f"/api/derivatives/{image_id}/blurs/{blur['id']}")
    assert bare.status_code == 200
    assert bare.headers["cache-control"] == "private, no-cache"
    for kind in ("blurred-preview", "blurred-thumbnail"):
        response = client.get(f"/api/derivatives/{image_id}/{kind}")
        assert response.status_code == 200
        assert response.headers["cache-control"] == "private, no-cache"
        versioned = client.get(f"/api/derivatives/{image_id}/{kind}?v=abc")
        assert "immutable" in versioned.headers["cache-control"]


def test_second_cover_and_delete_render_the_exact_set(client, app_env) -> None:
    image_id = _setup(client, app_env)
    first = client.post(
        f"/api/images/{image_id}/blurs",
        json={
            "x": 10,
            "y": 10,
            "width": 70,
            "height": 70,
            "shape": "rectangle",
            "strength": "strong",
        },
    ).json()
    second = client.post(
        f"/api/images/{image_id}/blurs",
        json={
            "x": 150,
            "y": 150,
            "width": 70,
            "height": 70,
            "shape": "rectangle",
            "strength": "strong",
        },
    ).json()
    body = client.get(second["preview_url"]).content
    assert _sharpness(body, 33, 33) < 40
    assert _sharpness(body, 173, 173) < 40  # new cover is in the render right away

    assert client.delete(f"/api/images/{image_id}/blurs/{first['id']}").status_code == 204
    remaining = _blurs(client, image_id)
    assert [b["id"] for b in remaining] == [second["id"]]
    body = client.get(remaining[0]["preview_url"]).content
    assert _sharpness(body, 33, 33) > 80  # deleted cover is no longer baked in
    assert _sharpness(body, 173, 173) < 40


def test_edit_after_queue_regates_and_publish_uses_new_geometry(client, app_env) -> None:
    image_id = _setup(client, app_env)
    blur = client.post(
        f"/api/images/{image_id}/blurs",
        json={
            "x": 20,
            "y": 20,
            "width": 80,
            "height": 80,
            "shape": "rectangle",
            "strength": "strong",
        },
    ).json()
    assert client.post(f"/api/images/{image_id}/blurs/approve").status_code == 200
    assert client.post("/api/queue", json={"image_id": image_id}).status_code in (200, 201)
    before = client.get(f"/api/images/{image_id}/publish-preview").content
    story_before = client.get(f"/api/images/{image_id}/story-preview", headers=LAN).content
    assert _sharpness(before, 48, 48) < 40

    moved = client.patch(f"/api/images/{image_id}/blurs/{blur['id']}", json={"x": 140, "y": 140})
    assert moved.status_code == 200
    assert moved.json()["approved"] is False
    ready = client.get(f"/api/images/{image_id}/publish-readiness").json()
    assert ready["publishing_allowed"] is False
    # Publish render refuses unapproved covers: the queued item is re-gated, never stale.
    assert client.get(f"/api/images/{image_id}/publish-preview").status_code in (403, 409)

    assert client.post(f"/api/images/{image_id}/blurs/approve").status_code == 200
    after = client.get(f"/api/images/{image_id}/publish-preview").content
    assert after != before
    assert _sharpness(after, 48, 48) > 80
    assert _sharpness(after, 168, 168) < 40
    story_after = client.get(f"/api/images/{image_id}/story-preview", headers=LAN).content
    assert story_after != story_before
    thumb = client.get(client.get(f"/api/images/{image_id}").json()["thumbnail_url"])
    assert thumb.status_code == 200
