"""Engage page API: a daily list the operator works through by hand in Instagram.

Nothing here sends anything to other accounts (no likes, follows, or comments via the API).
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Request
from instamagic.api.deps import db_dep, require_session, settings_dep
from instamagic.config import Settings
from instamagic.errors import NotAllowed, NotFound
from instamagic.models import EngageTarget
from instamagic.services.engage.service import (
    overview,
    run_engage,
    running_run,
    set_target_status,
    settings_out,
    target_out,
    update_settings,
)
from instamagic.services.factory import build_growth, build_instagram
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

router = APIRouter()


class EngageTargetPatch(BaseModel):
    status: str = Field(pattern="^(open|done|skipped)$")


class EngageSettingsPatch(BaseModel):
    enabled: bool | None = None
    daily_target: int | None = Field(default=None, ge=5, le=30)
    account_cooldown_days: int | None = Field(default=None, ge=1, le=60)
    run_hour_local: int | None = Field(default=None, ge=0, le=23)
    hashtags_per_run: int | None = Field(default=None, ge=1, le=5)
    seed_hashtags: list[str] | None = Field(default=None, max_length=8)


def _client(request: Request, settings: Settings, session: Session):
    client = getattr(request.app.state, "instagram", None)
    if client is None or not client.configured():
        client = build_instagram(settings, session)
    return client


@router.get("/api/engage")
def engage_overview(
    request: Request,
    _auth: None = Depends(require_session),
    settings: Settings = Depends(settings_dep),
    session: Session = db_dep,
) -> dict[str, object]:
    return overview(session, _client(request, settings, session))


@router.post("/api/engage/refresh", status_code=202)
def engage_refresh(
    request: Request,
    _auth: None = Depends(require_session),
    settings: Settings = Depends(settings_dep),
    session: Session = db_dep,
) -> dict[str, object]:
    """Non-blocking: the worker-io social lane builds the list. Inline mode (tests/dev) runs now."""
    if running_run(session) is not None:
        return {"queued": False, "running": True}
    if settings.pipeline_mode == "inline":
        run_engage(
            session,
            _client(request, settings, session),
            build_growth(settings),
            account_voice=settings.account_voice,
            trigger="manual",
        )
        return {"queued": False, "running": False, "ran": True}
    from instamagic.workers.social_loop import request_engage_refresh

    if not request_engage_refresh(settings.redis_url):
        raise NotAllowed("Could not reach the worker queue. Try again in a minute.")
    return {"queued": True, "running": False}


@router.patch("/api/engage/targets/{target_id}")
def engage_mark(
    target_id: UUID,
    body: EngageTargetPatch,
    _auth: None = Depends(require_session),
    session: Session = db_dep,
) -> dict[str, object]:
    row = session.get(EngageTarget, target_id)
    if row is None:
        raise NotFound("Engage target was not found.")
    return target_out(set_target_status(session, row, body.status))


@router.patch("/api/engage/settings")
def engage_update_settings(
    body: EngageSettingsPatch,
    _auth: None = Depends(require_session),
    session: Session = db_dep,
) -> dict[str, object]:
    row = update_settings(session, **body.model_dump(exclude_none=True))
    return settings_out(row)
