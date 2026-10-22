from pathlib import Path

from instamagic.services.gemini.labeling import label_status_is_terminal, should_retry_label
from tests.conftest import login, write_jpeg


def test_label_status_terminal_and_retry() -> None:
    assert label_status_is_terminal("ok")
    assert label_status_is_terminal("blocked:SAFETY")
    assert not label_status_is_terminal(None)
    assert should_retry_label(None, 0)
    assert not should_retry_label("blocked:SAFETY", 1)
    assert not should_retry_label("error", 5)


def test_enqueue_gemini_label_job(client, app_env) -> None:
    library: Path = app_env["library"]
    write_jpeg(library / "safe.jpg")
    login(client)
    created = client.post(
        "/api/libraries",
        json={
            "name": "L",
            "root_path": str(library),
            "confirmed": True,
            "statement": "I confirm that every person depicted in this library is a consenting adult.",
        },
    )
    library_id = created.json()["id"]
    client.post(f"/api/libraries/{library_id}/scan")
    image_id = client.get("/api/images", params={"library_id": library_id}).json()["items"][0]["id"]
    client.post(f"/api/images/{image_id}/rating", json={"rating": "safe"})
    queued = client.post("/api/gemini/label", json={"image_ids": [image_id]})
    assert queued.status_code == 202, queued.text
    job_id = queued.json()["job_id"]
    job = client.get(f"/api/jobs/{job_id}")
    assert job.status_code == 200
    assert job.json()["kind"] == "gemini_label"
    assert job.json()["status"] in {"succeeded", "running", "queued"}
