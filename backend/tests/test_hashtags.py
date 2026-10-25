"""Publish-time hashtags and the sensitive/spam blocklist."""

from __future__ import annotations

import json
from pathlib import Path

from instamagic.services.posting.hashtags import (
    MAX_TAGS,
    MIN_TAGS,
    clean_tags,
    compose_caption,
    is_blocked,
    tags_in_text,
)
from tests.conftest import login
from tests.test_publish import _attested_library, _marked

STORED = [
    "monochrome",
    "blackandwhite",
    "fineartphotography",
    "intimateportraits",
    "sexy",
    "boudoir",
    "lightandshadow",
    "texture",
    "followforfollow",
    "softlight",
    "composition",
]


def test_blocklist_strips_sensitive_and_spam() -> None:
    for tag in ["sexy", "#Boudoir", "nude", "naked", "girl", "girls", "hot", "lingerie", "bikini", "sensual",
                "erotic", "nsfw", "adult", "followforfollow", "f4f", "like4like", "instagood", "sexyback",
                "intimatephotography", "bodyform"]:
        assert is_blocked(tag), tag
    for tag in ["photography", "photooftheweek_no", "lightandshadow", "fineartphotography", "blackandwhite",
                "portraitphotography", "texture", "shadowplay"]:
        assert not is_blocked(tag), tag
    assert clean_tags(STORED) == [
        "monochrome", "blackandwhite", "fineartphotography", "lightandshadow", "texture", "softlight", "composition"
    ]


def test_compose_appends_5_to_10_clean_tags_and_is_idempotent() -> None:
    out = compose_caption("Form and light.", STORED, seed="a")
    assert out.startswith("Form and light.\n\n#")
    tags = tags_in_text(out)
    assert MIN_TAGS <= len(tags) <= MAX_TAGS
    assert len(set(tags)) == len(tags)
    assert not any(is_blocked(t) for t in tags)
    assert tags[0] == "monochrome"
    assert compose_caption(out, STORED, seed="a") == out
    # Varies per post.
    others = {tuple(tags_in_text(compose_caption("x", STORED, seed=str(i)))) for i in range(8)}
    assert len(others) > 1


def test_compose_respects_manual_tags_and_strips_blocked() -> None:
    manual = "Quiet light #mytag #another #third #fourth #fifth #sexy"
    out = compose_caption(manual, STORED, seed="b")
    assert "#sexy" not in out
    assert out == "Quiet light #mytag #another #third #fourth #fifth"
    few = compose_caption("Hi #mytag", STORED, seed="b")
    assert tags_in_text(few)[0] == "mytag"
    assert MIN_TAGS <= len(tags_in_text(few)) <= MAX_TAGS


def test_compose_empty_caption_and_no_stored_tags() -> None:
    out = compose_caption("", [], seed="c")
    assert out.startswith("#")
    assert MIN_TAGS <= len(tags_in_text(out)) <= MAX_TAGS
    assert compose_caption("", ["sexy"], seed="c").startswith("#")


def test_published_caption_carries_hashtags(client, app_env) -> None:
    from instamagic.db import session_factory
    from instamagic.models import Image

    library: Path = app_env["library"]
    _marked(library / "session" / "clean.jpg", (30, 30, 30), (20, 20, 80, 80))
    login(client)
    library_id = _attested_library(client, library)
    image_id = client.get("/api/images", params={"library_id": library_id}).json()["items"][0]["id"]
    client.post(f"/api/images/{image_id}/rating", json={"rating": "excellent"})
    session = session_factory()()
    from uuid import UUID

    image = session.get(Image, UUID(image_id))
    image.enrich_hashtags_json = json.dumps(STORED)
    session.commit()
    session.close()
    item_id = client.post("/api/queue", json={"image_id": image_id}).json()["id"]
    assert client.post(f"/api/queue/{item_id}/approve").status_code == 200
    caption_id = client.post(f"/api/images/{image_id}/captions", json={"confirmed": True}).json()[0]["id"]
    published = client.post(f"/api/queue/{item_id}/publish", json={"caption_id": caption_id})
    assert published.status_code == 200, published.text
    fake = client.app.state.instagram
    sent = fake.calls[-1]["caption"]
    tags = tags_in_text(sent)
    assert MIN_TAGS <= len(tags) <= MAX_TAGS
    assert "sexy" not in tags and "boudoir" not in tags and "followforfollow" not in tags
    assert "monochrome" in tags


def test_backfill_adds_tags_to_safe_captions(client, app_env) -> None:
    from uuid import UUID

    from instamagic.db import session_factory
    from instamagic.models import Caption, Image
    from instamagic.services.posting.hashtag_backfill import backfill_hashtags
    from sqlalchemy import select

    library: Path = app_env["library"]
    _marked(library / "session" / "clean.jpg", (30, 30, 30), (20, 20, 80, 80))
    login(client)
    library_id = _attested_library(client, library)
    image_id = client.get("/api/images", params={"library_id": library_id}).json()["items"][0]["id"]
    client.post(f"/api/images/{image_id}/rating", json={"rating": "safe"})
    assert client.post(f"/api/images/{image_id}/captions", json={"confirmed": True}).status_code == 201
    session = session_factory()()
    image = session.get(Image, UUID(image_id))
    image.enrich_hashtags_json = json.dumps(STORED)
    session.commit()
    stats = backfill_hashtags(session)
    assert stats["images_cleaned"] == 1 and stats["captions_updated"] >= 1
    caps = session.scalars(select(Caption).where(Caption.image_id == UUID(image_id))).all()
    assert all(MIN_TAGS <= len(tags_in_text(c.text)) <= MAX_TAGS for c in caps)
    assert "sexy" not in json.loads(session.get(Image, UUID(image_id)).enrich_hashtags_json)
    again = backfill_hashtags(session)
    assert again["captions_updated"] == 0
    session.close()
