"""Path containment / traversal defenses for library roots and derivatives."""

from __future__ import annotations

from pathlib import Path

import pytest
from instamagic.errors import NotFound
from instamagic.services.images.folders import normalize_folder
from instamagic.services.images.paths import assert_library_root, safe_join
from tests.conftest import login, write_jpeg


def test_safe_join_rejects_nul_and_absolute(tmp_path: Path) -> None:
    (tmp_path / "ok.jpg").write_bytes(b"x")
    with pytest.raises(NotFound):
        safe_join(tmp_path, "ok\x00.jpg")
    with pytest.raises(NotFound):
        safe_join(tmp_path, "\\windows\\path")
    with pytest.raises(NotFound):
        safe_join(tmp_path, "")


def test_safe_join_rejects_symlink_escape(tmp_path: Path) -> None:
    root = tmp_path / "derivatives"
    root.mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_text("classified")
    link = root / "escape.jpg"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlinks unavailable")
    with pytest.raises(NotFound):
        safe_join(root, "escape.jpg")


def test_normalize_folder_rejects_traversal() -> None:
    with pytest.raises(ValueError):
        normalize_folder("../etc")
    with pytest.raises(ValueError):
        normalize_folder("/absolute")
    with pytest.raises(ValueError):
        normalize_folder("a\\b")
    with pytest.raises(ValueError):
        normalize_folder("a\x00b")
    assert normalize_folder("session/one") == "session/one"
    assert normalize_folder(None) == ""


def test_library_create_rejects_outside_allowed_roots(client, app_env) -> None:
    login(client)
    outside = app_env["root"].parent
    response = client.post(
        "/api/libraries",
        json={"name": "bad", "root_path": str(outside), "confirmed": False, "statement": ""},
    )
    assert response.status_code == 400
    assert "allowed" in response.json()["detail"].lower() or "outside" in response.json()["detail"].lower()


def test_assert_library_root_prefix_boundary(tmp_path: Path) -> None:
    allowed = tmp_path / "photos"
    allowed.mkdir()
    impostor = tmp_path / "photos_evil"
    impostor.mkdir()
    with pytest.raises(ValueError):
        assert_library_root(impostor, [allowed])


def test_derivative_escape_via_db_path_is_404(client, app_env) -> None:
    """Regression: poisoned thumbnail_path must not leave derivatives_root."""
    from uuid import UUID

    from instamagic.db import session_factory
    from instamagic.models import Image as ImageRow

    library = app_env["library"]
    write_jpeg(library / "session" / "shot.jpg")
    login(client)
    statement = "I confirm that every person depicted in this library is a consenting adult."
    created = client.post(
        "/api/libraries",
        json={"name": "Archive", "root_path": str(library), "confirmed": True, "statement": statement},
    )
    library_id = created.json()["id"]
    assert client.post(f"/api/libraries/{library_id}/scan").status_code == 202
    items = client.get("/api/images", params={"library_id": library_id}).json()["items"]
    image_id = items[0]["id"]

    db = session_factory()()
    image = db.get(ImageRow, UUID(image_id))
    assert image is not None
    # Poisoned DB path must not matter: grid thumbs are served from the fixed
    # {image_id}/thumb.jpg layout, never from Image.thumbnail_path.
    image.thumbnail_path = "../../../etc/passwd"
    db.commit()
    db.close()

    ok = client.get(f"/api/derivatives/{image_id}/thumbnail")
    assert ok.status_code == 200
    assert ok.headers["content-type"].startswith("image/jpeg")
    assert "max-age=" in ok.headers.get("cache-control", "")
