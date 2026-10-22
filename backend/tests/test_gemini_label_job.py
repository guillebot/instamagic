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


def test_label_job_continues_after_apply_failure(client, app_env, monkeypatch) -> None:
    from instamagic.config import get_settings
    from instamagic.services.recommendations import label_job as label_job_module

    monkeypatch.setenv("GEMINI_LABEL_CONCURRENCY", "1")
    get_settings.cache_clear()

    def inline_enqueue(_session, settings, *, existing_job=None, **_kwargs):
        from instamagic.workers.tasks import run_gemini_label_job_entry

        if existing_job is not None:
            run_gemini_label_job_entry(str(existing_job.id))
        return existing_job

    monkeypatch.setattr("instamagic.api.routes._enqueue_gemini_label_work", inline_enqueue)

    library: Path = app_env["library"]
    write_jpeg(library / "a.jpg", color=(12, 24, 48))
    write_jpeg(library / "b.jpg", color=(180, 60, 90), size=(500, 700))
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
    items = client.get("/api/images", params={"library_id": library_id}).json()["items"]
    by_name = {item["filename"]: item for item in items}
    for item in items:
        client.post(f"/api/images/{item['id']}/rating", json={"rating": "safe"})

    fail_image_id = by_name["a.jpg"]["id"]
    original_apply = label_job_module._apply_label_result

    def flaky_apply(session, image, result):
        if str(image.id) == fail_image_id:
            raise RuntimeError("simulated persistence failure")
        return original_apply(session, image, result)

    monkeypatch.setattr(label_job_module, "_apply_label_result", flaky_apply)

    queued = client.post("/api/gemini/label", json={"library_id": library_id, "all_eligible": True})
    assert queued.status_code == 202
    job = client.get(f"/api/jobs/{queued.json()['job_id']}").json()
    assert job["status"] == "succeeded"
    assert job["files_failed"] == 1
    assert job["files_processed"] == 1
    refreshed = client.get("/api/images", params={"library_id": library_id}).json()["items"]
    statuses = {item["filename"]: item["gemini_label_status"] for item in refreshed}
    assert statuses["a.jpg"] == "error"
    assert statuses["b.jpg"] == "ok"
