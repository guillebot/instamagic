"""GraphInstagramClient container polling and linked Page id."""

from __future__ import annotations

import pytest
from instamagic.services.instagram.client import ACCOUNT_DETAIL_FIELDS, GraphInstagramClient


def test_publish_waits_for_container_finished(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str]] = []

    class FakeResponse:
        def __init__(self, payload: dict, status_code: int = 200):
            self._payload = payload
            self.status_code = status_code

        def json(self) -> dict:
            return self._payload

    status_checks = iter(
        [
            {"status_code": "IN_PROGRESS"},
            {"status_code": "FINISHED"},
        ]
    )

    def fake_post(url, data=None, timeout=None):
        calls.append(("POST", url))
        if url.endswith("/media"):
            return FakeResponse({"id": "container-1"})
        if url.endswith("/media_publish"):
            return FakeResponse({"id": "media-99"})
        raise AssertionError(url)

    def fake_get(url, params=None, timeout=None):
        calls.append(("GET", url))
        if url.endswith("/container-1"):
            return FakeResponse(next(status_checks))
        if url.endswith("/media-99"):
            return FakeResponse({"permalink": "https://www.instagram.com/p/abc/"})
        raise AssertionError(url)

    monkeypatch.setattr("instamagic.services.instagram.client.httpx.post", fake_post)
    monkeypatch.setattr("instamagic.services.instagram.client.httpx.get", fake_get)
    monkeypatch.setattr("instamagic.services.instagram.client.time.sleep", lambda _s: None)

    client = GraphInstagramClient(
        access_token="page-token",
        business_account_id="178414000",
        facebook_page_id="page-42",
    )
    result = client.publish_image(image_url="https://example.test/x.jpg", caption="hello")
    assert result.media_id == "media-99"
    assert any("container-1" in url for method, url in calls if method == "GET")


def test_fetch_account_uses_stored_page_id(monkeypatch: pytest.MonkeyPatch) -> None:
    requested_fields: list[str] = []

    def fake_get(url, params=None, timeout=None):
        requested_fields.append((params or {}).get("fields", ""))

        class FakeResponse:
            status_code = 200

            def json(self):
                # Minimal graph.facebook.com payload (no account_type on this host).
                return {
                    "id": "17841403337960568",
                    "username": "foto.sch",
                    "name": "Foto Schimmel",
                    "followers_count": 1200,
                }

        return FakeResponse()

    monkeypatch.setattr("instamagic.services.instagram.client.httpx.get", fake_get)
    client = GraphInstagramClient(
        access_token="page-token",
        business_account_id="17841403337960568",
        facebook_page_id="page-photo-schimmel",
    )
    details = client.fetch_account_details()
    assert requested_fields == [ACCOUNT_DETAIL_FIELDS]
    assert "account_type" not in ACCOUNT_DETAIL_FIELDS
    assert details.username == "foto.sch"
    assert details.name == "Foto Schimmel"
    assert details.followers_count == 1200
    assert details.account_type is None
    assert details.profile_picture_url is None
    assert details.biography is None
    assert details.linked_facebook_page_id == "page-photo-schimmel"
