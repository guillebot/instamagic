from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageOps
from PIL.ExifTags import IFD

SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
RAW_EXTENSIONS = {".cr2", ".cr3", ".nef", ".arw", ".dng"}

Image.MAX_IMAGE_PIXELS = 80_000_000


@dataclass
class PhotoMetadata:
    width: int | None
    height: int | None
    orientation: str | None
    captured_at: datetime | None
    camera: str | None
    lens: str | None
    focal_length_mm: float | None
    iso: int | None
    shutter: str | None
    aperture: float | None
    has_gps: bool


def classify_extension(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in SUPPORTED_EXTENSIONS:
        return "supported"
    if suffix in RAW_EXTENSIONS:
        return "raw"
    return "ignored"


def _rational(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, tuple) and len(value) == 2 and value[1]:
        return float(value[0]) / float(value[1])
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def _shutter(value: object) -> str | None:
    number = _rational(value)
    if number is None or number <= 0:
        return None
    if number >= 1:
        return f"{number:.2f}s".rstrip("0").rstrip(".")
    return f"1/{int(round(1 / number))}"


def _parse_date(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    for fmt in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(value.strip(), fmt)
        except ValueError:
            continue
    return None


def _orientation_name(width: int, height: int) -> str:
    if width == height:
        return "square"
    return "portrait" if height > width else "landscape"


def read_image(path: Path) -> Image.Image:
    with Image.open(path) as image:
        image.load()
        return image


def metadata_from_image(image: Image.Image) -> PhotoMetadata:
    exif = image.getexif()
    has_gps = False
    captured_at = None
    camera = None
    lens = None
    focal = None
    iso = None
    shutter = None
    aperture = None
    if exif:
        try:
            gps = exif.get_ifd(IFD.GPSInfo)
            has_gps = bool(gps)
        except (KeyError, AttributeError):
            has_gps = False
        make = exif.get(271)
        model = exif.get(272)
        camera_parts = [str(part).strip() for part in (make, model) if part]
        camera = " ".join(camera_parts) or None
        lens = exif.get(42036) or exif.get(42035)
        lens = str(lens).strip() if lens else None
        focal = _rational(exif.get(37386))
        iso_value = exif.get(34855)
        iso = int(iso_value) if isinstance(iso_value, int) else None
        shutter = _shutter(exif.get(33434))
        aperture = _rational(exif.get(33437))
        captured_at = _parse_date(exif.get(36867) or exif.get(306))
    oriented = ImageOps.exif_transpose(image)
    width, height = oriented.size
    return PhotoMetadata(
        width=width,
        height=height,
        orientation=_orientation_name(width, height),
        captured_at=captured_at,
        camera=camera,
        lens=lens,
        focal_length_mm=focal,
        iso=iso,
        shutter=shutter,
        aperture=aperture,
        has_gps=has_gps,
    )


def oriented_rgb(image: Image.Image) -> Image.Image:
    transposed = ImageOps.exif_transpose(image)
    if transposed.mode != "RGB":
        return transposed.convert("RGB")
    return transposed


def save_derivative(image: Image.Image, dest: Path, long_edge: int, quality: int = 82) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    working = image.copy()
    working.thumbnail((long_edge, long_edge), Image.Resampling.LANCZOS)
    # Drop every metadata block, including GPS, by saving a fresh JPEG.
    working.save(dest, format="JPEG", quality=quality, optimize=True)


def crop_image(image: Image.Image, x: int, y: int, width: int, height: int) -> Image.Image:
    return image.crop((x, y, x + width, y + height))


def derivative_has_gps(path: Path) -> bool:
    with Image.open(path) as image:
        exif = image.getexif()
        if not exif:
            return False
        try:
            return bool(exif.get_ifd(IFD.GPSInfo))
        except (KeyError, AttributeError):
            return False
