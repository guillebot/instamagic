import pytest
from instamagic.services.images.folders import normalize_folder
from tests.conftest import login, write_jpeg

STATEMENT = "I confirm that every person depicted in this library is a consenting adult."


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(None, ""), ("", ""), ("a", "a"), ("a/b/", "a/b"), ("a//b", "a/b"), (" a/b ", "a/b")],
)
def test_normalize_folder_accepts(raw, expected) -> None:
    assert normalize_folder(raw) == expected


@pytest.mark.parametrize("raw", ["/etc", "../x", "a/../b", "a/./b", "a\\b", "a\x00b"])
def test_normalize_folder_rejects(raw) -> None:
    with pytest.raises(ValueError):
        normalize_folder(raw)


def test_folders_and_folder_filter(client, app_env) -> None:
    library = app_env["library"]
    write_jpeg(library / "root.jpg", color=(1, 2, 3))
    write_jpeg(library / "2015 Shoot_A" / "a1.jpg", color=(10, 20, 30))
    write_jpeg(library / "2015 Shoot_A" / "a2.jpg", color=(40, 20, 30))
    write_jpeg(library / "2015 Shoot_A" / "picks" / "p1.jpg", color=(70, 20, 30))
    write_jpeg(library / "2016 B" / "b1.jpg", color=(100, 20, 30))
    (library / "Empty Session").mkdir()
    login(client)
    created = client.post(
        "/api/libraries",
        json={
            "name": "Archive",
            "root_path": str(library),
            "confirmed": True,
            "statement": STATEMENT,
        },
    )
    library_id = created.json()["id"]
    assert client.post(f"/api/libraries/{library_id}/scan").json()["status"] == "succeeded"

    top = client.get(f"/api/libraries/{library_id}/folders").json()
    assert top["path"] == "" and top["parent"] is None and top["source"] == "db"
    assert top["image_count"] == 1
    assert [(f["name"], f["image_count"]) for f in top["folders"]] == [
        ("2015 Shoot_A", 3),
        ("2016 B", 1),
    ]
    assert all(f["thumbnail_url"] for f in top["folders"])

    shoot = client.get(
        f"/api/libraries/{library_id}/folders", params={"path": "2015 Shoot_A"}
    ).json()
    assert shoot["parent"] == ""
    assert [c["path"] for c in shoot["breadcrumbs"]] == ["", "2015 Shoot_A"]
    assert [(f["path"], f["image_count"]) for f in shoot["folders"]] == [("2015 Shoot_A/picks", 1)]
    assert shoot["image_count"] == 2

    empty = client.get(
        f"/api/libraries/{library_id}/folders", params={"path": "Empty Session"}
    ).json()
    assert empty["folders"] == [] and empty["image_count"] == 0

    bad = client.get(f"/api/libraries/{library_id}/folders", params={"path": "../.."})
    assert bad.status_code == 400

    def names(params):
        response = client.get("/api/images", params={"library_id": library_id, **params})
        assert response.status_code == 200, response.text
        return sorted(item["filename"] for item in response.json()["items"])

    assert names({"folder": ""}) == ["root.jpg"]
    assert names({"folder": "2015 Shoot_A"}) == ["a1.jpg", "a2.jpg"]
    assert names({"folder": "2015 Shoot_A", "recursive": "true"}) == ["a1.jpg", "a2.jpg", "p1.jpg"]
    assert names({"folder": "2015 Shoot%", "recursive": "true"}) == []
    assert len(names({})) == 5
    assert client.get("/api/images", params={"folder": "/abs"}).status_code == 400


def test_folders_fall_back_to_filesystem(client, app_env) -> None:
    library = app_env["library"]
    (library / "Not Scanned Yet" / "inner").mkdir(parents=True)
    login(client)
    created = client.post(
        "/api/libraries",
        json={
            "name": "Archive",
            "root_path": str(library),
            "confirmed": True,
            "statement": STATEMENT,
        },
    )
    library_id = created.json()["id"]
    top = client.get(f"/api/libraries/{library_id}/folders").json()
    assert top["source"] == "filesystem"
    assert [(f["name"], f["image_count"]) for f in top["folders"]] == [("Not Scanned Yet", 0)]
