from __future__ import annotations

from fastapi import Depends, HTTPException, Request
from instamagic.auth import (
    COOKIE_NAME,
    _is_private_or_loopback,
    client_ip,
    lan_auth_bypass,
    session_is_valid,
)
from instamagic.config import Settings, get_settings
from instamagic.db import get_db


def settings_dep() -> Settings:
    return get_settings()


def require_session(request: Request, settings: Settings = Depends(settings_dep)) -> None:
    token = request.cookies.get(COOKIE_NAME)
    if session_is_valid(settings, token):
        return
    if lan_auth_bypass(request, settings):
        return
    raise HTTPException(status_code=401, detail="Login required.")


def require_lan_session(request: Request, settings: Settings = Depends(settings_dep)) -> None:
    require_session(request, settings)
    if settings.environment == "local":
        return
    peer = client_ip(request)
    if not _is_private_or_loopback(peer):
        raise HTTPException(status_code=403, detail="Story preview is only available on the local network.")


db_dep = Depends(get_db)
session_dep = Depends(require_session)
