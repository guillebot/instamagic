from pathlib import Path

from instamagic.services.images.derivatives import (
    THUMB_LONG_EDGE,
    classify_extension,
    derivative_has_gps,
    metadata_from_image,
    oriented_rgb,
    save_derivative,
)
from instamagic.services.images.hashes import file_sha256
from PIL import Image
from PIL.ExifTags import IFD


def test_extension_classification() -> None:
    assert classify_extension(Path("a.JPG")) == "supported"
    assert classify_extension(Path("a.CR3")) == "raw"
    assert classify_extension(Path("a.txt")) == "ignored"


def test_derivative_strips_gps_and_applies_orientation(tmp_path: Path) -> None:
    source = tmp_path / "source.jpg"
    image = Image.new("RGB", (80, 40), (12, 24, 36))
    exif = Image.Exif()
    exif[274] = 6
    gps = exif.get_ifd(IFD.GPSInfo)
    gps[1] = "N"
    gps[2] = (40.0, 0.0, 0.0)
    gps[3] = "W"
    gps[4] = (74.0, 0.0, 0.0)
    image.save(source, format="JPEG", exif=exif)
    with Image.open(source) as saved:
        meta = metadata_from_image(saved)
        assert meta.has_gps
        oriented = oriented_rgb(saved)
    assert oriented.size == (40, 80)
    dest = tmp_path / "out" / "thumb.jpg"
    save_derivative(oriented, dest, THUMB_LONG_EDGE, progressive=True)
    assert not derivative_has_gps(dest)
    with Image.open(dest) as thumb:
        assert max(thumb.size) <= THUMB_LONG_EDGE
    before = file_sha256(source)
    assert file_sha256(source) == before
