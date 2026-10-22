"""Optional Meta OAuth for Instagram Graph (Business/Creator + Page)."""

from __future__ import annotations

import json
import secrets
import time
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx
from instamagic.auth import sign_payload, verify_signed_payload
from instamagic.config import Settings
from instamagic.errors import NotAllowed, NotConfigured
from instamagic.services.instagram.graph_constants import DEFAULT_INSTAGRAM_GRAPH_VERSION, OAUTH_SCOPES

META_APP_DOCS_URL = (
    "https://developers.facebook.com/docs/instagram-platform/instagram-api-with-facebook-login"
)
META_APPS_DASHBOARD_URL = "https://developers.facebook.com/apps/"
META_CREATE_APP_DOCS_URL = (
    "https://developers.facebook.com/docs/instagram-platform/create-an-instagram-app"
)

PAGE_FIELDS = "id,name,access_token,instagram_business_account{id,username}"


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
    page_name: str | None = None
    ig_username: str | None = None


@dataclass(frozen=True)
class OAuthPageOption:
    page_id: str
    page_name: str
    ig_business_account_id: str
    ig_username: str | None


@dataclass(frozen=True)
class OAuthPageSelectionRequired:
    options: tuple[OAuthPageOption, ...]
    user_access_token: str


def oauth_redirect_uri(settings: Settings) -> str:
    explicit = settings.meta_oauth_redirect_uri.strip()
    if explicit:
        return explicit.rstrip("/")
    base = settings.frontend_origin.strip().rstrip("/")
    return f"{base}/api/instagram/oauth/callback"


def oauth_available(meta: MetaAppCredentials | None) -> bool:
    return meta is not None and bool(meta.app_id.strip() and meta.app_secret.strip())


def _graph_version(settings: Settings) -> str:
    return settings.instagram_graph_version.strip() or DEFAULT_INSTAGRAM_GRAPH_VERSION


def build_authorize_url(settings: Settings, meta: MetaAppCredentials) -> tuple[str, str, str]:
    """Return authorize URL, signed state, and browser-bound nonce (for cookie)."""
    if not oauth_available(meta):
        raise NotConfigured(
            "Meta OAuth is not configured. Save Meta App ID and App Secret on /instagram, "
            "or set META_APP_ID and META_APP_SECRET on the host."
        )
    nonce = secrets.token_urlsafe(24)
    signed_state = sign_payload(f"ig-oauth|{nonce}", settings)
    params: dict[str, str] = {
        "client_id": meta.app_id.strip(),
        "redirect_uri": oauth_redirect_uri(settings),
        "state": signed_state,
        "response_type": "code",
    }
    config_id = settings.meta_oauth_config_id.strip()
    if config_id:
        params["config_id"] = config_id
    else:
        params["scope"] = OAUTH_SCOPES
    version = _graph_version(settings)
    url = f"https://www.facebook.com/{version}/dialog/oauth?{urlencode(params)}"
    return url, signed_state, nonce


def verify_oauth_state(state: str, settings: Settings, *, nonce_cookie: str | None) -> None:
    payload = verify_signed_payload(state, settings)
    if payload is None or not payload.startswith("ig-oauth|"):
        raise NotAllowed("Invalid or expired OAuth state.")
    parts = payload.split("|", 1)
    if len(parts) != 2 or parts[0] != "ig-oauth":
        raise NotAllowed("Invalid or expired OAuth state.")
    cookie = (nonce_cookie or "").strip()
    if not cookie or cookie != parts[1]:
        raise NotAllowed("OAuth state does not match this browser session. Start Connect again.")


