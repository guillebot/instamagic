"""Regression: Review safety/rating filters must match human-rated images."""

from instamagic.services.images.presentation import normalize_human_rating_filter
from tests.conftest import login, write_jpeg

STATEMENT = "I confirm that every person depicted in this library is a consenting adult."


def test_normalize_human_rating_filter() -> None:
    assert normalize_human_rating_filter(None) is None
    assert normalize_human_rating_filter("") is None
    assert normalize_human_rating_filter("  ") is None
    assert normalize_human_rating_filter("safe") == "safe"
    assert normalize_human_rating_filter("SAFE") == "safe"
    assert normalize_human_rating_filter("Safe") == "safe"
    assert normalize_human_rating_filter("EXCELLENT") == "excellent"
    assert normalize_human_rating_filter("DO_NOT_POST") == "reject"
    assert normalize_human_rating_filter("bogus") is None


def test_human_rating_and_effective_safe_filters(client, app_env) -> None:
    library = app_env["library"]
    write_jpeg(library / "Album" / "a.jpg", color=(1, 2, 3))
    write_jpeg(library / "Album" / "b.jpg", color=(4, 5, 6))
    write_jpeg(library / "Album" / "c.jpg", color=(7, 8, 9))
    write_jpeg(library / "Album" / "d.jpg", color=(10, 11, 12))
    write_jpeg(library / "Other" / "e.jpg", color=(13, 14, 15))
    login(client)
    library_id = client.post(
        "/api/libraries",
        json={
            "name": "Archive",
            "root_path": str(library),
            "confirmed": True,
            "statement": STATEMENT,
        },
    ).json()["id"]
    assert client.post(f"/api/libraries/{library_id}/scan").json()["status"] == "succeeded"

    items = client.get("/api/images", params={"library_id": library_id, "folder": "Album"}).json()[
        "items"
    ]
    assert len(items) == 4
    ratings = ["reject", "review", "safe", "excellent"]
    for item, rating in zip(sorted(items, key=lambda row: row["filename"]), ratings, strict=True):
        out = client.post(f"/api/images/{item['id']}/rating", json={"rating": rating}).json()
        assert out["human_rating"] == rating
        # Classifier stays REVIEW for clean fixtures; badge follows human rating.
        assert out["model_state"] == "REVIEW"

    def names(**params: str) -> list[str]:
        response = client.get(
            "/api/images", params={"library_id": library_id, "folder": "Album", **params}
        )
        assert response.status_code == 200, response.text
        return sorted(row["filename"] for row in response.json()["items"])

    assert names(human_rating="reject") == ["a.jpg"]
    assert names(human_rating="review") == ["b.jpg"]
    assert names(human_rating="safe") == ["c.jpg"]
    assert names(human_rating="excellent") == ["d.jpg"]
    # Case / badge aliases for the rating query param.
    assert names(human_rating="SAFE") == ["c.jpg"]
    assert names(human_rating="DO_NOT_POST") == ["a.jpg"]

    # Badge filter (model_state query) must use effective_state, not raw model_state.
    assert names(model_state="SAFE") == ["c.jpg", "d.jpg"]
    assert names(model_state="REVIEW") == ["b.jpg"]
    assert names(model_state="REJECTED") == ["a.jpg"]
    assert names(model_state="DO_NOT_POST") == []  # human reject → Rejected, not Unsafe
    assert names(model_state="SAFE_AFTER_CROP") == []
    assert names(model_state="PUBLISHABLE") == []

    # Folder scoping still applies — Other/e.jpg is rated nowhere.
    assert "e.jpg" not in names(model_state="SAFE")
    assert (
        client.get(
            "/api/images",
            params={"library_id": library_id, "folder": "Album", "human_rating": "nope"},
        ).status_code
        == 400
    )


def test_safe_filter_with_include_subfolders(client, app_env) -> None:
    """Reproduce: album + Include subfolders + SAFE must return human-rated safe pics."""
    library = app_env["library"]
    write_jpeg(library / "Album" / "top_safe.jpg", color=(1, 2, 3))
    write_jpeg(library / "Album" / "top_other.jpg", color=(4, 5, 6))
    write_jpeg(library / "Album" / "picks" / "nested_safe.jpg", color=(7, 8, 9))
    write_jpeg(library / "Album" / "picks" / "nested_reject.jpg", color=(10, 11, 12))
    write_jpeg(library / "Elsewhere" / "out.jpg", color=(13, 14, 15))
    login(client)
    library_id = client.post(
        "/api/libraries",
        json={
            "name": "Archive",
            "root_path": str(library),
            "confirmed": True,
            "statement": STATEMENT,
        },
    ).json()["id"]
    assert client.post(f"/api/libraries/{library_id}/scan").json()["status"] == "succeeded"

    by_name = {
        item["filename"]: item["id"]
        for item in client.get(
            "/api/images",
            params={"library_id": library_id, "folder": "Album", "recursive": "true"},
        ).json()["items"]
    }
    assert set(by_name) == {"top_safe.jpg", "top_other.jpg", "nested_safe.jpg", "nested_reject.jpg"}

    for name, rating in [
        ("top_safe.jpg", "safe"),
        ("top_other.jpg", "review"),
        ("nested_safe.jpg", "safe"),
        ("nested_reject.jpg", "reject"),
    ]:
        out = client.post(f"/api/images/{by_name[name]}/rating", json={"rating": rating}).json()
        assert out["human_rating"] == rating
        assert out["model_state"] == "REVIEW"  # classifier unchanged
        if rating == "safe":
            assert out["effective_state"] == "SAFE"

    def names(**params: object) -> list[str]:
        response = client.get(
            "/api/images",
            params={"library_id": library_id, "folder": "Album", "recursive": "true", **params},
        )
        assert response.status_code == 200, response.text
        return sorted(row["filename"] for row in response.json()["items"])

    # Rating filter (stored lowercase) + include subfolders.
    assert names(human_rating="safe") == ["nested_safe.jpg", "top_safe.jpg"]
    # Frontend / badge may send SAFE uppercase on the rating param.
    assert names(human_rating="SAFE") == ["nested_safe.jpg", "top_safe.jpg"]
    # Safety badge filter (model_state=SAFE) must follow effective_state under recursion.
    assert names(model_state="SAFE") == ["nested_safe.jpg", "top_safe.jpg"]

    # Non-recursive stays direct children only.
    direct = client.get(
        "/api/images",
        params={
            "library_id": library_id,
            "folder": "Album",
            "recursive": "false",
            "human_rating": "safe",
        },
    ).json()["items"]
    assert sorted(row["filename"] for row in direct) == ["top_safe.jpg"]


