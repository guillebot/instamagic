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
OAUTH_SCOPES = (
    "instagram_basic,instagram_content_publish,pages_show_list,pages_read_engagement"
)


@dataclass(frozen=True)
class OAuthTokens:
    access_token: str
    business_account_id: str
    page_id: str | None = None


def oauth_redirect_uri(settings: Settings) -> str:
    explicit = settings.meta_oauth_redirect_uri.strip()
    if explicit:
        return explicit.rstrip("/")
    base = settings.resolve_public_base_url().rstrip("/")
    return f"{base}/api/instagram/oauth/callback"


def oauth_available(settings: Settings) -> bool:
    return bool(settings.meta_app_id.strip() and settings.meta_app_secret.strip())


def build_authorize_url(settings: Settings) -> tuple[str, str]:
    if not oauth_available(settings):
        raise NotConfigured(
            "Meta OAuth is not configured. Set META_APP_ID and META_APP_SECRET on the host."
        )
    state = secrets.token_urlsafe(24)
    signed_state = sign_payload(f"ig-oauth|{state}", settings)
    params = {
        "client_id": settings.meta_app_id.strip(),
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


def exchange_code_for_credentials(settings: Settings, *, code: str) -> OAuthTokens:
    if not oauth_available(settings):
        raise NotConfigured("Meta OAuth is not configured.")
    version = settings.instagram_graph_version.strip() or "v21.0"
    base = f"https://graph.facebook.com/{version}"
    redirect_uri = oauth_redirect_uri(settings)
    with httpx.Client(timeout=45.0) as client:
        short = client.get(
            f"{base}/oauth/access_token",
            params={
                "client_id": settings.meta_app_id.strip(),
                "client_secret": settings.meta_app_secret.strip(),
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
                "client_id": settings.meta_app_id.strip(),
                "client_secret": settings.meta_app_secret.strip(),
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
