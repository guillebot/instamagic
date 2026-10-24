"""Curate "why not picked": read-only reasons for safe-rated images that were not picked."""

import time
from pathlib import Path

from PIL import Image, ImageDraw
from tests.conftest import legacy_rate, login


def _marked(path: Path, color: tuple[int, int, int], box: tuple[int, int, int, int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (400, 600), color)
    ImageDraw.Draw(image).rectangle(box, fill=(230, 230, 230))
    image.save(path, format="JPEG", quality=90)


def _seed(client, library: Path) -> tuple[str, dict[str, str]]:
    session_dir = library / "s1"
    _marked(session_dir / "a-clean.jpg", (30, 30, 30), (20, 20, 80, 80))
    _marked(session_dir / "a-clean-copy.jpg", (31, 30, 30), (22, 20, 82, 80))
    _marked(session_dir / "blocked-frame.jpg", (40, 40, 90), (40, 300, 180, 420))
    login(client)
    statement = "I confirm that every person depicted in this library is a consenting adult."
    created = client.post(
        "/api/libraries",
        json={"name": "Archive", "root_path": str(library), "confirmed": True, "statement": statement},
    )
    library_id = created.json()["id"]
    assert client.post(f"/api/libraries/{library_id}/scan").status_code == 202
    items = client.get("/api/images", params={"library_id": library_id, "limit": 50}).json()["items"]
    ids = {item["filename"]: item["id"] for item in items}
    for name, image_id in ids.items():
        response = client.post(f"/api/images/{image_id}/rating", json={"rating": "safe"})
        if name == "blocked-frame.jpg":
            # The API refuses safe on a flagged, unverified photo; seed it as a legacy row so
            # the skipped/why-not-picked gates are still exercised on their own.
            assert response.status_code == 409, response.text
            legacy_rate(image_id, "safe")
        else:
            assert response.status_code == 200
    return library_id, ids


def _state(client, image_id: str) -> tuple:
    row = client.get(f"/api/images/{image_id}").json()
    return (row.get("human_rating"), row.get("model_state"), row.get("blur_unlock_status"))


def test_why_not_picked_reasons_and_safe_thumbnails(client, app_env) -> None:
    library_id, ids = _seed(client, app_env["library"])
    blocked = ids["blocked-frame.jpg"]
    # Partial cover over the flagged region (fake classifier box x=140..260, y=72..240).
    cover = client.post(
        f"/api/images/{blocked}/blurs",
        json={"x": 140, "y": 72, "width": 50, "height": 168, "shape": "rectangle", "strength": "standard"},
    )
    assert cover.status_code == 201, cover.text
    # A blur edit on an unverified photo drops it back to review (blur and verify first).
    assert client.get(f"/api/images/{blocked}").json()["human_rating"] == "review"
    legacy_rate(blocked, "safe")  # legacy row: the curation gate must still skip it on its own
    before = {image_id: _state(client, image_id) for image_id in ids.values()}

    created = client.post(
        "/api/curation",
        json={"library_id": library_id, "limit": 0, "include_images": False, "confirmed": True},
    )
    assert created.status_code == 201, created.text
    batch = created.json()
    picked = {pick["image"]["id"] for pick in batch["picks"]}
    assert all(pick["rated_at"] for pick in batch["picks"])
    skipped = batch["skipped"]
    assert skipped is not None
    by_id = {row["image_id"]: row for row in skipped["items"]}
    assert picked.isdisjoint(by_id)

    flagged = by_id[blocked]
    assert flagged["code"] == "dnp_coverage", flagged
    assert "threshold" in flagged["reason"] and "%" in flagged["reason"]
    assert flagged["coverage_percent"] < flagged["coverage_threshold_percent"]
    assert flagged["blur_editor"] is True
    # Flagged image: blurred render only, never the original thumbnail.
    assert flagged["thumbnail_url"].startswith(f"/api/derivatives/{blocked}/blurred-thumbnail")

    clean_pair = {ids["a-clean.jpg"], ids["a-clean-copy.jpg"]}
    assert len(clean_pair & picked) == 1
    (dup_id,) = clean_pair - picked
    assert by_id[dup_id]["code"] == "near_duplicate"
    assert by_id[dup_id]["duplicate_of"] in picked
    assert by_id[dup_id]["thumbnail_url"] == f"/api/derivatives/{dup_id}/thumbnail"

    # Read-only: ratings, safety state and blur status are untouched; nothing got queued.
    assert {image_id: _state(client, image_id) for image_id in ids.values()} == before
    assert client.get("/api/queue").json() == []

    # Stored on the batch and returned again on read.
    again = client.get(f"/api/curation/{batch['id']}").json()
    assert {row["image_id"] for row in again["skipped"]["items"]} == set(by_id)


def test_flagged_without_blur_has_no_thumbnail(client, app_env) -> None:
    library_id, ids = _seed(client, app_env["library"])
    created = client.post(
        "/api/curation",
        json={"library_id": library_id, "limit": 0, "include_images": False, "confirmed": True},
    )
    assert created.status_code == 201, created.text
    rows = {row["image_id"]: row for row in created.json()["skipped"]["items"]}
    flagged = rows[ids["blocked-frame.jpg"]]
    assert flagged["code"] == "dnp_no_blur"
    assert flagged["thumbnail_url"] is None


def test_since_last_run_summary(client, app_env) -> None:
    library_id, ids = _seed(client, app_env["library"])
    body = {"library_id": library_id, "limit": 0, "include_images": False, "confirmed": True}
    assert client.post("/api/curation", json=body).status_code == 201
    time.sleep(1.2)  # SQLite rating timestamps have one-second resolution
    # Re-rate one image after the first run; it counts as "since last run".
    assert client.post(f"/api/images/{ids['blocked-frame.jpg']}/rating", json={"rating": "excellent"}).status_code == 409
    legacy_rate(ids["blocked-frame.jpg"], "excellent")
    second = client.post("/api/curation", json=body).json()
    skipped = second["skipped"]
    assert skipped["since"] is not None
    assert skipped["since_rated"] == 1
    assert skipped["since_skipped"] == 1
    assert skipped["since_picked"] == 0
