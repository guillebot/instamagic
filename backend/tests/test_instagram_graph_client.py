"""GraphInstagramClient container polling and linked Page id."""

from __future__ import annotations

import pytest
from instamagic.services.instagram.client import GraphInstagramClient


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
    def fake_get(url, params=None, timeout=None):
        class FakeResponse:
            status_code = 200

            def json(self):
                return {
                    "id": "178414000",
                    "username": "foto.sch",
                    "account_type": "BUSINESS",
                }

        return FakeResponse()

    monkeypatch.setattr("instamagic.services.instagram.client.httpx.get", fake_get)
    client = GraphInstagramClient(
        access_token="page-token",
        business_account_id="178414000",
        facebook_page_id="page-photo-schimmel",
    )
    details = client.fetch_account_details()
    assert details.linked_facebook_page_id == "page-photo-schimmel"
