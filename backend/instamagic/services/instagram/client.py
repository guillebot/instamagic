"""Official Instagram Graph API Content Publishing client (Business/Creator + Page).

No unofficial mobile API. Tokens come from env only and must never be logged.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Protocol

import httpx
from instamagic.errors import InstagramNotConfigured, NotConfigured


@dataclass(frozen=True)
class PublishResult:
    container_id: str
    media_id: str
    permalink: str | None = None


class InstagramClient(Protocol):
    def configured(self) -> bool: ...

    def publish_image(self, *, image_url: str, caption: str) -> PublishResult: ...


class NotConfiguredInstagramClient:
    def configured(self) -> bool:
        return False

    def publish_image(self, *, image_url: str, caption: str) -> PublishResult:
        del image_url, caption
        raise InstagramNotConfigured(
            "Instagram Graph API is not configured. Set INSTAGRAM_ACCESS_TOKEN and "
            "INSTAGRAM_BUSINESS_ACCOUNT_ID (Professional account linked to a Facebook Page)."
        )


class FakeInstagramClient:
    """Deterministic publisher for CI. Never calls Meta."""

    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []

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


class GraphInstagramClient:
    """Meta Instagram Graph Content Publishing: create media container → publish."""

    def __init__(
        self,
        *,
        access_token: str,
        business_account_id: str,
        graph_version: str = "v21.0",
        timeout: float = 60.0,
    ) -> None:
        self._token = access_token.strip()
        self._ig_user_id = business_account_id.strip()
        self._version = graph_version.strip() or "v21.0"
        self._timeout = timeout
        self._base = f"https://graph.facebook.com/{self._version}"

    def configured(self) -> bool:
        return bool(self._token and self._ig_user_id)

    def publish_image(self, *, image_url: str, caption: str) -> PublishResult:
        if not self.configured():
            raise InstagramNotConfigured(
                "Instagram Graph API is not configured. Set INSTAGRAM_ACCESS_TOKEN and "
                "INSTAGRAM_BUSINESS_ACCOUNT_ID."
            )
        if not image_url.startswith("https://"):
            raise NotConfigured("Instagram requires a publicly reachable https image_url.")
        if not caption.strip():
            raise NotConfigured("Caption is required.")
        container_id = self._create_container(image_url=image_url, caption=caption.strip())
        media_id = self._publish_container(container_id)
        permalink = self._permalink(media_id)
        return PublishResult(container_id=container_id, media_id=media_id, permalink=permalink)

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


def _json_or_raise(response: httpx.Response, *, action: str) -> dict:
    try:
        payload = response.json()
    except ValueError as exc:
        raise NotConfigured(f"Instagram {action} returned non-JSON.") from exc
    if response.status_code >= 400:
        # Never echo tokens; Meta error messages are usually safe.
        error = payload.get("error") if isinstance(payload, dict) else None
        message = "request failed"
        if isinstance(error, dict):
            message = str(error.get("message") or message)
        raise NotConfigured(f"Instagram {action} failed: {message}")
    if not isinstance(payload, dict):
        raise NotConfigured(f"Instagram {action} returned an unexpected payload.")
    return payload
