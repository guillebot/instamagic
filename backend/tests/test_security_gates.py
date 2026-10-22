"""Attestation, DO_NOT_POST, and publishing / Gemini fail-closed gates via API."""

from __future__ import annotations

from pathlib import Path

import pytest
from instamagic.errors import GeminiBlocked
from instamagic.models.enums import SafetyState
from instamagic.services.gemini.context import assert_safe_gemini_payload, build_context
from instamagic.services.gemini.google import build_request_body
from instamagic.services.safety.decision import DecisionInput, decide
from PIL import Image, ImageDraw
from tests.conftest import login

ATTESTATION = "I confirm that every person depicted in this library is a consenting adult."


def _marked(path: Path, color: tuple[int, int, int], box: tuple[int, int, int, int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (400, 600), color)
    ImageDraw.Draw(image).rectangle(box, fill=(230, 230, 230))
    image.save(path, format="JPEG", quality=90)


def test_unattested_library_cannot_queue(client, app_env) -> None:
    library: Path = app_env["library"]
    _marked(library / "session" / "clean.jpg", (30, 30, 30), (20, 20, 80, 80))
    login(client)
    created = client.post(
        "/api/libraries",
        json={"name": "NoAttest", "root_path": str(library), "confirmed": False, "statement": ""},
    )
    assert created.status_code == 201
    assert created.json()["attested"] is False
    library_id = created.json()["id"]
    assert client.post(f"/api/libraries/{library_id}/scan").status_code == 202
    items = client.get("/api/images", params={"library_id": library_id}).json()["items"]
    image_id = items[0]["id"]
    client.post(f"/api/images/{image_id}/rating", json={"rating": "excellent"})
    refused = client.post("/api/queue", json={"image_id": image_id})
    assert refused.status_code == 409
    assert "attestation" in refused.json()["detail"].lower()


def test_wrong_attestation_statement_rejected(client, app_env) -> None:
    login(client)
    created = client.post(
        "/api/libraries",
        json={
            "name": "BadStatement",
            "root_path": str(app_env["library"]),
            "confirmed": True,
            "statement": "I pinky-promise everyone is an adult.",
        },
    )
    assert created.status_code == 400


def test_do_not_post_cannot_be_queued_or_cropped(client, app_env) -> None:
    library: Path = app_env["library"]
    _marked(library / "session" / "blocked-frame.jpg", (40, 40, 90), (40, 300, 180, 420))
    login(client)
    created = client.post(
        "/api/libraries",
        json={"name": "Archive", "root_path": str(library), "confirmed": True, "statement": ATTESTATION},
    )
    library_id = created.json()["id"]
    assert client.post(f"/api/libraries/{library_id}/scan").status_code == 202
    items = client.get("/api/images", params={"library_id": library_id}).json()["items"]
    blocked = items[0]
    assert blocked["model_state"] == "DO_NOT_POST"
    image_id = blocked["id"]
    client.post(f"/api/images/{image_id}/rating", json={"rating": "safe"})
    assert client.post("/api/queue", json={"image_id": image_id}).status_code == 409
    crops = client.get(f"/api/images/{image_id}/crops").json()
    if crops:
        assert client.post(f"/api/images/{image_id}/crops/{crops[0]['id']}/approve").status_code == 409
    captions = client.post(f"/api/images/{image_id}/captions", json={"confirmed": True})
    assert captions.status_code == 409


def test_missing_detector_fails_closed_to_review() -> None:
    state = decide(
        DecisionInput(
            attested=True,
            detector_available=False,
            uncertain=False,
            clear_exposed=True,
            viable_safety_crop=True,
        )
    )
    assert state is SafetyState.REVIEW


def test_gemini_payload_rejects_path_leak() -> None:
    with pytest.raises(GeminiBlocked):
        assert_safe_gemini_payload({"text": "see /photos/session/a.jpg"}, policy="text_only")
    with pytest.raises(GeminiBlocked):
        assert_safe_gemini_payload({"inline_data": {"data": "x"}}, policy="text_only")


def test_build_request_body_text_only_drops_bytes() -> None:
    context = build_context(
        attested=True,
        human_rating="safe",
        model_state="REVIEW",
        needs_crop=False,
        has_approved_crop=False,
        policy="text_only",
        operator_confirmed=False,
        content_tags=["portrait"],
        composition="portrait",
        mood="soft",
        safe_description="A portrait photograph.",
        crop=None,
    )
    body = build_request_body(context, policy="text_only", image_bytes=b"should-not-appear")
    assert "inline_data" not in str(body)
    assert b"should-not-appear" not in str(body).encode()


def test_path_like_tags_rejected(client) -> None:
    login(client)
    bad = client.post("/api/tags", json={"name": "../etc/passwd"})
    assert bad.status_code == 422


def test_auto_publish_always_false_in_config(client) -> None:
    login(client)
    config = client.get("/api/config").json()
    assert config["auto_publish"] is False
    status = client.get("/api/instagram/status").json()
    assert status["auto_publish"] is False
