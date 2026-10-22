from pathlib import Path

import pytest
from instamagic.services.images.paths import (
    assert_configured_external_mounts,
    assert_external_mount,
    assert_library_root,
    assert_library_root_mounted,
    is_transient_io_error,
    retry_transient_io,
    safe_join,
)


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


def test_assert_external_mount_rejects_plain_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("instamagic.services.images.paths.is_mount_point", lambda _path: False)
    with pytest.raises(ValueError, match="not an external mount"):
        assert_external_mount(tmp_path)


def test_assert_external_mount_accepts_mount_point(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("instamagic.services.images.paths.is_mount_point", lambda _path: True)
    assert assert_external_mount(tmp_path) == tmp_path.resolve()


def test_assert_configured_external_mounts_requires_roots() -> None:
    with pytest.raises(ValueError, match="ALLOWED_LIBRARY_ROOTS is empty"):
        assert_configured_external_mounts([])


def test_assert_library_root_mounted_checks_allowed_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    library = tmp_path / "photos"
    library.mkdir()
    seen: list[Path] = []

    def fake_mount(path: Path) -> bool:
        seen.append(path)
        return path == tmp_path.resolve()

    monkeypatch.setattr("instamagic.services.images.paths.is_mount_point", fake_mount)
    assert assert_library_root_mounted(library, [tmp_path]) == library.resolve()
    assert seen == [tmp_path.resolve()]

    monkeypatch.setattr("instamagic.services.images.paths.is_mount_point", lambda _path: False)
    with pytest.raises(ValueError, match="not an external mount"):
        assert_library_root_mounted(library, [tmp_path])


def test_retry_transient_io_retries_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("instamagic.services.images.paths.time.sleep", lambda _seconds: None)
    calls = {"n": 0}

    def flaky() -> str:
        calls["n"] += 1
        if calls["n"] < 3:
            raise BlockingIOError(11, "Resource temporarily unavailable", "/data/originals/a.jpg")
        return "ok"

    assert retry_transient_io(flaky, attempts=5, base_delay=0.01) == "ok"
    assert calls["n"] == 3


def test_retry_transient_io_raises_non_transient_immediately() -> None:
    calls = {"n": 0}

    def missing() -> None:
        calls["n"] += 1
        raise FileNotFoundError("gone")

    with pytest.raises(FileNotFoundError):
        retry_transient_io(missing, attempts=5, base_delay=0.01)
    assert calls["n"] == 1


def test_is_transient_io_error_recognizes_blocking() -> None:
    assert is_transient_io_error(BlockingIOError(11, "busy", "/tmp/x"))
    assert not is_transient_io_error(FileNotFoundError("gone"))