def test_safe_after_crop_and_blur_filters(client, app_env) -> None:
    """SAFE_AFTER_CROP / SAFE_AFTER_BLUR find remediable frames (same classifier state)."""
    library = app_env["library"]
    # Fake classifier: "unsafe" in the name → SAFE_AFTER_CROP; clean → REVIEW.
    write_jpeg(library / "Album" / "unsafe-top.jpg", color=(90, 40, 40))
    write_jpeg(library / "Album" / "clean-top.jpg", color=(30, 30, 30))
    write_jpeg(library / "Album" / "picks" / "unsafe-nested.jpg", color=(90, 50, 50))
    write_jpeg(library / "Album" / "picks" / "clean-nested.jpg", color=(40, 40, 40))
    write_jpeg(library / "Elsewhere" / "unsafe-out.jpg", color=(80, 30, 30))
    login(client)
    library_id = client.post(
        "/api/libraries",
        json={
            "name": "Archive",
            "root_path": str(library),
            "confirmed": True,
            "statement": STATEMENT,
        },
    ).json()["id"]
    assert client.post(f"/api/libraries/{library_id}/scan").json()["status"] == "succeeded"

    def names(**params: object) -> list[str]:
        response = client.get(
            "/api/images",
            params={"library_id": library_id, "folder": "Album", **params},
        )
        assert response.status_code == 200, response.text
        return sorted(row["filename"] for row in response.json()["items"])

    # Direct children only.
    assert names(model_state="SAFE_AFTER_CROP") == ["unsafe-top.jpg"]
    assert names(model_state="SAFE_AFTER_BLUR") == ["unsafe-top.jpg"]
    assert names(model_state="REVIEW") == ["clean-top.jpg"]

    # Include subfolders: both remediable filters see nested unsafe frames.
    assert names(model_state="SAFE_AFTER_CROP", recursive="true") == [
        "unsafe-nested.jpg",
        "unsafe-top.jpg",
    ]
    assert names(model_state="SAFE_AFTER_BLUR", recursive="true") == [
        "unsafe-nested.jpg",
        "unsafe-top.jpg",
    ]
    assert "unsafe-out.jpg" not in names(model_state="SAFE_AFTER_CROP", recursive="true")

    # Rated safe without crop/blur stays remediable (not Safe, not Publishable yet).
    items = client.get(
        "/api/images",
        params={"library_id": library_id, "folder": "Album"},
    ).json()["items"]
    unsafe_id = next(row["id"] for row in items if row["filename"] == "unsafe-top.jpg")
    rated = client.post(f"/api/images/{unsafe_id}/rating", json={"rating": "safe"}).json()
    assert rated["model_state"] == "SAFE_AFTER_CROP"
    assert rated["effective_state"] == "SAFE_AFTER_CROP"
    assert names(model_state="SAFE_AFTER_CROP") == ["unsafe-top.jpg"]
    assert names(model_state="SAFE_AFTER_BLUR") == ["unsafe-top.jpg"]
    assert names(model_state="SAFE") == []
    assert names(model_state="PUBLISHABLE") == []

    crops = client.get(f"/api/images/{unsafe_id}/crops").json()
    crop = next(item for item in crops if item.get("excludes_unsafe"))
    approved = client.post(
        f"/api/images/{unsafe_id}/crops/{crop['id']}/approve",
        json={"x": crop["x"], "y": crop["y"], "width": crop["width"], "height": crop["height"]},
    )
    assert approved.status_code == 200, approved.text
    after = client.get(f"/api/images/{unsafe_id}").json()
    assert after["effective_state"] == "PUBLISHABLE"
    assert names(model_state="PUBLISHABLE") == ["unsafe-top.jpg"]
    assert names(model_state="SAFE_AFTER_CROP") == []
    assert names(model_state="SAFE") == []
