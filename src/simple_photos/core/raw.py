"""RAW camera files (CR3, NEF, ARW, RAF, DNG…), through LibRaw (rawpy).

Developing a RAW takes about a second, so thumbnails and the viewer's
first look use the JPEG preview every camera embeds — often full size,
read in milliseconds. Only when that preview is much smaller than the
photo does the viewer develop the RAW itself, in the background.

Camera details (date, exposure, lens) come from LibRaw, which reads
every maker's format; the camera's name comes from the preview's EXIF.

The preview is stored the way the sensor was held, with the turn kept
separately (LibRaw's "flip"), so it's turned here — unless the preview
carries its own orientation tag (DNGs from phones do), when that's used
instead so it isn't turned twice.
"""
from __future__ import annotations

import io
import os
from pathlib import Path
from typing import Optional

from PIL import Image, ImageOps

RAW_EXTENSIONS = {
    ".3fr", ".arw", ".cr2", ".cr3", ".crw", ".dng", ".erf", ".iiq", ".kdc", ".mef", ".mos", ".mrw",
    ".nef", ".nrw", ".orf", ".pef", ".raf", ".raw", ".rw2", ".rwl", ".sr2", ".srf", ".srw", ".x3f",
}

# LibRaw's flip → the EXIF orientation that means the same turn.
_FLIP_TO_ORIENTATION = {0: 1, 3: 3, 5: 8, 6: 6}

# A preview narrower than this share of the RAW gets developed for the viewer.
DEVELOP_BELOW = 0.75


def is_raw(path: str | Path) -> bool:
    return os.path.splitext(str(path))[1].lower() in RAW_EXTENSIONS


def available() -> bool:
    try:
        import rawpy  # noqa: F401
    except ImportError:
        return False
    return True


def _orientation(raw) -> int:
    return _FLIP_TO_ORIENTATION.get(raw.sizes.flip, 1)


def _upright(image: Image.Image, flip_orientation: int) -> Image.Image:
    own = image.getexif().get(274, 1) if image.format == "JPEG" else 1
    if own not in (None, 1):
        return ImageOps.exif_transpose(image)
    method = {3: Image.Transpose.ROTATE_180, 6: Image.Transpose.ROTATE_270, 8: Image.Transpose.ROTATE_90}.get(flip_orientation)
    return image.transpose(method) if method else image


def full_size(path: str | Path) -> tuple[int, int]:
    """The RAW's own width and height, the right way up."""
    import rawpy

    with rawpy.imread(str(path)) as raw:
        width, height = raw.sizes.width, raw.sizes.height
        return (height, width) if _orientation(raw) in (6, 8) else (width, height)


def preview(path: str | Path, max_size: Optional[int] = None) -> Image.Image:
    """The embedded preview, upright, as RGB — decoded at a reduced scale
    when only max_size pixels are wanted, which is far quicker for the
    big previews some cameras embed. Falls back to a quick half-size
    develop for the rare RAW with no preview. Raises OSError if the file
    can't be read."""
    import rawpy

    try:
        with rawpy.imread(str(path)) as raw:
            orientation = _orientation(raw)
            try:
                thumb = raw.extract_thumb()
            except (rawpy.LibRawNoThumbnailError, rawpy.LibRawUnsupportedThumbnailError):
                thumb = None
            if thumb is not None and thumb.format == rawpy.ThumbFormat.JPEG:
                image = Image.open(io.BytesIO(thumb.data))
                if max_size:
                    image.draft("RGB", (max_size, max_size))
                image.load()
                return _upright(image, orientation).convert("RGB")
            if thumb is not None:  # an uncompressed bitmap preview
                return _upright(Image.fromarray(thumb.data), orientation).convert("RGB")
            # No preview at all: develop at half size (LibRaw turns it itself).
            return Image.fromarray(raw.postprocess(use_camera_wb=True, output_bps=8, half_size=True))
    except rawpy.LibRawError as error:
        raise OSError(f"Can't read this RAW file: {error}") from error


def develop(path: str | Path) -> Image.Image:
    """The full RAW, developed with the camera's white balance — upright
    (LibRaw applies the turn itself)."""
    import rawpy

    try:
        with rawpy.imread(str(path)) as raw:
            return Image.fromarray(raw.postprocess(use_camera_wb=True, output_bps=8))
    except rawpy.LibRawError as error:
        raise OSError(f"Can't develop this RAW file: {error}") from error


def needs_developing(path: str | Path, preview_width: int, preview_height: int) -> bool:
    try:
        width, height = full_size(path)
    except Exception:
        return False
    return max(preview_width, preview_height) < DEVELOP_BELOW * max(width, height)


def read_info(path: str | Path):
    """PhotoInfo for a RAW. Raises OSError if it can't be read."""
    import rawpy

    from simple_photos.core.metadata import DATE_FORMAT, PhotoInfo, file_date

    info = PhotoInfo()
    try:
        with rawpy.imread(str(path)) as raw:
            width, height = raw.sizes.width, raw.sizes.height
            if _orientation(raw) in (6, 8):
                width, height = height, width
            info.width, info.height = width, height
            other = raw.other
            if other.timestamp and other.timestamp.year >= 1826:
                info.taken = other.timestamp.strftime(DATE_FORMAT)
            info.iso = int(other.iso_speed) if other.iso_speed else None
            info.exposure_time = other.shutter_speed or None
            info.f_number = round(other.aperture, 1) if other.aperture else None
            info.focal_length = round(other.focal_length, 1) if other.focal_length else None
            info.lens = (raw.lens.model or "").strip()
            try:
                thumb = raw.extract_thumb()
                if thumb.format == rawpy.ThumbFormat.JPEG:
                    exif = Image.open(io.BytesIO(thumb.data)).getexif()
                    info.camera_make = str(exif.get(271) or "").strip("\x00 ")
                    info.camera_model = str(exif.get(272) or "").strip("\x00 ")
            except rawpy.LibRawError:
                pass
    except rawpy.LibRawError as error:
        raise OSError(f"Can't read this RAW file: {error}") from error
    if not info.camera_model:
        # No name in the preview: TIFF-based RAWs (DNG, NEF, CR2, ARW…) have
        # it in their own first directory, which Pillow can read.
        try:
            with Image.open(path) as image:
                exif = image.getexif()
                info.camera_make = str(exif.get(271) or "").strip("\x00 ")
                info.camera_model = str(exif.get(272) or "").strip("\x00 ")
        except Exception:
            pass
    if info.taken is None:
        info.taken = file_date(path)
    return info
