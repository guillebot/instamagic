from __future__ import annotations

from uuid import UUID

from instamagic.config import get_settings
from instamagic.db import session_factory
from instamagic.logging_config import get_logger
from instamagic.services.factory import build_classifier, build_face_detector
from instamagic.services.pipeline import run_scan

log = get_logger("worker.tasks")


def run_scan_job(library_id: str, job_id: str) -> None:
    settings = get_settings()
    session = session_factory()()
    try:
        run_scan(
            session,
            UUID(library_id),
            settings.derivatives_root,
            build_classifier(settings),
            build_face_detector(settings),
            UUID(job_id),
        )
        log.info("scan_finished", library_id=library_id, job_id=job_id)
    except Exception:
        log.info("scan_failed", library_id=library_id, job_id=job_id)
        raise
    finally:
        session.close()
