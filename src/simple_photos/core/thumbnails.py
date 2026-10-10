"""Loading photos the right way up, and the on-disk thumbnail cache.

Decoding a 24-megapixel JPEG just to draw a 200-pixel tile is slow, so
each photo's thumbnail is made once and kept as a small JPEG in the
user's cache folder. The cache file's name comes from the photo's path
and modified time, so editing a photo makes a fresh thumbnail and the
old one is simply never asked for again.
"""
from __future__ import annotations

import hashlib
import os
import threading
from pathlib import Path
from typing import Optional

from PIL import Image, ImageOps

from simple_photos.core import metadata  # noqa: F401  (registers the HEIF opener)
from simple_photos.core.diagnostics import note_current_file

THUMBNAIL_SIZE = 400  # longest side, in pixels — sharp at the grid's largest size


def thumbnail_cache_dir() -> Path:
    from platformdirs import user_cache_dir

    return Path(user_cache_dir("Simple-Photos")) / "thumbnails"


def load_image(path: str | Path, max_size: Optional[int] = None) -> Image.Image:
    """The photo as RGB/RGBA, rotated as its EXIF orientation says, and
    no bigger than max_size on its longest side if given. Raises OSError
    if it can't be decoded."""
    note_current_file(str(path))
    with Image.open(path) as image:
        if max_size and image.format == "JPEG":
            # Let libjpeg decode at 1/2, 1/4 or 1/8 scale — far faster.
            image.draft("RGB", (max_size, max_size))
        image = ImageOps.exif_transpose(image)
        if max_size:
            image.thumbnail((max_size, max_size), Image.LANCZOS)
        if image.mode not in ("RGB", "RGBA"):
            image = image.convert("RGBA" if "A" in image.getbands() or "transparency" in image.info else "RGB")
        else:
            image.load()
        return image


def thumbnail_path(photo_path: str | Path, mtime: float, cache_dir: Path, size: int = THUMBNAIL_SIZE) -> Path:
    key = hashlib.sha1(f"{os.path.abspath(photo_path)}|{mtime}|{size}".encode("utf-8", "surrogatepass")).hexdigest()
    return cache_dir / key[:2] / f"{key}.jpg"


def ensure_thumbnail(
    photo_path: str | Path, mtime: float, cache_dir: Optional[Path] = None, size: int = THUMBNAIL_SIZE
) -> Optional[Path]:
    """The cached thumbnail's path, making it first if need be. None if
    the photo can't be decoded."""
    cache_dir = cache_dir or thumbnail_cache_dir()
    dest = thumbnail_path(photo_path, mtime, cache_dir, size)
    if dest.exists():
        return dest
    try:
        image = load_image(photo_path, size)
    except Exception:  # Pillow raises all sorts on a corrupt file
        return None
    if image.mode == "RGBA":
        # JPEG has no transparency: put see-through parts on white.
        background = Image.new("RGB", image.size, (255, 255, 255))
        background.paste(image, mask=image.getchannel("A"))
        image = background
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        partial = dest.with_suffix(f".{os.getpid()}-{threading.get_ident()}.part")
        image.save(partial, "JPEG", quality=85)
        os.replace(partial, dest)
    except OSError:
        return None
    return dest


def move_thumbnail(
    old_path: str | Path, new_path: str | Path, mtime: float, cache_dir: Optional[Path] = None, size: int = THUMBNAIL_SIZE
) -> None:
    """A photo was moved: re-file its cached thumbnail under the new path
    so it needn't be made again. Never raises."""
    cache_dir = cache_dir or thumbnail_cache_dir()
    old = thumbnail_path(old_path, mtime, cache_dir, size)
    new = thumbnail_path(new_path, mtime, cache_dir, size)
    try:
        new.parent.mkdir(parents=True, exist_ok=True)
        os.replace(old, new)
    except OSError:
        pass