def exchange_code_for_credentials(
    settings: Settings,
    meta: MetaAppCredentials,
    *,
    code: str,
) -> OAuthTokens:
    tokens, selection, _user = exchange_code_for_credentials_with_pages(settings, meta, code=code)
    if selection is not None:
        names = ", ".join(
            f"{o.page_name} (@{o.ig_username or '?'})" for o in selection.options[:5]
        )
        extra = "" if len(selection.options) <= 5 else f" (+{len(selection.options) - 5} more)"
        raise NotConfigured(
            "Multiple Facebook Pages with Instagram accounts were found. "
            f"Set INSTAGRAM_EXPECTED_USERNAME (e.g. foto.sch) or reconnect and pick a Page. "
            f"Candidates: {names}{extra}."
        )
    if tokens is None:
        raise NotConfigured("OAuth did not return Instagram credentials.")
    return tokens


def exchange_code_for_credentials_with_pages(
    settings: Settings,
    meta: MetaAppCredentials,
    *,
    code: str,
) -> tuple[OAuthTokens | None, OAuthPageSelectionRequired | None, str | None]:
    if not oauth_available(meta):
        raise NotConfigured("Meta OAuth is not configured.")
    version = _graph_version(settings)
    base = f"https://graph.facebook.com/{version}"
    redirect_uri = oauth_redirect_uri(settings)
    app_id = meta.app_id.strip()
    app_secret = meta.app_secret.strip()
    with httpx.Client(timeout=45.0) as client:
        user_token = _exchange_user_token(client, base, app_id, app_secret, redirect_uri, code)
        page_rows = _fetch_all_pages(client, base, user_token)
        candidates = [page for page in page_rows if _page_has_ig(page)]
        if not candidates:
            if not page_rows:
                raise NotConfigured(
                    "No Facebook Pages found for this Meta user. Link a Professional Instagram "
                    "account to a Page, then retry OAuth."
                )
            raise NotConfigured(
                "Pages were found but none have a linked Instagram business account. "
                "Convert the IG account to Professional and link it to a Facebook Page."
            )
        options = _options_from_pages(candidates)
        picked = _pick_page_option(settings, options)
        if isinstance(picked, OAuthPageSelectionRequired):
            return None, OAuthPageSelectionRequired(options=options, user_access_token=user_token), user_token
        for page in candidates:
            ig = page.get("instagram_business_account")
            ig_id = ig.get("id") if isinstance(ig, dict) else None
            if str(ig_id) == picked.ig_business_account_id:
                return _tokens_from_page_row(page), None, user_token
        raise NotConfigured("Could not resolve Page token for the selected Instagram account.")


def complete_oauth_with_page(
    settings: Settings,
    *,
    user_access_token: str,
    page_id: str,
) -> OAuthTokens:
    version = _graph_version(settings)
    base = f"https://graph.facebook.com/{version}"
    with httpx.Client(timeout=45.0) as client:
        for page in _fetch_all_pages(client, base, user_access_token):
            if str(page.get("id") or "") == page_id.strip() and _page_has_ig(page):
                return _tokens_from_page_row(page)
    raise NotConfigured("Selected Facebook Page was not found. Start Connect again.")


def _exchange_user_token(
    client: httpx.Client,
    base: str,
    app_id: str,
    app_secret: str,
    redirect_uri: str,
    code: str,
) -> str:
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
    return str(long_payload.get("access_token") or short_token).strip()


def _fetch_all_pages(client: httpx.Client, base: str, user_access_token: str) -> list[dict]:
    rows: list[dict] = []
    url: str | None = f"{base}/me/accounts"
    params: dict[str, str] | None = {
        "fields": PAGE_FIELDS,
        "access_token": user_access_token,
        "limit": "50",
    }
    while url:
        response = client.get(url, params=params)
        payload = _json_or_raise(response, action="list Pages")
        data = payload.get("data")
        if isinstance(data, list):
            rows.extend(item for item in data if isinstance(item, dict))
        paging = payload.get("paging") if isinstance(payload.get("paging"), dict) else {}
        next_url = paging.get("next") if isinstance(paging, dict) else None
        url = str(next_url) if next_url else None
        params = None
    return rows


