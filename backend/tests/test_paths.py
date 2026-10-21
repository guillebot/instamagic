from pathlib import Path

import pytest
from instamagic.services.images.paths import assert_library_root, safe_join


def test_safe_join_rejects_escape(tmp_path: Path) -> None:
    (tmp_path / "thumb.jpg").write_bytes(b"jpeg")
    assert safe_join(tmp_path, "thumb.jpg").name == "thumb.jpg"
    with pytest.raises(Exception):
        safe_join(tmp_path, "../secrets.txt")
    with pytest.raises(Exception):
        safe_join(tmp_path, "/etc/passwd")


def test_library_root_must_stay_inside_allowed_roots(tmp_path: Path) -> None:
    library = tmp_path / "photos"
    library.mkdir()
    assert assert_library_root(library, [tmp_path]) == library.resolve()
    outside = tmp_path.parent
    with pytest.raises(ValueError):
        assert_library_root(outside, [library])
