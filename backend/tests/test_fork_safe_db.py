"""Fork safety for SQLAlchemy connection pools (RQ work-horse)."""

from __future__ import annotations

import os

import pytest
from sqlalchemy import text


@pytest.mark.skipif(not hasattr(os, "fork"), reason="POSIX fork required")
def test_fork_parent_and_child_can_query(app_env) -> None:
    from instamagic.db import get_engine, reset_engine

    reset_engine()
    engine = get_engine()
    parent_conn = engine.connect()
    parent_conn.execute(text("SELECT 1"))

    child_status = 1
    if os.fork() == 0:
        try:
            child_engine = get_engine()
            for _ in range(5):
                with child_engine.connect() as conn:
                    conn.execute(text("SELECT 1"))
            child_status = 0
        except Exception:
            child_status = 2
        os._exit(child_status)

    _, status = os.wait()
    exit_code = os.waitstatus_to_exitcode(status)
    assert exit_code == 0

    for _ in range(5):
        parent_conn.execute(text("SELECT 1"))
    parent_conn.close()
    reset_engine()


@pytest.mark.skipif(not hasattr(os, "fork"), reason="POSIX fork required")
def test_fork_child_disposes_inherited_pool(app_env) -> None:
    from instamagic.db import get_engine, reset_engine

    reset_engine()
    engine = get_engine()
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    pool = engine.pool
    assert pool.checkedout() == 0

    if os.fork() == 0:
        try:
            child_engine = get_engine()
            assert child_engine is engine
            # Inherited checked-out connections must not survive in the child.
            assert child_engine.pool.checkedout() == 0
            with child_engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            os._exit(0)
        except Exception:
            os._exit(1)

    _, status = os.wait()
    assert os.waitstatus_to_exitcode(status) == 0
    reset_engine()


def test_scheduler_tick_retries_after_operational_error(monkeypatch) -> None:
    from instamagic.workers import scheduler_loop

    calls = {"count": 0}

    def flaky_job() -> int:
        calls["count"] += 1
        if calls["count"] == 1:
            from sqlalchemy.exc import OperationalError

            raise OperationalError("SELECT 1", {}, Exception("server closed the connection unexpectedly"))
        return 0

    disposed = {"called": False}

    def fake_dispose(**_kwargs) -> None:
        disposed["called"] = True

    monkeypatch.setattr(scheduler_loop, "run_scheduler_job", flaky_job)
    monkeypatch.setattr(scheduler_loop, "dispose_engine", fake_dispose)

    assert scheduler_loop._run_scheduler_tick() == 0
    assert calls["count"] == 2
    assert disposed["called"]


def test_mark_scan_job_failed_after_connection_error(app_env) -> None:
    from instamagic.db import session_factory
    from instamagic.models import JobStatus, Library, ProcessingJob
    from instamagic.workers.tasks import _SCAN_DB_FAILURE_MESSAGE, _mark_scan_job_failed

    session = session_factory()()
    library = Library(name="Archive", root_path=str(app_env["library"]))
    session.add(library)
    session.flush()
    job = ProcessingJob(kind="scan", status=JobStatus.RUNNING.value, library_id=library.id)
    session.add(job)
    session.commit()
    job_id = str(job.id)
    session.close()

    _mark_scan_job_failed(job_id, _SCAN_DB_FAILURE_MESSAGE)

    session = session_factory()()
    finished = session.get(ProcessingJob, job.id)
    assert finished is not None
    assert finished.status == JobStatus.FAILED.value
    assert finished.error == _SCAN_DB_FAILURE_MESSAGE
    assert finished.finished_at is not None
    session.close()
