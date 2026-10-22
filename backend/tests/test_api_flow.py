from pathlib import Path
from uuid import UUID

from instamagic.db import session_factory
from instamagic.models import Image as ImageRow
from PIL import Image, ImageDraw
from tests.conftest import login


def test_health_and_login_required(client) -> None:
    health = client.get("/health")
    assert health.status_code == 200
    body = health.json()
    assert body["status"] == "ok"
    assert "version" in body
    assert client.get("/api/libraries").status_code == 401
    login(client)
    assert client.get("/api/auth/session").json()["authenticated"] is True
    config = client.get("/api/config")
    assert config.status_code == 200
    assert config.json()["version"] == body["version"]


def test_scan_does_not_modify_originals_and_gates_queue(client, app_env) -> None:
    library: Path = app_env["library"]
    session_dir = library / "session-001"
    _marked(session_dir / "a-clean.jpg", (30, 30, 30), (20, 20, 80, 80))
    _marked(session_dir / "unsafe-portrait.jpg", (90, 40, 40), (200, 40, 280, 120))
    _marked(session_dir / "blocked-frame.jpg", (40, 40, 90), (40, 300, 180, 420))
    (session_dir / "z-copy.jpg").write_bytes((session_dir / "a-clean.jpg").read_bytes())
    (session_dir / "frame.cr3").write_bytes(b"not-a-real-raw")
    before = {path.relative_to(library).as_posix(): path.read_bytes() for path in library.rglob("*") if path.is_file()}

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
    assert scanned.json()["status"] == "succeeded"
    jobs = client.get("/api/jobs", params={"library_id": library_id})
    assert jobs.status_code == 200, jobs.text
    job = jobs.json()[0]
    assert job["status"] == "succeeded"
    assert job["files_seen"] == 5
    assert job["files_processed"] == 3
    assert job["files_skipped"] == 2
    assert job["files_failed"] == 0
    assert job["files_total"] == 5
    assert job["started_at"]
    assert job["finished_at"]
    assert "seen=5" in job["detail"]

    after = {path.relative_to(library).as_posix(): path.read_bytes() for path in library.rglob("*") if path.is_file()}
    assert after == before

    listed = client.get("/api/images", params={"library_id": library_id, "limit": 50})
    assert listed.status_code == 200
    items = listed.json()["items"]
    by_name = {item["filename"]: item for item in items}
    assert set(by_name) == {"a-clean.jpg", "unsafe-portrait.jpg", "blocked-frame.jpg"}
    assert by_name["unsafe-portrait.jpg"]["model_state"] == "SAFE_AFTER_CROP"
    assert by_name["blocked-frame.jpg"]["model_state"] == "DO_NOT_POST"
    assert by_name["a-clean.jpg"]["model_state"] == "REVIEW"
    assert by_name["a-clean.jpg"]["has_gps"] is False
    assert by_name["a-clean.jpg"]["duplicate_count"] == 1
    assert by_name["a-clean.jpg"]["has_approved_crop"] is False

    thumb = client.get(by_name["a-clean.jpg"]["thumbnail_url"])
    assert thumb.status_code == 200
    assert thumb.headers["content-type"].startswith("image/jpeg")

    clean_id = by_name["a-clean.jpg"]["id"]
    review_crops = client.get(f"/api/images/{clean_id}/crops").json()
    assert review_crops
    review_crop = next(item for item in review_crops if item["aspect_ratio"] == "4:5")
    review_approved = client.post(
        f"/api/images/{clean_id}/crops/{review_crop['id']}/approve",
        json={
            "x": review_crop["x"],
            "y": review_crop["y"],
            "width": review_crop["width"],
            "height": review_crop["height"],
        },
    )
    assert review_approved.status_code == 200, review_approved.text
    assert review_approved.json()["approved"] is True
    after_review_crop = client.get(f"/api/images/{clean_id}").json()
    assert after_review_crop["model_state"] == "REVIEW"
    assert after_review_crop["has_approved_crop"] is True

    rated = client.post(f"/api/images/{clean_id}/rating", json={"rating": "excellent"})
    assert rated.status_code == 200
    queued = client.post("/api/queue", json={"image_id": clean_id})
    assert queued.status_code == 201, queued.text

    blocked_id = by_name["blocked-frame.jpg"]["id"]
    client.post(f"/api/images/{blocked_id}/rating", json={"rating": "safe"})
    refused = client.post("/api/queue", json={"image_id": blocked_id})
    assert refused.status_code == 409
    blocked_crops = client.get(f"/api/images/{blocked_id}/crops").json()
    if blocked_crops:
        blocked_crop = blocked_crops[0]
        blocked_approve = client.post(f"/api/images/{blocked_id}/crops/{blocked_crop['id']}/approve")
        assert blocked_approve.status_code == 409
        assert "DO_NOT_POST" in blocked_approve.json()["detail"]

    unsafe_id = by_name["unsafe-portrait.jpg"]["id"]
    client.post(f"/api/images/{unsafe_id}/rating", json={"rating": "safe"})
    too_soon = client.post("/api/queue", json={"image_id": unsafe_id})
    assert too_soon.status_code == 409
    crops = client.get(f"/api/images/{unsafe_id}/crops").json()
    crop = next(item for item in crops if item["aspect_ratio"] == "4:5" and item["excludes_unsafe"])
    approved = client.post(
        f"/api/images/{unsafe_id}/crops/{crop['id']}/approve",
        json={"x": crop["x"], "y": crop["y"], "width": crop["width"], "height": crop["height"]},
    )
    assert approved.status_code == 200
    assert approved.json()["approved"] is True
    after_safe_crop = client.get(f"/api/images/{unsafe_id}").json()
    assert after_safe_crop["model_state"] == "SAFE_AFTER_CROP"
    assert after_safe_crop["has_approved_crop"] is True
    queued_crop = client.post("/api/queue", json={"image_id": unsafe_id})
    assert queued_crop.status_code == 201

    captions = client.post(f"/api/images/{clean_id}/captions", json={"confirmed": False})
    assert captions.status_code == 201, captions.text
    assert len(captions.json()) >= 8
    assert all("comment yes" not in row["text"].lower() for row in captions.json())

    candidates = client.get("/api/candidates").json()
    candidate_ids = {row["image"]["id"] for row in candidates}
    assert clean_id in candidate_ids
    assert unsafe_id in candidate_ids
    assert blocked_id not in candidate_ids

    db = session_factory()()
    image = db.get(ImageRow, UUID(clean_id))
    assert image is not None
    image.thumbnail_path = "../../etc/passwd"
    db.commit()
    db.close()
    escaped = client.get(f"/api/derivatives/{clean_id}/thumbnail")
    assert escaped.status_code == 404


def _marked(path: Path, color: tuple[int, int, int], box: tuple[int, int, int, int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (400, 600), color)
    ImageDraw.Draw(image).rectangle(box, fill=(230, 230, 230))
    image.save(path, format="JPEG", quality=90)


def test_instagram_not_configured_client_refuses_publish() -> None:
    from instamagic.services.instagram.client import NotConfiguredInstagramClient

    client = NotConfiguredInstagramClient()
    assert client.configured() is False
    try:
        client.publish_image(image_url="https://example.test/a.jpg", caption="x")
        raise AssertionError("expected not configured")
    except Exception as exc:
        assert exc.__class__.__name__ == "InstagramNotConfigured"
