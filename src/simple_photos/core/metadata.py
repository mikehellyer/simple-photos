"""Reading what a photo file says about itself: its size, when it was
taken, the camera and exposure settings, and where (GPS).

Everything goes through Pillow, with pillow-heif registered so iPhone
HEIC/HEIF files open the same way as JPEGs. A tag that's missing or
garbled just comes back as None — cameras write all sorts of odd EXIF.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from PIL import ExifTags, Image

try:
    import pillow_heif

    pillow_heif.register_heif_opener()
    HEIF_SUPPORTED = True
except ImportError:  # the app still works, just without HEIC
    HEIF_SUPPORTED = False

PHOTO_EXTENSIONS = {".jpg", ".jpeg", ".jpe", ".png", ".tif", ".tiff", ".webp", ".gif", ".bmp"}
if HEIF_SUPPORTED:
    PHOTO_EXTENSIONS |= {".heic", ".heif", ".avif"}

DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# Base IFD tags
_MAKE, _MODEL, _ORIENTATION, _DATETIME = 271, 272, 274, 306
# Exif IFD tags
_EXPOSURE_TIME, _F_NUMBER, _ISO = 33434, 33437, 34855
_DATETIME_ORIGINAL, _DATETIME_DIGITIZED = 36867, 36868
_FOCAL_LENGTH, _LENS_MODEL = 37386, 42036
# GPS IFD tags
_GPS_LAT_REF, _GPS_LAT, _GPS_LON_REF, _GPS_LON = 1, 2, 3, 4


@dataclass
class PhotoInfo:
    width: int = 0
    height: int = 0
    taken: Optional[str] = None  # DATE_FORMAT, local time as the camera recorded it
    camera_make: str = ""
    camera_model: str = ""
    lens: str = ""
    iso: Optional[int] = None
    exposure_time: Optional[float] = None  # seconds
    f_number: Optional[float] = None
    focal_length: Optional[float] = None  # mm
    gps_lat: Optional[float] = None
    gps_lon: Optional[float] = None


def is_photo_file(path: str | Path) -> bool:
    name = os.path.basename(str(path))
    return not name.startswith(".") and os.path.splitext(name)[1].lower() in PHOTO_EXTENSIONS


def _text(value) -> str:
    if isinstance(value, bytes):
        value = value.decode("utf-8", "replace")
    if not isinstance(value, str):
        return ""
    return value.replace("\x00", "").strip()


def _number(value) -> Optional[float]:
    if isinstance(value, (tuple, list)):
        value = value[0] if value else None
    try:
        number = float(value)
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    return number if number == number and number not in (float("inf"), float("-inf")) else None


def parse_exif_date(value) -> Optional[str]:
    """'2024:07:14 18:03:22' (EXIF's own format) → '2024-07-14 18:03:22'.
    Placeholder dates like '0000:00:00 00:00:00' come back as None."""
    text = _text(value)
    for fmt, length in (("%Y:%m:%d %H:%M:%S", 19), ("%Y-%m-%d %H:%M:%S", 19), ("%Y:%m:%d %H:%M", 16), ("%Y:%m:%d", 10)):
        try:
            parsed = datetime.strptime(text[:length], fmt)
        except ValueError:
            continue
        if parsed.year < 1826:  # older than the oldest surviving photograph
            return None
        return parsed.strftime(DATE_FORMAT)
    return None


def _gps_coordinate(dms, ref) -> Optional[float]:
    try:
        degrees, minutes, seconds = (float(part) for part in dms)
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    value = degrees + minutes / 60 + seconds / 3600
    if _text(ref).upper() in ("S", "W"):
        value = -value
    return round(value, 7)


def file_date(path: str | Path) -> str:
    return time.strftime(DATE_FORMAT, time.localtime(os.path.getmtime(path)))


def read_photo_info(path: str | Path) -> PhotoInfo:
    """Raises OSError (or Pillow's subclasses of it) if the file can't be
    opened as an image at all; anything missing inside it is just left
    blank."""
    info = PhotoInfo()
    with Image.open(path) as image:
        info.width, info.height = image.size
        try:
            exif = image.getexif()
        except Exception:  # a corrupt EXIF block shouldn't hide the photo
            exif = None
        if exif:
            _fill_from_exif(info, exif)
    if info.taken is None:
        info.taken = file_date(path)
    return info


def _fill_from_exif(info: PhotoInfo, exif: Image.Exif) -> None:
    if exif.get(_ORIENTATION) in (5, 6, 7, 8):  # stored on its side
        info.width, info.height = info.height, info.width
    info.camera_make = _text(exif.get(_MAKE))
    info.camera_model = _text(exif.get(_MODEL))

    try:
        sub = exif.get_ifd(ExifTags.IFD.Exif)
    except Exception:
        sub = {}
    info.taken = (
        parse_exif_date(sub.get(_DATETIME_ORIGINAL))
        or parse_exif_date(sub.get(_DATETIME_DIGITIZED))
        or parse_exif_date(exif.get(_DATETIME))
    )
    info.lens = _text(sub.get(_LENS_MODEL))
    iso = _number(sub.get(_ISO))
    info.iso = int(iso) if iso else None
    info.exposure_time = _number(sub.get(_EXPOSURE_TIME)) or None
    info.f_number = _number(sub.get(_F_NUMBER)) or None
    info.focal_length = _number(sub.get(_FOCAL_LENGTH)) or None

    try:
        gps = exif.get_ifd(ExifTags.IFD.GPSInfo)
    except Exception:
        gps = {}
    if gps:
        lat = _gps_coordinate(gps.get(_GPS_LAT), gps.get(_GPS_LAT_REF))
        lon = _gps_coordinate(gps.get(_GPS_LON), gps.get(_GPS_LON_REF))
        if lat is not None and lon is not None and (lat, lon) != (0.0, 0.0) and abs(lat) <= 90 and abs(lon) <= 180:
            info.gps_lat, info.gps_lon = lat, lon


# --- display helpers -------------------------------------------------------


def format_exposure(seconds: Optional[float]) -> str:
    if not seconds:
        return ""
    if seconds >= 1:
        return f"{seconds:g} s"
    return f"1/{round(1 / seconds)} s"


def format_aperture(f_number: Optional[float]) -> str:
    return f"f/{f_number:.1f}".replace(".0", "") if f_number else ""


def format_focal_length(mm: Optional[float]) -> str:
    return f"{mm:g} mm" if mm else ""


def format_camera(make: str, model: str) -> str:
    """'Canon' + 'Canon EOS R6' → 'Canon EOS R6' (most cameras repeat the
    make in the model; don't say it twice)."""
    if make and model.lower().startswith(make.split()[0].lower()):
        return model
    return " ".join(part for part in (make, model) if part)


def format_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("bytes", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{int(size)} {unit}" if unit == "bytes" else f"{size:.1f} {unit}"
        size /= 1024
    return ""


def megapixels(width: int, height: int) -> str:
    return f"{width * height / 1_000_000:.1f} MP" if width and height else ""

