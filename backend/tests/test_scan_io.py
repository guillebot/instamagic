from pathlib import Path

import pytest
from instamagic.services.pipeline import _brief_error, iter_library_files


def test_brief_error_keeps_relative_library_path(tmp_path: Path) -> None:
    root = tmp_path / "originals"
    root.mkdir()
    absolute = root / "2020.02.12 Shooting Sofia Coego" / "IMG_0880.jpg"
    absolute.parent.mkdir()
    absolute.write_bytes(b"x")
    exc = BlockingIOError(11, "Resource temporarily unavailable", str(absolute))
    message = _brief_error(exc, root)
    assert "/2020.02.12" not in message
    assert "2020.02.12 Shooting Sofia Coego/IMG_0880.jpg" in message
    assert "flaky library mount" in message


def test_brief_error_scrub_without_filename_attr(tmp_path: Path) -> None:
    root = tmp_path / "originals"
    root.mkdir()
    # Plain OSError without .filename still embeds the absolute path in str(exc).
    path = f"{root}/session/photo.jpg"
    exc = OSError(f"[Errno 11] Resource temporarily unavailable: '{path}'")
    message = _brief_error(exc, root)
    assert str(root) not in message
    assert "'/session/photo.jpg'" not in message
    assert "session/photo.jpg" in message
    assert message.startswith("OSError:")


def test_iter_library_files_skips_after_exhausted_retries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("instamagic.services.pipeline.time.sleep", lambda _seconds: None)
    monkeypatch.setattr("instamagic.services.images.paths.time.sleep", lambda _seconds: None)
    good = tmp_path / "ok.jpg"
    bad = tmp_path / "bad.jpg"
    good.write_bytes(b"good")
    bad.write_bytes(b"bad")

    original_is_file = Path.is_file

    def flaky_is_file(self: Path) -> bool:
        if self.name == "bad.jpg":
            raise BlockingIOError(11, "Resource temporarily unavailable", str(self))
        return original_is_file(self)

    monkeypatch.setattr(Path, "is_file", flaky_is_file)
    names = [path.name for path in iter_library_files(tmp_path)]
    assert names == ["ok.jpg"]