def _options_from_pages(candidates: list[dict]) -> tuple[OAuthPageOption, ...]:
    options: list[OAuthPageOption] = []
    for page in candidates:
        ig = page.get("instagram_business_account")
        ig_id = ig.get("id") if isinstance(ig, dict) else None
        page_id = str(page.get("id") or "").strip()
        if not ig_id or not page_id:
            continue
        ig_username = None
        if isinstance(ig, dict):
            ig_username = str(ig.get("username") or "").strip() or None
        options.append(
            OAuthPageOption(
                page_id=page_id,
                page_name=str(page.get("name") or "").strip() or page_id,
                ig_business_account_id=str(ig_id),
                ig_username=ig_username,
            )
        )
    return tuple(options)


def _pick_page_option(
    settings: Settings,
    options: tuple[OAuthPageOption, ...],
) -> OAuthPageOption | OAuthPageSelectionRequired:
    if len(options) == 1:
        return options[0]
    expected_username = settings.instagram_expected_username.strip().lstrip("@").lower()
    expected_ig_id = settings.instagram_business_account_id.strip()
    matches: list[OAuthPageOption] = []
    for option in options:
        if expected_ig_id and option.ig_business_account_id == expected_ig_id:
            matches.append(option)
        elif expected_username and (option.ig_username or "").lower() == expected_username:
            matches.append(option)
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        return OAuthPageSelectionRequired(options=tuple(matches), user_access_token="")
    return OAuthPageSelectionRequired(options=options, user_access_token="")


def _tokens_from_page_row(page: dict) -> OAuthTokens:
    ig = page.get("instagram_business_account")
    ig_id = ig.get("id") if isinstance(ig, dict) else None
    page_token = str(page.get("access_token") or "").strip()
    if not ig_id or not page_token:
        raise NotConfigured("Selected Page is missing Instagram linkage or Page token.")
    ig_username = None
    if isinstance(ig, dict):
        ig_username = str(ig.get("username") or "").strip() or None
    return OAuthTokens(
        access_token=page_token,
        business_account_id=str(ig_id),
        page_id=str(page.get("id")) if page.get("id") else None,
        page_name=str(page.get("name") or "").strip() or None,
        ig_username=ig_username,
    )


def _page_has_ig(page: dict) -> bool:
    ig = page.get("instagram_business_account")
    ig_id = ig.get("id") if isinstance(ig, dict) else None
    page_token = str(page.get("access_token") or "").strip()
    return bool(ig_id and page_token)


def seal_oauth_pending(
    settings: Settings,
    *,
    user_access_token: str,
    options: tuple[OAuthPageOption, ...],
    ttl_seconds: int = 900,
) -> str:
    payload = {
        "expires": int(time.time()) + ttl_seconds,
        "user_token": user_access_token,
        "options": [
            {
                "page_id": o.page_id,
                "page_name": o.page_name,
                "ig_business_account_id": o.ig_business_account_id,
                "ig_username": o.ig_username,
            }
            for o in options
        ],
    }
    return sign_payload(
        f"ig-oauth-pending|{json.dumps(payload, separators=(',', ':'))}",
        settings,
        ttl_seconds=ttl_seconds,
    )


def open_oauth_pending(settings: Settings, token: str) -> dict:
    raw = verify_signed_payload(token, settings)
    if raw is None or not raw.startswith("ig-oauth-pending|"):
        raise NotAllowed("OAuth page selection expired. Start Connect again.")
    try:
        payload = json.loads(raw.split("|", 1)[1])
    except (json.JSONDecodeError, IndexError) as exc:
        raise NotAllowed("OAuth page selection is invalid.") from exc
    if not isinstance(payload, dict):
        raise NotAllowed("OAuth page selection is invalid.")
    expires = int(payload.get("expires") or 0)
    if expires < int(time.time()):
        raise NotAllowed("OAuth page selection expired. Start Connect again.")
    return payload


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
