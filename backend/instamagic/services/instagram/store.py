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
from instamagic.services.instagram.oauth import (
    META_APP_DOCS_URL,
    META_APPS_DASHBOARD_URL,
    META_CREATE_APP_DOCS_URL,
    OAUTH_SCOPES,
    MetaAppCredentials,
    oauth_available,
    oauth_redirect_uri,
)
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


def _ensure_row(session: Session) -> InstagramConnection:
    row = get_connection(session)
    if row is None:
        row = InstagramConnection(id=CONNECTION_ID)
        session.add(row)
    return row


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


def resolve_meta_app_credentials(
    settings: Settings,
    session: Session | None,
) -> MetaAppCredentials | None:
    """Prefer sealed DB Meta app credentials; fall back to host env."""
    if session is not None:
        row = get_connection(session)
        if row is not None:
            app_id = (row.meta_app_id or "").strip()
            cipher = (row.meta_app_secret_ciphertext or "").strip()
            if app_id and cipher:
                try:
                    secret = open_sealed(cipher, app_secret=settings.app_secret)
                except ValueError:
                    secret = ""
                if secret:
                    return MetaAppCredentials(app_id=app_id, app_secret=secret, source="database")
    env_id = settings.meta_app_id.strip()
    env_secret = settings.meta_app_secret.strip()
    if env_id and env_secret:
        return MetaAppCredentials(app_id=env_id, app_secret=env_secret, source="env")
    return None


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
    row = _ensure_row(session)
    row.access_token_ciphertext = seal(token, app_secret=settings.app_secret)
    row.business_account_id = account_id
    row.updated_at = datetime.now(UTC)
    session.commit()
    session.refresh(row)
    return row


def save_meta_app_credentials(
    session: Session,
    settings: Settings,
    *,
    app_id: str,
    app_secret: str,
) -> InstagramConnection:
    """Seal Meta App Secret with APP_SECRET. Never log plaintext app_secret."""
    cleaned_id = app_id.strip()
    cleaned_secret = app_secret.strip()
    if not cleaned_id or not cleaned_secret:
        raise ValueError("Meta App ID and App Secret are required.")
    if len(cleaned_id) > 64 or any(ch in cleaned_id for ch in "/\\\x00\n\r\t "):
        raise ValueError("Meta App ID is not valid.")
    if len(cleaned_secret) > 512 or any(ch in cleaned_secret for ch in "\x00\n\r"):
        raise ValueError("Meta App Secret is not valid.")
    row = _ensure_row(session)
    row.meta_app_id = cleaned_id
    row.meta_app_secret_ciphertext = seal(cleaned_secret, app_secret=settings.app_secret)
    row.updated_at = datetime.now(UTC)
    session.commit()
    session.refresh(row)
    return row


def clear_credentials(session: Session) -> None:
    """Clear sealed Page token + cached profile. Keeps Meta App ID/Secret."""
    row = get_connection(session)
    if row is None:
        return
    row.access_token_ciphertext = ""
    row.business_account_id = ""
    row.account_json = None
    row.account_fetched_at = None
    row.updated_at = datetime.now(UTC)
    # Drop empty shell rows that have no Meta app either.
    if not (row.meta_app_id or "").strip() and not (row.meta_app_secret_ciphertext or "").strip():
        session.delete(row)
    session.commit()


def clear_meta_app_credentials(session: Session) -> None:
    """Clear sealed Meta App credentials. Keeps Instagram Page token if present."""
    row = get_connection(session)
    if row is None:
        return
    row.meta_app_id = ""
    row.meta_app_secret_ciphertext = ""
    row.updated_at = datetime.now(UTC)
    if not row.access_token_ciphertext.strip() and not row.business_account_id.strip() and not row.account_json:
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
    meta = resolve_meta_app_credentials(settings, session)
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

    oauth_ready = oauth_available(meta)
    meta_app_id_display: str | None = None
    if meta is not None:
        meta_app_id_display = meta.app_id
    elif row is not None and (row.meta_app_id or "").strip():
        meta_app_id_display = row.meta_app_id.strip()

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
        "meta_apps_dashboard_url": META_APPS_DASHBOARD_URL,
        "meta_create_app_docs_url": META_CREATE_APP_DOCS_URL,
        "meta_app_id": meta_app_id_display,
        "meta_app_secret_configured": oauth_ready,
        "meta_app_source": meta.source if meta else "none",
        "can_edit_meta_app": settings.instagram_provider != "fake",
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
