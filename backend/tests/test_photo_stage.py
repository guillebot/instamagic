from datetime import UTC, datetime
from pathlib import Path

from instamagic.db import get_engine
from instamagic.models import Image as ImageRow
from instamagic.models import PublishingQueueItem
from instamagic.services.images.stage import QueueFact, photo_stage
from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session
from tests.conftest import login


def _stage(**kwargs):  # type: ignore[no-untyped-def]
    base = {"human_rating": None, "model_state": "SAFE", "has_blurs": False, "blur_verified": False}
    base.update(kwargs)
    return photo_stage(**base)


def test_photo_stage_rules() -> None:
    assert _stage().stage == "new"
    assert _stage(model_state="DO_NOT_POST").stage == "new"
    assert _stage(has_blurs=True).stage == "to_review"
    assert _stage(human_rating="review").stage == "to_review"
    assert _stage(human_rating="reject").stage == "rejected"
    assert (_stage(human_rating="safe", model_state="DO_NOT_POST").detail) == "blur_unverified"
    flagged_ok = _stage(human_rating="safe", model_state="DO_NOT_POST", has_blurs=True, blur_verified=True)
    assert (flagged_ok.stage, flagged_ok.detail) == ("candidate", "needs_caption")
    ready = _stage(human_rating="excellent", caption_statuses=["generated"])
    assert (ready.stage, ready.detail, ready.caption_ready) == ("candidate", "caption_ready", True)
    assert _stage(human_rating="safe", caption_statuses=["rejected"]).caption_ready is False
    assert _stage(human_rating="safe", queue=[QueueFact("CANDIDATE")]).detail == "in_queue"
    slot = _stage(human_rating="safe", queue=[QueueFact("APPROVED", datetime.now(UTC))])
    assert (slot.stage, slot.detail) == ("scheduled", "slot")
    assert _stage(queue=[QueueFact("APPROVED")]).detail == "awaiting_slot"
    assert _stage(queue=[QueueFact("FAILED")]).detail == "failed"
    posted = _stage(queue=[QueueFact("APPROVED"), QueueFact("PUBLISHED")], permalink="https://x/p/1")
    assert (posted.stage, posted.permalink, posted.label) == ("posted", "https://x/p/1", "Posted")


def test_stage_field_filter_and_counts(client, app_env) -> None:
    library: Path = app_env["library"]
    folder = library / "s1"
    folder.mkdir()
    for index, name in enumerate(["a.jpg", "b.jpg", "c.jpg", "d.jpg"]):
        Image.new("RGB", (64, 48), (20 + index * 40, 30, 30)).save(folder / name)
    login(client)
    statement = "I confirm that every person depicted in this library is a consenting adult."
    created = client.post(
        "/api/libraries",
        json={"name": "Archive", "root_path": str(library), "confirmed": True, "statement": statement},
    )
    library_id = created.json()["id"]
    assert client.post(f"/api/libraries/{library_id}/scan").status_code == 202

    with Session(get_engine()) as session:
        rows = {row.filename: row for row in session.scalars(select(ImageRow)).all()}
        for row in rows.values():
            row.model_state = "SAFE"
        rows["b.jpg"].human_rating = "reject"
        rows["c.jpg"].human_rating = "safe"
        rows["d.jpg"].human_rating = "safe"
        session.add(
            PublishingQueueItem(image_id=rows["d.jpg"].id, state="PUBLISHED", media_type="IMAGE")
        )
        session.commit()

    items = client.get("/api/images", params={"library_id": library_id}).json()["items"]
    stages = {item["filename"]: item["stage"] for item in items}
    assert stages == {"a.jpg": "new", "b.jpg": "rejected", "c.jpg": "candidate", "d.jpg": "posted"}
    by_name = {item["filename"]: item for item in items}
    assert by_name["c.jpg"]["stage_label"] == "Candidate"
    assert by_name["c.jpg"]["stage_detail"] == "needs_caption"

    for stage, expected in [("new", {"a.jpg"}), ("candidate", {"c.jpg"}), ("posted", {"d.jpg"})]:
        listed = client.get("/api/images", params={"library_id": library_id, "stage": stage}).json()["items"]
        assert {item["filename"] for item in listed} == expected
    assert client.get("/api/images", params={"stage": "bogus"}).status_code == 400

    counts = client.get("/api/images/stage-counts", params={"library_id": library_id})
    assert counts.status_code == 200, counts.text
    assert counts.json()["counts"] == {
        "new": 1,
        "to_review": 0,
        "candidate": 1,
        "scheduled": 0,
        "posted": 1,
        "rejected": 1,
    }


def test_queue_item_stage() -> None:
    from instamagic.services.images.stage import queue_item_stage

    assert queue_item_stage("PUBLISHED", None, {"stage": "posted"})["stage"] == "posted"
    assert queue_item_stage("APPROVED", None, {"stage": "posted"})["stage_detail"] == "awaiting_slot"
    candidate = queue_item_stage("CANDIDATE", None, {"stage": "candidate", "stage_detail": "in_queue"})
    assert (candidate["stage"], candidate["stage_label"]) == ("candidate", "Candidate")
    assert queue_item_stage("CANDIDATE", None, {"stage": "posted"})["stage_detail"] == "in_queue"
