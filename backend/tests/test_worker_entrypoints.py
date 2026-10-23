"""End-to-end worker task tests (curation + Gemini label) with fake provider."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch
from uuid import UUID, uuid4

from instamagic.config import get_settings
from instamagic.models import CurationBatch, JobStatus, ProcessingJob
from instamagic.models.enums import CurationBatchStatus, JobKind
from instamagic.services.recommendations.curate import execute_curation_batch
from instamagic.services.recommendations.gemini_derivatives import gemini_preview_bytes
from instamagic.services.recommendations.label_job import create_gemini_label_job
from instamagic.workers.tasks import run_curation_job, run_gemini_label_job_entry
from tests.conftest import login, write_jpeg


def _library_with_safe_images(client, app_env: dict, count: int = 2) -> str:
    library: Path = app_env["library"]
    for index in range(count):
        write_jpeg(library / f"img-{index}.jpg", color=(20 + index * 40, 30, 50))
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
    for item in items:
        client.post(f"/api/images/{item['id']}/rating", json={"rating": "safe"})
    return library_id


def test_execute_curation_batch_accepts_performance_weight() -> None:
    assert "performance_weight" in execute_curation_batch.__code__.co_varnames


def test_run_curation_job_threads_performance_weight(client, app_env, monkeypatch) -> None:
    library_id = _library_with_safe_images(client, app_env, count=1)
    from instamagic.db import session_factory

    session = session_factory()()
    batch = CurationBatch(
        library_id=UUID(library_id),
        status=CurationBatchStatus.RUNNING.value,
        limit=0,
    )
    session.add(batch)
    session.flush()
    job = ProcessingJob(
        kind=JobKind.CURATE.value,
        status=JobStatus.QUEUED.value,
        library_id=UUID(library_id),
        detail=json.dumps({"batch_id": str(batch.id), "image_ids": []}),
    )
    session.add(job)
    session.commit()
    job_id = str(job.id)
    session.close()

    captured: dict[str, float] = {}

    def spy_execute(*args, **kwargs):
        captured["performance_weight"] = kwargs.get("performance_weight", -1.0)
        session = args[0]
        batch_row = session.get(CurationBatch, batch.id)
        assert batch_row is not None
        batch_row.status = CurationBatchStatus.READY.value
        session.commit()
        return batch_row

    monkeypatch.setattr("instamagic.workers.tasks.execute_curation_batch", spy_execute)
    run_curation_job(job_id)
    assert captured.get("performance_weight") == get_settings().curate_performance_weight


def test_run_curation_job_completes_with_fake_gemini(client, app_env) -> None:
    library_id = _library_with_safe_images(client, app_env, count=2)
    from instamagic.db import session_factory

    session = session_factory()()
    batch = CurationBatch(
        library_id=UUID(library_id),
        status=CurationBatchStatus.RUNNING.value,
        limit=0,
    )
    session.add(batch)
    session.flush()
    job = ProcessingJob(
        kind=JobKind.CURATE.value,
        status=JobStatus.QUEUED.value,
        library_id=UUID(library_id),
        detail=json.dumps({"batch_id": str(batch.id), "image_ids": []}),
    )
    session.add(job)
    session.commit()
    job_id = str(job.id)
    session.close()

    run_curation_job(job_id)

    session = session_factory()()
    finished = session.get(ProcessingJob, UUID(job_id))
    assert finished is not None
    assert finished.status == JobStatus.SUCCEEDED.value
    refreshed = session.get(CurationBatch, batch.id)
    assert refreshed is not None
    assert refreshed.status == CurationBatchStatus.READY.value
    session.close()


def test_curation_api_includes_held_back(client, app_env) -> None:
    library_id = _library_with_safe_images(client, app_env, count=2)
    response = client.post(
        "/api/curation",
        json={"library_id": library_id, "limit": 1, "confirmed": True},
    )
    assert response.status_code == 201
    body = response.json()
    assert "held_back" in body
    assert isinstance(body["held_back"], int)
    assert body["held_back"] >= 0


def test_run_gemini_label_job_entry_labels_all_eligible(client, app_env, monkeypatch) -> None:
    monkeypatch.setenv("GEMINI_LABEL_CONCURRENCY", "1")
    get_settings.cache_clear()
    monkeypatch.setattr(
        "instamagic.api.routes._enqueue_gemini_label_work",
        lambda *_args, **_kwargs: None,
    )
    library_id = _library_with_safe_images(client, app_env, count=4)
    items = client.get("/api/images", params={"library_id": library_id}).json()["items"]
    image_ids = [UUID(item["id"]) for item in items]
    assert len(image_ids) == 4
    from instamagic.db import session_factory

    session = session_factory()()
    job = create_gemini_label_job(
        session,
        library_id=UUID(library_id),
        image_ids=image_ids,
        all_eligible=True,
    )
    session.commit()
    job_id = str(job.id)
    session.close()

    run_gemini_label_job_entry(job_id)

    session = session_factory()()
    finished = session.get(ProcessingJob, UUID(job_id))
    assert finished is not None
    assert finished.status == JobStatus.SUCCEEDED.value
    assert finished.files_processed == 4
    assert finished.files_failed == 0
    images = client.get("/api/images", params={"library_id": library_id}).json()["items"]
    assert len(images) == 4
    assert all(item.get("gemini_label_status") == "ok" for item in images)
    session.close()


def test_gemini_preview_bytes_safe_join_accepts_derivative_path_string(app_env, monkeypatch) -> None:
    from instamagic.db import session_factory
    from instamagic.models import Image, Library
    session = session_factory()()
    settings = get_settings()
    library = Library(id=uuid4(), name="L", root_path=str(app_env["library"]))
    session.add(library)
    session.flush()
    image = Image(
        id=uuid4(),
        library_id=library.id,
        filename="x.jpg",
        relative_path="x.jpg",
        file_size=1,
        sha256="abc",
        preview_path="previews/x.jpg",
    )
    session.add(image)
    session.commit()

    derivatives = Path(app_env["derivatives"])
    rel = "publish/x-test.jpg"
    target = derivatives / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"jpeg")

    with patch(
        "instamagic.services.recommendations.gemini_derivatives.ensure_publish_derivative",
        return_value=rel,
    ):
        with patch(
            "instamagic.services.recommendations.gemini_derivatives.has_any_approved_blur",
            return_value=True,
        ):
            raw = gemini_preview_bytes(session, settings, image, None, require_policy=False)
    assert raw == b"jpeg"
    session.close()
