"""Optional Meta OAuth for Instagram Graph (Business/Creator + Page)."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx
from instamagic.auth import sign_payload, verify_signed_payload
from instamagic.config import Settings
from instamagic.errors import NotAllowed, NotConfigured

# Scopes needed to list Pages + publish via IG Content Publishing API.
# Confirm current names in Meta's Instagram Graph docs when the app is created.
# Must be a plain comma-separated string (not a tuple) for urlencode.
OAUTH_SCOPES = "instagram_basic,instagram_content_publish,pages_show_list,pages_read_engagement"

META_APP_DOCS_URL = (
    "https://developers.facebook.com/docs/instagram-platform/instagram-api-with-facebook-login"
)
META_APPS_DASHBOARD_URL = "https://developers.facebook.com/apps/"
META_CREATE_APP_DOCS_URL = (
    "https://developers.facebook.com/docs/instagram-platform/create-an-instagram-app"
)


@dataclass(frozen=True)
class MetaAppCredentials:
    """Resolved Meta app client id + secret. Never log ``app_secret``."""

    app_id: str
    app_secret: str
    source: str  # database | env


@dataclass(frozen=True)
class OAuthTokens:
    access_token: str
    business_account_id: str
    page_id: str | None = None


def oauth_redirect_uri(settings: Settings) -> str:
    """Callback URL registered in the Meta app.

    Prefer ``META_OAUTH_REDIRECT_URI``, else ``FRONTEND_ORIGIN`` (the Traefik /
    browser host). Do **not** use ``PUBLIC_BASE_URL`` here — that is for Meta's
    crawler to fetch publish JPEGs and may be a tunnel distinct from login.
    """
    explicit = settings.meta_oauth_redirect_uri.strip()
    if explicit:
        return explicit.rstrip("/")
    base = settings.frontend_origin.strip().rstrip("/")
    return f"{base}/api/instagram/oauth/callback"


def oauth_available(meta: MetaAppCredentials | None) -> bool:
    return meta is not None and bool(meta.app_id.strip() and meta.app_secret.strip())


def build_authorize_url(settings: Settings, meta: MetaAppCredentials) -> tuple[str, str]:
    if not oauth_available(meta):
        raise NotConfigured(
            "Meta OAuth is not configured. Save Meta App ID and App Secret on /instagram, "
            "or set META_APP_ID and META_APP_SECRET on the host."
        )
    state = secrets.token_urlsafe(24)
    signed_state = sign_payload(f"ig-oauth|{state}", settings)
    params = {
        "client_id": meta.app_id.strip(),
        "redirect_uri": oauth_redirect_uri(settings),
        "state": signed_state,
        "scope": OAUTH_SCOPES,
        "response_type": "code",
    }
    version = settings.instagram_graph_version.strip() or "v21.0"
    url = f"https://www.facebook.com/{version}/dialog/oauth?{urlencode(params)}"
    return url, signed_state


def verify_oauth_state(state: str, settings: Settings) -> None:
    payload = verify_signed_payload(state, settings)
    if payload is None or not payload.startswith("ig-oauth|"):
        raise NotAllowed("Invalid or expired OAuth state.")


def exchange_code_for_credentials(
    settings: Settings,
    meta: MetaAppCredentials,
    *,
    code: str,
) -> OAuthTokens:
    if not oauth_available(meta):
        raise NotConfigured("Meta OAuth is not configured.")
    version = settings.instagram_graph_version.strip() or "v21.0"
    base = f"https://graph.facebook.com/{version}"
    redirect_uri = oauth_redirect_uri(settings)
    app_id = meta.app_id.strip()
    app_secret = meta.app_secret.strip()
    with httpx.Client(timeout=45.0) as client:
        short = client.get(
            f"{base}/oauth/access_token",
            params={
                "client_id": app_id,
                "client_secret": app_secret,
                "redirect_uri": redirect_uri,
                "code": code,
            },
        )
        short_payload = _json_or_raise(short, action="OAuth code exchange")
        short_token = str(short_payload.get("access_token") or "").strip()
        if not short_token:
            raise NotConfigured("Meta did not return an access token.")

        long_lived = client.get(
            f"{base}/oauth/access_token",
            params={
                "grant_type": "fb_exchange_token",
                "client_id": app_id,
                "client_secret": app_secret,
                "fb_exchange_token": short_token,
            },
        )
        long_payload = _json_or_raise(long_lived, action="long-lived token exchange")
        user_token = str(long_payload.get("access_token") or short_token).strip()

        pages = client.get(
            f"{base}/me/accounts",
            params={
                "fields": "id,name,access_token,instagram_business_account",
                "access_token": user_token,
            },
        )
        pages_payload = _json_or_raise(pages, action="list Pages")
        data = pages_payload.get("data")
        if not isinstance(data, list) or not data:
            raise NotConfigured(
                "No Facebook Pages found for this Meta user. Link a Professional Instagram "
                "account to a Page, then retry OAuth."
            )
        for page in data:
            if not isinstance(page, dict):
                continue
            ig = page.get("instagram_business_account")
            ig_id = ig.get("id") if isinstance(ig, dict) else None
            page_token = str(page.get("access_token") or "").strip()
            if ig_id and page_token:
                return OAuthTokens(
                    access_token=page_token,
                    business_account_id=str(ig_id),
                    page_id=str(page.get("id")) if page.get("id") else None,
                )
        raise NotConfigured(
            "Pages were found but none have a linked Instagram business account. "
            "Convert the IG account to Professional and link it to a Facebook Page."
        )


def _json_or_raise(response: httpx.Response, *, action: str) -> dict:
    try:
        payload = response.json()
    except ValueError as exc:
        raise NotConfigured(f"Meta {action} returned non-JSON.") from exc
    if response.status_code >= 400:
        error = payload.get("error") if isinstance(payload, dict) else None
        message = "request failed"
        if isinstance(error, dict):
            message = str(error.get("message") or message)
        raise NotConfigured(f"Meta {action} failed: {message}")
    if not isinstance(payload, dict):
        raise NotConfigured(f"Meta {action} returned an unexpected payload.")
    return payload
