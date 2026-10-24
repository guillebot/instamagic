"""Regression tests for live Gemini queue, labeling, and curation fixes."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from uuid import uuid4

from instamagic.errors import GeminiTruncated
from instamagic.logging_config import configure_logging
from instamagic.models import (
    AdultAttestation,
    CurationBatch,
    HumanRating,
    Image,
    JobStatus,
    Library,
    ProcessingJob,
    SafetyState,
)
from instamagic.models.enums import CurationBatchStatus, JobKind
from instamagic.services.gemini.curation import CurationCandidatePayload, CurationResult
from instamagic.services.queues import QUEUE_GEMINI
from instamagic.services.recommendations.curate import (
    active_curation_in_flight,
    curate_chunk_resilient,
    reconcile_stale_curation_batches,
)
from instamagic.services.recommendations.gates import gemini_gate, publishing_gate
from instamagic.services.recommendations.gemini_derivatives import has_approved_covering_blur
from instamagic.services.recommendations.label_job import resolve_label_targets
from tests.conftest import login, write_jpeg


def test_gemini_gate_allows_safe_with_classifier_review() -> None:
    result = gemini_gate(
        attested=True,
        human_rating="safe",
        model_state=SafetyState.REVIEW.value,
        needs_crop=False,
        has_approved_crop=False,
        operator_confirmed=True,
        policy="preview",
    )
    assert result.allowed
    publish = publishing_gate(
        attested=True,
        human_rating="safe",
        model_state=SafetyState.REVIEW.value,
        needs_crop=False,
        has_approved_crop=False,
    )
    assert publish.allowed


class TruncatingProvider:
    def curate_batch(
        self,
        candidates: list[CurationCandidatePayload],
        *,
        account_voice: str,
        policy: str,
        image_bytes_by_id: dict[str, bytes] | None = None,
        thinking_budget: int = 512,
        max_output_tokens: int | None = None,
    ) -> CurationResult:
        del account_voice, policy, image_bytes_by_id, thinking_budget, max_output_tokens
        if len(candidates) > 1:
            raise GeminiTruncated("Gemini output truncated (MAX_TOKENS).")
        from instamagic.services.gemini.curation import CurationPickDraft

        item = candidates[0]
        return CurationResult(
            picks=[
                CurationPickDraft(
                    image_id=item.image_id, rank=1, reasons=["ok"], tags=[], captions=[]
                )
            ]
        )


def test_curate_chunk_bisects_on_truncation() -> None:
    chunk = [
        CurationCandidatePayload(image_id="a", local_score=10),
        CurationCandidatePayload(image_id="b", local_score=9),
    ]
    outcome = curate_chunk_resilient(
        TruncatingProvider(),
        chunk,
        account_voice="v",
        policy="text_only",
        image_bytes_by_id=None,
        thinking_budget=0,
    )
    assert len(outcome.chunk_results) == 2
    assert outcome.blocked_ids == []


def test_resolve_label_targets_includes_safe_review_model_state(app_env, monkeypatch) -> None:
    from instamagic.db import session_factory

    session = session_factory()()
    library = Library(id=uuid4(), name="L", root_path=str(app_env["library"]))
    session.add(library)
    session.flush()
    session.add(
        AdultAttestation(
            library_id=library.id,
            confirmed=True,
            statement="I confirm that every person depicted in this library is a consenting adult.",
        )
    )
    image = Image(
        id=uuid4(),
        library_id=library.id,
        filename="x.jpg",
        relative_path="x.jpg",
        file_size=1,
        sha256="abc",
        human_rating=HumanRating.SAFE.value,
        model_state=SafetyState.REVIEW.value,
    )
    session.add(image)
    session.commit()

    targets, stats = resolve_label_targets(
        session,
        library_id=library.id,
        image_ids=None,
        all_eligible=True,
        covering_blur_fn=has_approved_covering_blur,
    )
    assert len(targets) == 1
    assert stats["targets"] == 1
    session.close()


def test_enqueue_uses_gemini_queue(monkeypatch) -> None:
    from instamagic.api.routes import _enqueue_worker_job
    from instamagic.config import get_settings

    monkeypatch.setenv("PIPELINE_MODE", "queue")
    get_settings.cache_clear()
    settings = get_settings()
    captured: list[str] = []

    class FakeQueue:
        def __init__(self, name, connection=None):
            captured.append(name)

        def enqueue(self, *args, **kwargs):
            return None

    monkeypatch.setattr("redis.Redis.from_url", staticmethod(lambda _u: object()))
    monkeypatch.setattr("rq.Queue", FakeQueue)

    _enqueue_worker_job(
        settings,
        "instamagic.workers.tasks.run_gemini_label_job_entry",
        "job-id",
        job_timeout=60,
        queue_name=QUEUE_GEMINI,
    )
    assert captured == [QUEUE_GEMINI]
    get_settings.cache_clear()


def test_curation_rejects_parallel_batch(client, app_env) -> None:
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
    client.post(
        f"/api/images/{client.get('/api/images', params={'library_id': library_id}).json()['items'][0]['id']}/rating",
        json={"rating": "safe"},
    )

    from instamagic.db import session_factory

    session = session_factory()()
    from uuid import UUID

    batch = CurationBatch(
        library_id=UUID(library_id),
        status=CurationBatchStatus.RUNNING.value,
        limit=0,
    )
    session.add(batch)
    session.add(
        ProcessingJob(
            kind=JobKind.CURATE.value,
            status=JobStatus.RUNNING.value,
            library_id=UUID(library_id),
            detail=json.dumps({"batch_id": str(batch.id)}),
        )
    )
    session.commit()
    session.close()

    refused = client.post(
        "/api/curation",
        json={"library_id": library_id, "limit": 0, "confirmed": True},
    )
    assert refused.status_code == 409
    session = session_factory()()
    assert active_curation_in_flight(session, UUID(library_id))
    session.close()


def test_reconcile_stale_running_batch(app_env) -> None:
    from instamagic.db import session_factory

    session = session_factory()()
    batch = CurationBatch(status=CurationBatchStatus.RUNNING.value, limit=0)
    session.add(batch)
    session.flush()
    job = ProcessingJob(
        kind=JobKind.CURATE.value,
        status=JobStatus.QUEUED.value,
        detail=json.dumps({"batch_id": str(batch.id)}),
    )
    session.add(job)
    session.commit()
    updated = reconcile_stale_curation_batches(session, "redis://127.0.0.1:9/0")
    assert updated == 1
    session.refresh(batch)
    assert batch.status == CurationBatchStatus.FAILED.value
    session.close()


def test_httpx_loggers_are_warning() -> None:
    configure_logging("INFO")
    assert logging.getLogger("httpx").level == logging.WARNING
    assert logging.getLogger("httpcore").level == logging.WARNING
