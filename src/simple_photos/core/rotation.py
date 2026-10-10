"""Turning photos a quarter turn at a time, without losing quality.

A JPEG is never re-encoded: only its EXIF Orientation tag changes, which
every modern viewer, phone and browser honours (it's how cameras record
a photo taken holding the camera sideways). Usually the tag is already
there and just two bytes of the file change. Lossless formats (PNG,
TIFF, BMP) have their pixels turned and are saved again — still no loss.
Formats that would lose quality on re-saving (WebP, HEIC, AVIF) and
animated GIFs are left alone.

The file's modified time is kept, since for a photo without EXIF it's
the only record of when it was taken.
"""
from __future__ import annotations

import os
import shutil
import struct
from pathlib import Path

from PIL import Image, ImageOps

from simple_photos.core import metadata  # noqa: F401  (registers the HEIF opener)

ORIENTATION_TAG = 274

# Orientation after turning the photo a quarter turn clockwise, for each
# of EXIF's eight orientations (2, 4, 5 and 7 are the mirrored ones).
_QUARTER_TURN_CW = {1: 6, 6: 3, 3: 8, 8: 1, 2: 7, 7: 4, 4: 5, 5: 2}

JPEG_EXTENSIONS = {".jpg", ".jpeg", ".jpe"}
RESAVE_EXTENSIONS = {".png", ".tif", ".tiff", ".bmp", ".gif"}


class RotateError(Exception):
    """Why a photo couldn't be rotated, in words for the user."""


def turned(orientation: int, quarter_turns_cw: int) -> int:
    orientation = orientation if orientation in _QUARTER_TURN_CW else 1
    for _ in range(quarter_turns_cw % 4):
        orientation = _QUARTER_TURN_CW[orientation]
    return orientation


def can_rotate(path: str | Path) -> bool:
    ext = os.path.splitext(str(path))[1].lower()
    return ext in JPEG_EXTENSIONS or ext in RESAVE_EXTENSIONS


def rotate_photo(path: str | Path, quarter_turns_cw: int) -> None:
    """Turn the photo clockwise by that many quarter turns (negative for
    anticlockwise). Raises RotateError or OSError."""
    turns = quarter_turns_cw % 4
    if turns == 0:
        return
    path = str(path)
    ext = os.path.splitext(path)[1].lower()
    stat = os.stat(path)
    if ext in JPEG_EXTENSIONS:
        _rotate_jpeg(path, turns)
    elif ext in RESAVE_EXTENSIONS:
        _rotate_by_resaving(path, turns)
    else:
        raise RotateError(f"Rotating {ext or 'these'} files isn't supported (it would lose quality)")
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))


# --- JPEG: just the Orientation tag ---------------------------------------


def _segments(data: bytes):
    """(marker, start, end) of each header segment, up to the image data."""
    if data[:2] != b"\xff\xd8":
        raise RotateError("This isn't a valid JPEG file")
    pos = 2
    while pos + 4 <= len(data):
        if data[pos] != 0xFF:
            raise RotateError("This JPEG file looks damaged")
        marker = data[pos + 1]
        if marker == 0xFF:  # padding
            pos += 1
            continue
        if marker in (0xD9, 0xDA):  # end of image / start of scan
            return
        length = struct.unpack(">H", data[pos + 2 : pos + 4])[0]
        yield marker, pos, pos + 2 + length
        pos += 2 + length


def _find_exif(data: bytes) -> tuple[int, int] | None:
    for marker, start, end in _segments(data):
        if marker == 0xE1 and data[start + 4 : start + 10] == b"Exif\x00\x00":
            return start, end
    return None


