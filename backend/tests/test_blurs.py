import numpy as np
from instamagic.services.blurs.generator import (
    BlurWindow,
    apply_blur_covers,
    covers_unsafe_boxes,
    feathered_ellipse_mask,
    suggest_blurs,
)
from instamagic.services.geometry import Box
from PIL import Image


def test_feathered_composite_blurs_inside_and_spares_outside() -> None:
    # High-frequency checkerboard so blur changes are measurable.
    base = np.zeros((200, 200, 3), dtype=np.uint8)
    base[::2, ::2] = 255
    base[1::2, 1::2] = 255
    image = Image.fromarray(base, mode="RGB")
    cover = BlurWindow(
        x=50,
        y=50,
        width=100,
        height=100,
        shape="ellipse",
        feather=20,
        radius=18.0,
        reason="test",
        covers_unsafe=True,
    )
    result = apply_blur_covers(image, [cover])
    out = np.asarray(result)
    # Well inside the opaque core: pixels should differ from the sharp checkerboard.
    assert not np.array_equal(out[100, 100], base[100, 100])
    # Far outside the ellipse: unchanged.
    assert np.array_equal(out[10, 10], base[10, 10])
    assert np.array_equal(out[190, 190], base[190, 190])


def test_mask_boundary_is_feathered_not_binary() -> None:
    mask = feathered_ellipse_mask((120, 120), x=20, y=20, width=80, height=80, feather=16)
    arr = np.asarray(mask)
    assert arr[60, 60] == 255
    assert arr[5, 5] == 0
    ring = arr[(arr > 0) & (arr < 255)]
    assert ring.size > 0
    assert ring.min() < 200
    assert ring.max() > 50


def test_cover_check_passes_when_core_contains_unsafe_box() -> None:
    unsafe = Box(40, 40, 20, 20)
    covering = suggest_blurs(200, 200, [unsafe], strength="standard")
    assert covering
    assert covers_unsafe_boxes(covering, [unsafe], (200, 200))


def test_suggested_cover_contains_wide_unsafe_box() -> None:
    # Fake classifier shape: wide band near the bottom — ellipse must clear corners.
    unsafe = Box(60, 432, 240, 132)
    covering = suggest_blurs(400, 600, [unsafe], strength="standard")
    assert covering
    assert covers_unsafe_boxes(covering, [unsafe], (400, 600))


def test_cover_check_fails_when_blur_misses_region() -> None:
    unsafe = Box(40, 40, 30, 30)
    miss = BlurWindow(
        x=140,
        y=140,
        width=40,
        height=40,
        shape="ellipse",
        feather=8,
        radius=20.0,
        reason="miss",
        covers_unsafe=False,
    )
    assert not covers_unsafe_boxes([miss], [unsafe], (200, 200))


def test_suggested_sigma_is_strong_not_beauty_soft() -> None:
    unsafe = Box(80, 80, 100, 80)
    covers = suggest_blurs(400, 400, [unsafe], strength="subtle")
    assert covers[0].radius >= 14
    strong = suggest_blurs(400, 400, [unsafe], strength="strong")
    assert strong[0].radius > covers[0].radius


STATEMENT = "I confirm that every person depicted in this library is a consenting adult."


def test_create_rectangle_cover_defaults_hard_edge(client, app_env) -> None:
    from tests.conftest import login, write_jpeg

    library = app_env["library"]
    write_jpeg(library / "frame.jpg", color=(40, 40, 40))
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
    image_id = client.get("/api/images", params={"library_id": library_id}).json()["items"][0]["id"]
    created = client.post(
        f"/api/images/{image_id}/blurs",
        json={
            "x": 10,
            "y": 10,
            "width": 80,
            "height": 60,
            "shape": "rectangle",
            "strength": "standard",
        },
    )
    assert created.status_code == 201
    assert created.json()["edge_mode"] == "hard"


def test_suggest_blur_from_stored_regions_is_opt_in(client, app_env) -> None:
    """Scan stores boxes but does not auto-create covers; suggest builds them on demand."""
    from tests.conftest import login, write_jpeg

    library = app_env["library"]
    write_jpeg(library / "unsafe-frame.jpg", color=(90, 40, 40))
    write_jpeg(library / "blocked-frame.jpg", color=(80, 20, 20))
    write_jpeg(library / "clean-frame.jpg", color=(30, 30, 30))
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
    items = {
        row["filename"]: row
        for row in client.get("/api/images", params={"library_id": library_id}).json()["items"]
    }

    unsafe_id = items["unsafe-frame.jpg"]["id"]
    assert items["unsafe-frame.jpg"]["model_state"] == "SAFE_AFTER_CROP"
    assert client.get(f"/api/images/{unsafe_id}/blurs").json() == []

    suggested = client.post(f"/api/images/{unsafe_id}/blurs/suggest", json={"strength": "standard"})
    assert suggested.status_code == 200, suggested.text
    covers = suggested.json()
    assert len(covers) >= 1
    assert all(row["covers_unsafe"] for row in covers)
    assert all(not row["approved"] for row in covers)

    approved = client.post(f"/api/images/{unsafe_id}/blurs/approve")
    assert approved.status_code == 200, approved.text
    assert all(row["approved"] for row in approved.json())

    blocked_id = items["blocked-frame.jpg"]["id"]
    assert items["blocked-frame.jpg"]["model_state"] == "DO_NOT_POST"
    suggested_blocked = client.post(
        f"/api/images/{blocked_id}/blurs/suggest", json={"strength": "standard"}
    )
    assert suggested_blocked.status_code == 200, suggested_blocked.text

    clean_id = items["clean-frame.jpg"]["id"]
    empty = client.post(f"/api/images/{clean_id}/blurs/suggest", json={"strength": "standard"})
    assert empty.status_code == 409
