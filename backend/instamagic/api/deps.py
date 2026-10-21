from __future__ import annotations

from fastapi import Depends, HTTPException, Request
from instamagic.auth import COOKIE_NAME, session_is_valid
from instamagic.config import Settings, get_settings
from instamagic.db import get_db


def settings_dep() -> Settings:
    return get_settings()


def require_session(request: Request, settings: Settings = Depends(settings_dep)) -> None:
    token = request.cookies.get(COOKIE_NAME)
    if not session_is_valid(settings, token):
        raise HTTPException(status_code=401, detail="Login required.")


db_dep = Depends(get_db)
session_dep = Depends(require_session)
