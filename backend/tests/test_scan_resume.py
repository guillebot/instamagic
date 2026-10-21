from types import SimpleNamespace
from uuid import UUID, uuid4

from instamagic.db import session_factory
from instamagic.models import JobStatus, Library, ProcessingJob
from instamagic.workers.resume import SCAN_FUNC, requeue_interrupted_scans
from sqlalchemy import select
from tests.conftest import login, write_jpeg

STATEMENT = "I confirm that every person depicted in this library is a consenting adult."


def _fake_rq(monkeypatch):
    jobs: dict[str, SimpleNamespace] = {}
    waiting: list[str] = []

    class FakeQueue:
        def __init__(self, *_args, **_kwargs) -> None:
            self.job_ids = waiting

        def enqueue(self, func, library_arg, job_arg, job_timeout=None):
            rq_id = f"rq-{len(jobs)}"
            jobs[rq_id] = SimpleNamespace(func_name=func, args=(library_arg, job_arg))
            waiting.append(rq_id)

    monkeypatch.setattr("instamagic.workers.resume.Queue", FakeQueue)
    monkeypatch.setattr("instamagic.workers.resume.Redis", SimpleNamespace(from_url=lambda _url: object()))
    monkeypatch.setattr(
        "instamagic.workers.resume.Job",
        SimpleNamespace(fetch=lambda rq_id, connection=None: jobs[rq_id]),
    )
    return jobs


def test_resume_marks_interrupted_and_enqueues_once(app_env, monkeypatch) -> None:
    db = session_factory()()
    library = Library(name="Archive", root_path=str(app_env["library"]))
    db.add(library)
    db.flush()
    stale_ids = []
    for status in (JobStatus.RUNNING.value, JobStatus.QUEUED.value):
        job = ProcessingJob(kind="scan", status=status, library_id=library.id)
        db.add(job)
        db.flush()
        stale_ids.append(job.id)
    db.commit()
    library_id = library.id
    db.close()

    rq_jobs = _fake_rq(monkeypatch)
    assert requeue_interrupted_scans("redis://localhost/0") == 1
    assert requeue_interrupted_scans("redis://localhost/0") == 0
    assert len(rq_jobs) == 1
    (enqueued,) = rq_jobs.values()
    assert enqueued.func_name == SCAN_FUNC
    assert enqueued.args[0] == str(library_id)

    db = session_factory()()
    for job_id in stale_ids:
        assert db.get(ProcessingJob, job_id).status == JobStatus.INTERRUPTED.value
    fresh = db.get(ProcessingJob, UUID(enqueued.args[1]))
    assert fresh is not None
    assert fresh.status == JobStatus.QUEUED.value
    active = db.scalars(
        select(ProcessingJob).where(ProcessingJob.status.in_((JobStatus.RUNNING.value, JobStatus.QUEUED.value)))
    ).all()
    assert [job.id for job in active] == [fresh.id]
    db.close()


def test_rescan_skips_unchanged_files_without_hashing(client, app_env, monkeypatch) -> None:
    library = app_env["library"]
    write_jpeg(library / "s1" / "a.jpg", color=(10, 20, 30))
    write_jpeg(library / "s1" / "b.jpg", color=(200, 20, 30))
    login(client)
    created = client.post(
        "/api/libraries",
        json={"name": "Archive", "root_path": str(library), "confirmed": True, "statement": STATEMENT},
    )
    library_id = created.json()["id"]
    assert client.post(f"/api/libraries/{library_id}/scan").json()["status"] == "succeeded"

    def no_hashing(_path):
        raise AssertionError("unchanged files must not be re-hashed")

    monkeypatch.setattr("instamagic.services.pipeline.file_sha256", no_hashing)
    assert client.post(f"/api/libraries/{library_id}/scan").json()["status"] == "succeeded"
    job = client.get("/api/jobs", params={"library_id": library_id}).json()[0]
    assert job["files_seen"] == 2
    assert job["files_skipped"] == 2
    assert job["files_processed"] == 0


def test_scan_reuses_active_job(client, app_env) -> None:
    login(client)
    created = client.post(
        "/api/libraries",
        json={"name": "Archive", "root_path": str(app_env["library"]), "confirmed": True, "statement": STATEMENT},
    )
    library_id = UUID(created.json()["id"])
    db = session_factory()()
    job = ProcessingJob(
        id=uuid4(),
        kind="scan",
        status=JobStatus.RUNNING.value,
        library_id=library_id,
        detail="still going",
    )
    db.add(job)
    db.commit()
    job_id = str(job.id)
    db.close()

    again = client.post(f"/api/libraries/{library_id}/scan")
    assert again.status_code == 202
    assert again.json() == {"job_id": job_id, "status": JobStatus.RUNNING.value}
