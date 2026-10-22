"""Persist Instagram Graph credentials + cached account profile in the DB."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from instamagic.config import Settings
from instamagic.models import InstagramConnection
from instamagic.services.instagram.client import (
    FakeInstagramClient,
    GraphInstagramClient,
    InstagramAccountDetails,
    InstagramClient,
    NotConfiguredInstagramClient,
    mask_access_token,
)
from instamagic.services.instagram.oauth import META_APP_DOCS_URL, OAUTH_SCOPES, oauth_redirect_uri
from instamagic.services.secret_box import open_sealed, seal
from sqlalchemy.orm import Session

CONNECTION_ID = "default"


@dataclass(frozen=True)
class ResolvedCredentials:
    access_token: str
    business_account_id: str
    source: str  # env | database | fake


def get_connection(session: Session) -> InstagramConnection | None:
    return session.get(InstagramConnection, CONNECTION_ID)


def resolve_credentials(settings: Settings, session: Session | None) -> ResolvedCredentials | None:
    if settings.instagram_provider == "fake":
        return ResolvedCredentials(access_token="fake", business_account_id="fake", source="fake")
    env_token = settings.instagram_access_token.strip()
    env_account = settings.instagram_business_account_id.strip()
    if env_token and env_account:
        return ResolvedCredentials(
            access_token=env_token,
            business_account_id=env_account,
            source="env",
        )
    if session is None:
        return None
    row = get_connection(session)
    if row is None or not row.access_token_ciphertext.strip() or not row.business_account_id.strip():
        return None
    try:
        token = open_sealed(row.access_token_ciphertext, app_secret=settings.app_secret)
    except ValueError:
        return None
    if not token:
        return None
    return ResolvedCredentials(
        access_token=token,
        business_account_id=row.business_account_id.strip(),
        source="database",
    )


def build_instagram_client(settings: Settings, session: Session | None = None) -> InstagramClient:
    if settings.instagram_provider == "fake":
        return FakeInstagramClient()
    creds = resolve_credentials(settings, session)
    if creds is None:
        return NotConfiguredInstagramClient()
    return GraphInstagramClient(
        access_token=creds.access_token,
        business_account_id=creds.business_account_id,
        graph_version=settings.instagram_graph_version,
    )


def save_credentials(
    session: Session,
    settings: Settings,
    *,
    access_token: str,
    business_account_id: str,
) -> InstagramConnection:
    token = access_token.strip()
    account_id = business_account_id.strip()
    if not token or not account_id:
        raise ValueError("Access token and Instagram business account id are required.")
    if len(token) > 4096:
        raise ValueError("Access token is too long.")
    if len(account_id) > 64 or any(ch in account_id for ch in "/\\\x00\n\r\t "):
        raise ValueError("Instagram business account id is not valid.")
    row = get_connection(session)
    if row is None:
        row = InstagramConnection(id=CONNECTION_ID)
        session.add(row)
    row.access_token_ciphertext = seal(token, app_secret=settings.app_secret)
    row.business_account_id = account_id
    row.updated_at = datetime.now(UTC)
    session.commit()
    session.refresh(row)
    return row


def clear_credentials(session: Session) -> None:
    row = get_connection(session)
    if row is None:
        return
    session.delete(row)
    session.commit()


def save_account_profile(session: Session, details: InstagramAccountDetails) -> InstagramConnection | None:
    row = get_connection(session)
    if row is None:
        # Env-only credentials: still cache a profile row without a token ciphertext.
        row = InstagramConnection(
            id=CONNECTION_ID,
            access_token_ciphertext="",
            business_account_id=details.id,
        )
        session.add(row)
    row.account_json = json.dumps(details.as_dict(), separators=(",", ":"))
    row.account_fetched_at = datetime.now(UTC)
    if not row.business_account_id.strip():
        row.business_account_id = details.id
    row.updated_at = datetime.now(UTC)
    session.commit()
    session.refresh(row)
    return row


def load_cached_account(session: Session) -> dict[str, Any] | None:
    row = get_connection(session)
    if row is None or not row.account_json:
        return None
    try:
        payload = json.loads(row.account_json)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def connection_status_fields(
    settings: Settings,
    session: Session,
    *,
    configured: bool,
) -> dict[str, object]:
    creds = resolve_credentials(settings, session)
    row = get_connection(session)
    cached = load_cached_account(session)
    token_masked: str | None
    business_id: str | None
    source = creds.source if creds else "none"
    if settings.instagram_provider == "fake":
        token_masked = "fake-provider"
        business_id = "fake-ig-user-001"
    elif creds and creds.source == "env":
        token_masked = mask_access_token(settings.instagram_access_token)
        business_id = settings.instagram_business_account_id.strip() or None
    elif creds and creds.source == "database":
        token_masked = mask_access_token(creds.access_token)
        business_id = creds.business_account_id
    else:
        token_masked = None
        business_id = (row.business_account_id.strip() if row else "") or None

    oauth_ready = bool(settings.meta_app_id.strip() and settings.meta_app_secret.strip())
    return {
        "configured": configured,
        "provider": settings.instagram_provider,
        "credential_source": source,
        "auto_publish": settings.auto_publish,
        "public_base_url": settings.resolve_public_base_url(),
        "graph_version": settings.instagram_graph_version,
        "token_configured": bool(creds) and source != "none",
        "token_masked": token_masked,
        "business_account_id": business_id,
        "business_account_id_configured": bool(business_id) or settings.instagram_provider == "fake",
        "oauth_available": oauth_ready,
        "oauth_redirect_uri": oauth_redirect_uri(settings),
        "oauth_scopes": OAUTH_SCOPES,
        "meta_docs_url": META_APP_DOCS_URL,
        "account": cached,
        "account_fetched_at": row.account_fetched_at.isoformat() if row and row.account_fetched_at else None,
        "can_edit_database_credentials": settings.instagram_provider != "fake"
        and not (
            settings.instagram_access_token.strip() and settings.instagram_business_account_id.strip()
        ),
        "env_overrides_database": bool(
            settings.instagram_access_token.strip() and settings.instagram_business_account_id.strip()
        )
        and settings.instagram_provider != "fake",
    }
