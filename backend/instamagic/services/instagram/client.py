"""Official Instagram Graph API Content Publishing client (Business/Creator + Page).

No unofficial mobile API. Tokens come from env only and must never be logged.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass
from typing import Any, Protocol

import httpx
from instamagic.errors import InstagramNotConfigured, NotConfigured
from instamagic.services.instagram.graph_constants import DEFAULT_INSTAGRAM_GRAPH_VERSION

# Fields Meta documents on IG User for Professional accounts (Graph Content Publishing).
ACCOUNT_DETAIL_FIELDS = (
    "id,username,name,profile_picture_url,followers_count,follows_count,"
    "media_count,account_type,biography,website"
)

_NOT_CONFIGURED_MSG = (
    "Instagram Graph API is not configured. Connect via Meta OAuth on /instagram, "
    "paste a Page token in the app, or set INSTAGRAM_ACCESS_TOKEN and "
    "INSTAGRAM_BUSINESS_ACCOUNT_ID in the host environment."
)

_CONTAINER_POLL_INTERVAL = 1.5
_CONTAINER_POLL_MAX_WAIT = 90.0


@dataclass(frozen=True)
class PublishResult:
    container_id: str
    media_id: str
    permalink: str | None = None


@dataclass(frozen=True)
class InstagramAccountDetails:
    id: str
    username: str | None = None
    name: str | None = None
    profile_picture_url: str | None = None
    followers_count: int | None = None
    follows_count: int | None = None
    media_count: int | None = None
    account_type: str | None = None
    biography: str | None = None
    website: str | None = None
    linked_facebook_page_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class InstagramClient(Protocol):
    def configured(self) -> bool: ...

    def publish_image(self, *, image_url: str, caption: str) -> PublishResult: ...

    def fetch_account_details(self) -> InstagramAccountDetails: ...


def mask_access_token(token: str) -> str | None:
    """Never return the full token. Empty → None; short → opaque; else last 4 only."""
    value = token.strip()
    if not value:
        return None
    if len(value) <= 8:
        return "••••••••"
    return f"••••…{value[-4:]}"


class NotConfiguredInstagramClient:
    def configured(self) -> bool:
        return False

    def publish_image(self, *, image_url: str, caption: str) -> PublishResult:
        del image_url, caption
        raise InstagramNotConfigured(_NOT_CONFIGURED_MSG)

    def fetch_account_details(self) -> InstagramAccountDetails:
        raise InstagramNotConfigured(_NOT_CONFIGURED_MSG)


class FakeInstagramClient:
    """Deterministic publisher for CI. Never calls Meta."""

    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []
        self.account_fetches: int = 0

    def configured(self) -> bool:
        return True

    def publish_image(self, *, image_url: str, caption: str) -> PublishResult:
        if not image_url.startswith(("http://", "https://")):
            raise NotConfigured("Fake Instagram publisher requires an http(s) image_url.")
        if not caption.strip():
            raise NotConfigured("Caption is required.")
        container_id = f"fake-container-{uuid.uuid4().hex[:12]}"
        media_id = f"fake-media-{uuid.uuid4().hex[:12]}"
        self.calls.append({"image_url": image_url, "caption": caption, "media_id": media_id})
        return PublishResult(
            container_id=container_id,
            media_id=media_id,
            permalink=f"https://www.instagram.com/p/{media_id}/",
        )

    def fetch_account_details(self) -> InstagramAccountDetails:
        self.account_fetches += 1
        return InstagramAccountDetails(
            id="fake-ig-user-001",
            username="instamagic_fixture",
            name="Instamagic Fixture",
            profile_picture_url="https://example.test/ig-avatar.jpg",
            followers_count=128,
            follows_count=42,
            media_count=17,
            account_type="BUSINESS",
            biography="Local fake Graph account for CI.",
            website="https://example.test",
            linked_facebook_page_id="fake-page-001",
        )


class GraphInstagramClient:
    """Meta Instagram Graph Content Publishing: create media container → publish."""

    def __init__(
        self,
        *,
        access_token: str,
        business_account_id: str,
        graph_version: str = DEFAULT_INSTAGRAM_GRAPH_VERSION,
        facebook_page_id: str | None = None,
        timeout: float = 60.0,
    ) -> None:
        self._token = access_token.strip()
        self._ig_user_id = business_account_id.strip()
        self._version = graph_version.strip() or DEFAULT_INSTAGRAM_GRAPH_VERSION
        self._facebook_page_id = (facebook_page_id or "").strip() or None
        self._timeout = timeout
        self._base = f"https://graph.facebook.com/{self._version}"

    def configured(self) -> bool:
        return bool(self._token and self._ig_user_id)

    def publish_image(self, *, image_url: str, caption: str) -> PublishResult:
        if not self.configured():
            raise InstagramNotConfigured(_NOT_CONFIGURED_MSG)
        if not image_url.startswith("https://"):
            raise NotConfigured("Instagram requires a publicly reachable https image_url.")
        if not caption.strip():
            raise NotConfigured("Caption is required.")
        container_id = self._create_container(image_url=image_url, caption=caption.strip())
        self._wait_for_container(container_id)
        media_id = self._publish_container(container_id)
        permalink = self._permalink(media_id)
        return PublishResult(container_id=container_id, media_id=media_id, permalink=permalink)

    def fetch_account_details(self) -> InstagramAccountDetails:
        if not self.configured():
            raise InstagramNotConfigured(_NOT_CONFIGURED_MSG)
        url = f"{self._base}/{self._ig_user_id}"
        try:
            response = httpx.get(
                url,
                params={"fields": ACCOUNT_DETAIL_FIELDS, "access_token": self._token},
                timeout=self._timeout,
            )
        except httpx.HTTPError as exc:
            raise NotConfigured("Instagram Graph API account lookup failed.") from exc
        payload = _json_or_raise(response, action="fetch account details")
        page_id = self._facebook_page_id or self._resolve_linked_page_id()
        return InstagramAccountDetails(
            id=str(payload.get("id") or self._ig_user_id),
            username=_optional_str(payload.get("username")),
            name=_optional_str(payload.get("name")),
            profile_picture_url=_optional_str(payload.get("profile_picture_url")),
            followers_count=_optional_int(payload.get("followers_count")),
            follows_count=_optional_int(payload.get("follows_count")),
            media_count=_optional_int(payload.get("media_count")),
            account_type=_optional_str(payload.get("account_type")),
            biography=_optional_str(payload.get("biography")),
            website=_optional_str(payload.get("website")),
            linked_facebook_page_id=page_id,
        )

    def _resolve_linked_page_id(self) -> str | None:
        """Page access tokens cannot list /me/accounts — only works with a user token."""
        return None

    def _create_container(self, *, image_url: str, caption: str) -> str:
        url = f"{self._base}/{self._ig_user_id}/media"
        try:
            response = httpx.post(
                url,
                data={
                    "image_url": image_url,
                    "caption": caption,
                    "access_token": self._token,
                },
                timeout=self._timeout,
            )
        except httpx.HTTPError as exc:
            raise NotConfigured("Instagram Graph API request failed.") from exc
        payload = _json_or_raise(response, action="create media container")
        creation_id = payload.get("id")
        if not creation_id:
            raise NotConfigured("Instagram did not return a media container id.")
        return str(creation_id)

    def _wait_for_container(self, creation_id: str) -> None:
        url = f"{self._base}/{creation_id}"
        deadline = time.monotonic() + _CONTAINER_POLL_MAX_WAIT
        last_status = "UNKNOWN"
        while time.monotonic() < deadline:
            try:
                response = httpx.get(
                    url,
                    params={"fields": "status_code", "access_token": self._token},
                    timeout=self._timeout,
                )
            except httpx.HTTPError as exc:
                raise NotConfigured("Instagram media container status check failed.") from exc
            payload = _json_or_raise(response, action="check media container status")
            last_status = str(payload.get("status_code") or "UNKNOWN").upper()
            if last_status == "FINISHED":
                return
            if last_status in {"ERROR", "EXPIRED"}:
                raise NotConfigured(
                    f"Instagram media container failed with status {last_status}. "
                    "Check image URL reachability, aspect ratio, and size limits."
                )
            time.sleep(_CONTAINER_POLL_INTERVAL)
        raise NotConfigured(
            f"Instagram media container did not finish in time (last status: {last_status})."
        )

    def _publish_container(self, creation_id: str) -> str:
        url = f"{self._base}/{self._ig_user_id}/media_publish"
        try:
            response = httpx.post(
                url,
                data={"creation_id": creation_id, "access_token": self._token},
                timeout=self._timeout,
            )
        except httpx.HTTPError as exc:
            raise NotConfigured("Instagram Graph API publish failed.") from exc
        payload = _json_or_raise(response, action="publish media")
        media_id = payload.get("id")
        if not media_id:
            raise NotConfigured("Instagram did not return a published media id.")
        return str(media_id)

    def _permalink(self, media_id: str) -> str | None:
        url = f"{self._base}/{media_id}"
        try:
            response = httpx.get(
                url,
                params={"fields": "permalink", "access_token": self._token},
                timeout=self._timeout,
            )
        except httpx.HTTPError:
            return None
        if response.status_code >= 400:
            return None
        try:
            payload = response.json()
        except ValueError:
            return None
        permalink = payload.get("permalink")
        return str(permalink) if permalink else None


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_int(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _json_or_raise(response: httpx.Response, *, action: str) -> dict:
    try:
        payload = response.json()
    except ValueError as exc:
        raise NotConfigured(f"Instagram {action} returned non-JSON.") from exc
    if response.status_code >= 400:
        error = payload.get("error") if isinstance(payload, dict) else None
        message = "request failed"
        if isinstance(error, dict):
            message = str(error.get("message") or message)
        raise NotConfigured(f"Instagram {action} failed: {message}")
    if not isinstance(payload, dict):
        raise NotConfigured(f"Instagram {action} returned an unexpected payload.")
    return payload