def _orientation_offset(data: bytes, tiff_start: int) -> int | None:
    """Byte offset of the Orientation value in IFD0, if the tag is there."""
    order = data[tiff_start : tiff_start + 2]
    if order not in (b"II", b"MM"):
        return None
    endian = "<" if order == b"II" else ">"
    ifd = tiff_start + struct.unpack(endian + "I", data[tiff_start + 4 : tiff_start + 8])[0]
    count = struct.unpack(endian + "H", data[ifd : ifd + 2])[0]
    for i in range(count):
        entry = ifd + 2 + i * 12
        tag, kind, n = struct.unpack(endian + "HHI", data[entry : entry + 8])
        if tag == ORIENTATION_TAG and kind == 3 and n == 1:  # one SHORT
            return entry + 8
    return None


def read_jpeg_orientation(data: bytes) -> int:
    found = _find_exif(data)
    if not found:
        return 1
    offset = _orientation_offset(data, found[0] + 10)
    if offset is None:
        return 1
    endian = "<" if data[found[0] + 10 : found[0] + 12] == b"II" else ">"
    return struct.unpack(endian + "H", data[offset : offset + 2])[0]


def _rotate_jpeg(path: str, turns: int) -> None:
    data = bytearray(Path(path).read_bytes())
    new = turned(read_jpeg_orientation(bytes(data)), turns)
    found = _find_exif(bytes(data))
    offset = _orientation_offset(bytes(data), found[0] + 10) if found else None
    if offset is not None:
        # The usual case: overwrite the two bytes in place.
        endian = "<" if data[found[0] + 10 : found[0] + 12] == b"II" else ">"
        data[offset : offset + 2] = struct.pack(endian + "H", new)
    else:
        # No Orientation tag yet: write the EXIF block afresh with one.
        with Image.open(path) as image:
            exif = image.getexif()
            for ifd in (0x8769, 0x8825, 0xA005):  # load sub-IFDs so they're rewritten too
                try:
                    exif.get_ifd(ifd)
                except Exception:
                    pass
        exif[ORIENTATION_TAG] = new
        payload = exif.tobytes()
        if not payload.startswith(b"Exif\x00\x00"):
            payload = b"Exif\x00\x00" + payload
        if len(payload) + 2 > 0xFFFF:
            raise RotateError("This photo's EXIF data is too large to update")
        segment = b"\xff\xe1" + struct.pack(">H", len(payload) + 2) + payload
        if found:
            data[found[0] : found[1]] = segment
        else:
            # After SOI, and after a JFIF header if there is one.
            insert_at = 2
            for marker, start, end in _segments(bytes(data)):
                if marker == 0xE0:
                    insert_at = end
                break
            data[insert_at:insert_at] = segment
    _write_atomically(path, bytes(data))


# --- lossless formats: turn the pixels -------------------------------------


def _rotate_by_resaving(path: str, turns: int) -> None:
    with Image.open(path) as image:
        if getattr(image, "n_frames", 1) > 1:
            raise RotateError("Rotating animated or multi-page images isn't supported")
        fmt = image.format
        info = dict(image.info)
        upright = ImageOps.exif_transpose(image)  # bake in any existing orientation first
        upright.load()
    method = {1: Image.Transpose.ROTATE_270, 2: Image.Transpose.ROTATE_180, 3: Image.Transpose.ROTATE_90}[turns]
    result = upright.transpose(method)
    options = {}
    exif = upright.getexif()
    if exif:
        exif[ORIENTATION_TAG] = 1
        options["exif"] = exif.tobytes()
    for key in ("dpi", "icc_profile", "transparency"):
        if key in info:
            options[key] = info[key]
    if fmt == "TIFF" and "compression" in info:
        options["compression"] = info["compression"]
    partial = f"{path}.{os.getpid()}.part"
    try:
        result.save(partial, format=fmt, **options)
        shutil.copymode(path, partial)
        os.replace(partial, path)
    finally:
        if os.path.exists(partial):
            os.remove(partial)


def _write_atomically(path: str, data: bytes) -> None:
    partial = f"{path}.{os.getpid()}.part"
    try:
        with open(partial, "wb") as out:
            out.write(data)
        shutil.copymode(path, partial)
        os.replace(partial, path)
    finally:
        if os.path.exists(partial):
            os.remove(partial)
