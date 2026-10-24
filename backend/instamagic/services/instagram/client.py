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
from instamagic.services.instagram.graph_http import (
    graph_error_fields,
    graph_get_page,
    graph_json_or_raise,
    graph_paginate,
)
from instamagic.services.instagram.permissions import FetchedScopes, scopes_from_debug_token_data

# IG User fields for graph.facebook.com (Facebook Login / Page token).
# account_type exists only on graph.instagram.com (Instagram Login) — omit here.
ACCOUNT_DETAIL_FIELDS = (
    "id,username,name,profile_picture_url,followers_count,follows_count,"
    "media_count,biography,website"
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
class ContentPublishingLimit:
    """Instagram content publishing quota (typically 50 posts / 24h rolling)."""

    quota_total: int
    quota_duration_seconds: int
    usage: int


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


@dataclass(frozen=True)
class MediaSummary:
    id: str
    caption: str | None
    media_type: str | None
    timestamp: str | None
    permalink: str | None
    like_count: int | None = None
    comments_count: int | None = None


@dataclass(frozen=True)
class MediaInsightSnapshot:
    reach: int | None = None
    saved: int | None = None
    shares: int | None = None
    likes: int | None = None
    comments: int | None = None
    total_interactions: int | None = None
    views: int | None = None
    impressions: int | None = None


class InstagramClient(Protocol):
    def configured(self) -> bool: ...

    def publish_image(self, *, image_url: str, caption: str) -> PublishResult: ...

    def publish_carousel(self, *, image_urls: list[str], caption: str) -> PublishResult: ...

    def publish_story(self, *, image_url: str) -> PublishResult: ...

    def fetch_account_details(self) -> InstagramAccountDetails: ...

    def fetch_content_publishing_limit(self) -> ContentPublishingLimit: ...

    def fetch_granted_permissions(self) -> set[str]: ...

    def fetch_account_insight_snapshot(self) -> dict[str, int | None]: ...

    def list_recent_media(self, *, limit: int = 50) -> list[MediaSummary]: ...

    def fetch_media_insights(
        self, media_id: str, *, media_type: str | None
    ) -> MediaInsightSnapshot: ...

    def search_hashtag(self, hashtag: str) -> str: ...

    def fetch_hashtag_top_media(self, hashtag_id: str) -> list[dict]: ...

    def fetch_hashtag_recent_media(self, hashtag_id: str) -> list[dict]: ...

    def fetch_business_discovery(self, username: str) -> dict: ...

    def list_media_comments(self, media_id: str) -> list[dict]: ...

    def post_comment_reply(self, comment_id: str, message: str) -> None: ...


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

    def publish_carousel(self, *, image_urls: list[str], caption: str) -> PublishResult:
        del image_urls, caption
        raise InstagramNotConfigured(_NOT_CONFIGURED_MSG)

    def publish_story(self, *, image_url: str) -> PublishResult:
        del image_url
        raise InstagramNotConfigured(_NOT_CONFIGURED_MSG)

    def fetch_account_details(self) -> InstagramAccountDetails:
        raise InstagramNotConfigured(_NOT_CONFIGURED_MSG)

    def fetch_content_publishing_limit(self) -> ContentPublishingLimit:
        raise InstagramNotConfigured(_NOT_CONFIGURED_MSG)

    def fetch_granted_permissions(self) -> set[str]:
        raise InstagramNotConfigured(_NOT_CONFIGURED_MSG)

    def fetch_account_insight_snapshot(self) -> dict[str, int | None]:
        raise InstagramNotConfigured(_NOT_CONFIGURED_MSG)

    def list_recent_media(self, *, limit: int = 50) -> list[MediaSummary]:
        raise InstagramNotConfigured(_NOT_CONFIGURED_MSG)

    def fetch_media_insights(
        self, media_id: str, *, media_type: str | None
    ) -> MediaInsightSnapshot:
        raise InstagramNotConfigured(_NOT_CONFIGURED_MSG)

    def search_hashtag(self, hashtag: str) -> str:
        raise InstagramNotConfigured(_NOT_CONFIGURED_MSG)

    def fetch_hashtag_top_media(self, hashtag_id: str) -> list[dict]:
        raise InstagramNotConfigured(_NOT_CONFIGURED_MSG)

    def fetch_hashtag_recent_media(self, hashtag_id: str) -> list[dict]:
        raise InstagramNotConfigured(_NOT_CONFIGURED_MSG)

    def fetch_business_discovery(self, username: str) -> dict:
        raise InstagramNotConfigured(_NOT_CONFIGURED_MSG)

    def list_media_comments(self, media_id: str) -> list[dict]:
        raise InstagramNotConfigured(_NOT_CONFIGURED_MSG)

    def post_comment_reply(self, comment_id: str, message: str) -> None:
        raise InstagramNotConfigured(_NOT_CONFIGURED_MSG)


class FakeInstagramClient:
    """Deterministic publisher for CI. Never calls Meta."""

    def __init__(self) -> None:
        self.calls: list[dict[str, str | list[str]]] = []
        self.carousel_calls: list[dict[str, object]] = []
        self.story_calls: list[dict[str, str]] = []
        self.account_fetches: int = 0
        self.content_limit_usage: int = 0
        self.content_limit_total: int = 50

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

    def publish_story(self, *, image_url: str) -> PublishResult:
        if not image_url.startswith(("http://", "https://")):
            raise NotConfigured("Fake Instagram publisher requires an http(s) image_url.")
        container_id = f"fake-story-container-{uuid.uuid4().hex[:12]}"
        media_id = f"fake-story-media-{uuid.uuid4().hex[:12]}"
        self.story_calls.append({"image_url": image_url, "media_id": media_id})
        return PublishResult(
            container_id=container_id,
            media_id=media_id,
            permalink=None,
        )

    def publish_carousel(self, *, image_urls: list[str], caption: str) -> PublishResult:
        if len(image_urls) < 2 or len(image_urls) > 10:
            raise NotConfigured("Carousel requires 2–10 image URLs.")
        for url in image_urls:
            if not url.startswith(("http://", "https://")):
                raise NotConfigured("Fake Instagram publisher requires http(s) image URLs.")
        if not caption.strip():
            raise NotConfigured("Caption is required.")
        container_id = f"fake-carousel-{uuid.uuid4().hex[:12]}"
        media_id = f"fake-media-{uuid.uuid4().hex[:12]}"
        self.carousel_calls.append(
            {"image_urls": list(image_urls), "caption": caption, "media_id": media_id}
        )
        return PublishResult(
            container_id=container_id,
            media_id=media_id,
            permalink=f"https://www.instagram.com/p/{media_id}/",
        )

    def fetch_content_publishing_limit(self) -> ContentPublishingLimit:
        return ContentPublishingLimit(
            quota_total=self.content_limit_total,
            quota_duration_seconds=86400,
            usage=self.content_limit_usage,
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

    def fetch_granted_permissions(self) -> set[str]:
        return set(self.fetch_granted_permissions_detailed().scopes)

    def fetch_granted_permissions_detailed(self) -> FetchedScopes:
        return FetchedScopes(
            scopes=frozenset(
                {
                    "instagram_basic",
                    "instagram_content_publish",
                    "instagram_manage_insights",
                    "instagram_manage_comments",
                    "pages_show_list",
                    "pages_read_engagement",
                    "business_management",
                }
            ),
            determined=True,
        )

    def fetch_live_username(self) -> str | None:
        return "instamagic_fixture"

    def fetch_account_insight_snapshot(self) -> dict[str, int | None]:
        return {
            "follower_count": 128,
            "reach": 400,
            "profile_views": 55,
            "impressions": 900,
            "accounts_engaged": 30,
        }

    def list_recent_media(self, *, limit: int = 50) -> list[MediaSummary]:
        del limit
        return [
            MediaSummary(
                id="fake-media-001",
                caption="Fixture post",
                media_type="IMAGE",
                timestamp="2026-01-01T12:00:00+0000",
                permalink="https://www.instagram.com/p/fixture/",
                like_count=10,
                comments_count=2,
            )
        ]

    def fetch_media_insights(
        self, media_id: str, *, media_type: str | None
    ) -> MediaInsightSnapshot:
        del media_id, media_type
        return MediaInsightSnapshot(
            reach=120,
            saved=3,
            shares=1,
            likes=10,
            comments=2,
            total_interactions=16,
            views=200,
        )

    def search_hashtag(self, hashtag: str) -> str:
        return f"fake-hashtag-id-{hashtag}"

    def fetch_hashtag_top_media(self, hashtag_id: str) -> list[dict]:
        del hashtag_id
        return [{"id": "m1", "caption": "#street #night", "like_count": 4}]

    def fetch_hashtag_recent_media(self, hashtag_id: str) -> list[dict]:
        del hashtag_id
        return [{"id": "m2", "caption": "#portrait", "like_count": 2}]

    def list_media_comments(self, media_id: str) -> list[dict]:
        del media_id
        return [
            {
                "id": "fake-comment-1",
                "text": "Beautiful tones!",
                "username": "viewer1",
                "timestamp": "2026-01-02T10:00:00+0000",
            }
        ]

    def post_comment_reply(self, comment_id: str, message: str) -> None:
        self.calls.append({"comment_id": comment_id, "message": message})

    def fetch_business_discovery(self, username: str) -> dict:
        return {
            "followers_count": 900,
            "media_count": 12,
            "media": {
                "data": [
                    {
                        "caption": "Hello #berlin",
                        "like_count": 30,
                        "comments_count": 2,
                        "timestamp": "2026-01-01T12:00:00+0000",
                        "media_type": "IMAGE",
                        "permalink": f"https://www.instagram.com/p/{username}/",
                    }
                ]
            },
        }


class GraphInstagramClient:
    """Meta Instagram Graph Content Publishing: create media container → publish."""

    def __init__(
        self,
        *,
        access_token: str,
        business_account_id: str,
        graph_version: str = DEFAULT_INSTAGRAM_GRAPH_VERSION,
        facebook_page_id: str | None = None,
        meta_app_id: str | None = None,
        meta_app_secret: str | None = None,
        timeout: float = 60.0,
    ) -> None:
        self._token = access_token.strip()
        self._ig_user_id = business_account_id.strip()
        self._version = graph_version.strip() or DEFAULT_INSTAGRAM_GRAPH_VERSION
        self._facebook_page_id = (facebook_page_id or "").strip() or None
        self._meta_app_id = (meta_app_id or "").strip() or None
        self._meta_app_secret = (meta_app_secret or "").strip() or None
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

    def publish_story(self, *, image_url: str) -> PublishResult:
        if not self.configured():
            raise InstagramNotConfigured(_NOT_CONFIGURED_MSG)
        if not image_url.startswith("https://"):
            raise NotConfigured(
                "Instagram requires a publicly reachable https image_url for stories."
            )
        container_id = self._create_container(image_url=image_url, caption="", media_type="STORIES")
        self._wait_for_container(container_id)
        media_id = self._publish_container(container_id)
        return PublishResult(container_id=container_id, media_id=media_id, permalink=None)

    def publish_carousel(self, *, image_urls: list[str], caption: str) -> PublishResult:
        if not self.configured():
            raise InstagramNotConfigured(_NOT_CONFIGURED_MSG)
        if len(image_urls) < 2 or len(image_urls) > 10:
            raise NotConfigured("Carousel requires 2–10 publicly reachable https image URLs.")
        if not caption.strip():
            raise NotConfigured("Caption is required.")
        child_ids: list[str] = []
        for url in image_urls:
            if not url.startswith("https://"):
                raise NotConfigured("Instagram requires publicly reachable https image URLs.")
            child_id = self._create_container(image_url=url, caption="", is_carousel_item=True)
            self._wait_for_container(child_id)
            child_ids.append(child_id)
        container_id = self._create_carousel_parent(child_ids=child_ids, caption=caption.strip())
        self._wait_for_container(container_id)
        media_id = self._publish_container(container_id)
        permalink = self._permalink(media_id)
        return PublishResult(container_id=container_id, media_id=media_id, permalink=permalink)

    def fetch_content_publishing_limit(self) -> ContentPublishingLimit:
        if not self.configured():
            raise InstagramNotConfigured(_NOT_CONFIGURED_MSG)
        url = f"{self._base}/{self._ig_user_id}/content_publishing_limit"
        try:
            response = httpx.get(
                url,
                params={"fields": "config,quota_usage", "access_token": self._token},
                timeout=self._timeout,
            )
        except httpx.HTTPError as exc:
            raise NotConfigured("Instagram content publishing limit check failed.") from exc
        payload = _json_or_raise(response, action="fetch content publishing limit")
        config = payload.get("config") if isinstance(payload.get("config"), dict) else {}
        quota_total = _optional_int(config.get("quota_total")) or 50
        quota_duration = _optional_int(config.get("quota_duration")) or 86400
        usage = _optional_int(payload.get("quota_usage")) or 0
        return ContentPublishingLimit(
            quota_total=quota_total,
            quota_duration_seconds=quota_duration,
            usage=usage,
        )

    def fetch_live_username(self) -> str | None:
        """Read-only live probe (username field only) for health checks."""
        if not self.configured():
            raise InstagramNotConfigured(_NOT_CONFIGURED_MSG)
        payload = graph_get_page(
            f"{self._base}/{self._ig_user_id}",
            params={"fields": "username", "access_token": self._token},
            timeout=min(self._timeout, 15.0),
            action="probe account username",
        )
        return _optional_str(payload.get("username"))

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

    def _create_container(
        self,
        *,
        image_url: str,
        caption: str,
        is_carousel_item: bool = False,
        media_type: str | None = None,
    ) -> str:
        url = f"{self._base}/{self._ig_user_id}/media"
        payload: dict[str, str] = {
            "image_url": image_url,
            "access_token": self._token,
        }
        if media_type:
            payload["media_type"] = media_type
        if is_carousel_item:
            payload["is_carousel_item"] = "true"
        elif caption:
            payload["caption"] = caption
        try:
            response = httpx.post(
                url,
                data=payload,
                timeout=self._timeout,
            )
        except httpx.HTTPError as exc:
            raise NotConfigured("Instagram Graph API request failed.") from exc
        payload = _json_or_raise(response, action="create media container")
        creation_id = payload.get("id")
        if not creation_id:
            raise NotConfigured("Instagram did not return a media container id.")
        return str(creation_id)

    def _create_carousel_parent(self, *, child_ids: list[str], caption: str) -> str:
        url = f"{self._base}/{self._ig_user_id}/media"
        try:
            response = httpx.post(
                url,
                data={
                    "media_type": "CAROUSEL",
                    "children": ",".join(child_ids),
                    "caption": caption,
                    "access_token": self._token,
                },
                timeout=self._timeout,
            )
        except httpx.HTTPError as exc:
            raise NotConfigured("Instagram Graph API carousel request failed.") from exc
        payload = _json_or_raise(response, action="create carousel container")
        creation_id = payload.get("id")
        if not creation_id:
            raise NotConfigured("Instagram did not return a carousel container id.")
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

    def fetch_granted_permissions_detailed(self) -> FetchedScopes:
        if not self.configured():
            raise InstagramNotConfigured(_NOT_CONFIGURED_MSG)
        if self._meta_app_id and self._meta_app_secret:
            app_access = f"{self._meta_app_id}|{self._meta_app_secret}"
            try:
                payload = graph_get_page(
                    f"{self._base}/debug_token",
                    params={"input_token": self._token, "access_token": app_access},
                    timeout=self._timeout,
                    action="inspect access token",
                )
                data = payload.get("data")
                if isinstance(data, dict):
                    scopes = scopes_from_debug_token_data(data)
                    return FetchedScopes(scopes=frozenset(scopes), determined=True)
            except NotConfigured:
                pass
        try:
            payload = graph_get_page(
                f"{self._base}/me/permissions",
                params={"access_token": self._token},
                timeout=self._timeout,
                action="fetch user token permissions",
            )
        except NotConfigured as exc:
            detail = exc.detail.lower()
            if "nonexisting field" in detail or "(permissions)" in detail:
                return FetchedScopes(scopes=frozenset(), determined=False)
            return FetchedScopes(scopes=frozenset(), determined=False)
        granted: set[str] = set()
        for row in payload.get("data") or []:
            if not isinstance(row, dict):
                continue
            if str(row.get("status") or "").lower() == "granted":
                name = str(row.get("permission") or "").strip()
                if name:
                    granted.add(name)
        return FetchedScopes(scopes=frozenset(granted), determined=True)

    def fetch_granted_permissions(self) -> set[str]:
        return set(self.fetch_granted_permissions_detailed().scopes)

    def fetch_account_insight_snapshot(self) -> dict[str, int | None]:
        if not self.configured():
            raise InstagramNotConfigured(_NOT_CONFIGURED_MSG)
        details = self.fetch_account_details()
        follower_count = details.followers_count
        metrics = ("reach,profile_views,accounts_engaged",)
        params = {
            "metric": metrics[0],
            "period": "day",
            "metric_type": "total_value",
        }
        payload = graph_get_page(
            f"{self._base}/{self._ig_user_id}/insights",
            params={**params, "access_token": self._token},
            timeout=self._timeout,
            action="fetch account insights",
        )
        values: dict[str, int | None] = {
            "follower_count": follower_count,
            "reach": None,
            "profile_views": None,
            "impressions": None,
            "accounts_engaged": None,
        }
        for row in payload.get("data") or []:
            if not isinstance(row, dict):
                continue
            name = str(row.get("name") or "")
            total = row.get("total_value")
            if isinstance(total, dict):
                val = _optional_int(total.get("value"))
            else:
                val = _insight_series_last(row)
            if name in values:
                values[name] = val
        return values

    def list_recent_media(self, *, limit: int = 50) -> list[MediaSummary]:
        if not self.configured():
            raise InstagramNotConfigured(_NOT_CONFIGURED_MSG)
        cap = max(1, min(limit, 100))
        fields = "id,caption,media_type,timestamp,permalink,like_count,comments_count"
        rows: list[MediaSummary] = []
        for item in graph_paginate(
            self._base,
            f"{self._ig_user_id}/media",
            access_token=self._token,
            params={"fields": fields, "limit": str(min(cap, 25))},
            timeout=self._timeout,
            action="list media",
            max_pages=max(1, (cap + 24) // 25),
        ):
            rows.append(
                MediaSummary(
                    id=str(item.get("id") or ""),
                    caption=_optional_str(item.get("caption")),
                    media_type=_optional_str(item.get("media_type")),
                    timestamp=_optional_str(item.get("timestamp")),
                    permalink=_optional_str(item.get("permalink")),
                    like_count=_optional_int(item.get("like_count")),
                    comments_count=_optional_int(item.get("comments_count")),
                )
            )
            if len(rows) >= cap:
                break
        return rows

    def fetch_media_insights(
        self, media_id: str, *, media_type: str | None
    ) -> MediaInsightSnapshot:
        if not self.configured():
            raise InstagramNotConfigured(_NOT_CONFIGURED_MSG)
        metric_list = "reach,saved,shares,likes,comments,total_interactions,views"
        if (media_type or "").upper() in {"VIDEO", "REELS"}:
            metric_list = "reach,saved,shares,likes,comments,total_interactions,views,impressions"
        payload = graph_get_page(
            f"{self._base}/{media_id}/insights",
            params={
                "metric": metric_list,
                "access_token": self._token,
                "metric_type": "total_value",
            },
            timeout=self._timeout,
            action="fetch media insights",
        )
        values: dict[str, int | None] = {}
        for row in payload.get("data") or []:
            if not isinstance(row, dict):
                continue
            name = str(row.get("name") or "")
            total = row.get("total_value")
            if isinstance(total, dict):
                values[name] = _optional_int(total.get("value"))
            else:
                values[name] = _insight_series_last(row)
        return MediaInsightSnapshot(
            reach=values.get("reach"),
            saved=values.get("saved"),
            shares=values.get("shares"),
            likes=values.get("likes"),
            comments=values.get("comments"),
            total_interactions=values.get("total_interactions"),
            views=values.get("views"),
            impressions=values.get("impressions"),
        )

    def search_hashtag(self, hashtag: str) -> str:
        if not self.configured():
            raise InstagramNotConfigured(_NOT_CONFIGURED_MSG)
        payload = graph_get_page(
            # Root node, not an IG User edge: GET /ig_hashtag_search?user_id=...&q=...
            f"{self._base}/ig_hashtag_search",
            params={
                "user_id": self._ig_user_id,
                "q": hashtag.lstrip("#"),
                "access_token": self._token,
            },
            timeout=self._timeout,
            action="search hashtag",
        )
        data = payload.get("data") or []
        if not data or not isinstance(data[0], dict):
            raise NotConfigured("Hashtag was not found.")
        hashtag_id = data[0].get("id")
        if not hashtag_id:
            raise NotConfigured("Hashtag search did not return an id.")
        return str(hashtag_id)

    def fetch_hashtag_top_media(self, hashtag_id: str) -> list[dict]:
        return self._hashtag_media_edge(hashtag_id, edge="top_media")

    def fetch_hashtag_recent_media(self, hashtag_id: str) -> list[dict]:
        return self._hashtag_media_edge(hashtag_id, edge="recent_media")

    def _hashtag_media_edge(self, hashtag_id: str, *, edge: str) -> list[dict]:
        if not self.configured():
            raise InstagramNotConfigured(_NOT_CONFIGURED_MSG)
        fields = "id,caption,like_count,comments_count,timestamp,media_type,media_url,permalink"
        rows: list[dict] = []
        for item in graph_paginate(
            self._base,
            f"{hashtag_id}/{edge}",
            access_token=self._token,
            # Large pages with media_url trip Graph error #1 ("reduce the amount of data").
            params={"user_id": self._ig_user_id, "fields": fields, "limit": "10"},
            timeout=self._timeout,
            action=f"fetch hashtag {edge}",
            max_pages=2,
        ):
            rows.append(item)
        return rows

    def fetch_business_discovery(self, username: str) -> dict:
        if not self.configured():
            raise InstagramNotConfigured(_NOT_CONFIGURED_MSG)
        handle = username.lstrip("@")
        fields = (
            f"business_discovery.username({handle})"
            "{username,followers_count,media_count,"
            "media{id,caption,like_count,comments_count,timestamp,media_type,media_url,permalink}}"
        )
        payload = graph_get_page(
            f"{self._base}/{self._ig_user_id}",
            params={"fields": fields, "access_token": self._token},
            timeout=self._timeout,
            action="business discovery",
        )
        discovery = payload.get("business_discovery")
        if not isinstance(discovery, dict):
            raise NotConfigured("Business discovery is unavailable for this account.")
        return discovery

    def list_media_comments(self, media_id: str) -> list[dict]:
        if not self.configured():
            raise InstagramNotConfigured(_NOT_CONFIGURED_MSG)
        fields = "id,text,username,timestamp"
        rows: list[dict] = []
        for item in graph_paginate(
            self._base,
            f"{media_id}/comments",
            access_token=self._token,
            params={"fields": fields, "limit": "50"},
            timeout=self._timeout,
            action="list media comments",
            max_pages=3,
        ):
            rows.append(item)
        return rows

    def post_comment_reply(self, comment_id: str, message: str) -> None:
        if not self.configured():
            raise InstagramNotConfigured(_NOT_CONFIGURED_MSG)
        if not message.strip():
            raise NotConfigured("Reply message is required.")
        url = f"{self._base}/{comment_id}/replies"
        try:
            response = httpx.post(
                url,
                data={"message": message.strip(), "access_token": self._token},
                timeout=self._timeout,
            )
        except httpx.HTTPError as exc:
            raise NotConfigured("Instagram comment reply failed.") from exc
        graph_json_or_raise(response, action="post comment reply")


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _insight_series_last(row: dict) -> int | None:
    values = row.get("values")
    if not isinstance(values, list) or not values:
        return None
    last = values[-1]
    if isinstance(last, dict):
        return _optional_int(last.get("value"))
    return None


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
        if isinstance(payload, dict):
            _code, message = graph_error_fields(payload)
        else:
            message = "request failed"
        raise NotConfigured(f"Instagram {action} failed: {message}")
    if not isinstance(payload, dict):
        raise NotConfigured(f"Instagram {action} returned an unexpected payload.")
    return payload
