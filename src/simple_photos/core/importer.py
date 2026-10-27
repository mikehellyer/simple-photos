"""Importing photos from a device (or any folder) into the collection.

Everything found is copied into one new folder named with the day of
the import. Nothing on the device is changed. Photos the library already
has — the same file size and the same date taken — can be skipped, so
putting the same card in again doesn't make copies.

Each file is copied to a .part file first and renamed once complete, so
a stopped import never leaves half a photo behind; the copy keeps the
original's dates.
"""
from __future__ import annotations

import datetime as _dt
import os
import shutil
import stat
from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional

from simple_photos.core.fileops import folder_name_problem, free_name
from simple_photos.core.metadata import file_date, is_photo_file, read_photo_info

# System folders on removable drives, never worth looking through.
_SKIP_FOLDERS = {"$recycle.bin", "system volume information", "lost+found", "found.000", "recycler"}


@dataclass
class SourcePhoto:
    path: str
    size: int
    taken: str
    already_imported: bool = False


@dataclass
class DeviceScan:
    photos: list[SourcePhoto]
    stopped: bool = False

    @property
    def new_photos(self) -> list[SourcePhoto]:
        return [p for p in self.photos if not p.already_imported]

    @property
    def total_size(self) -> int:
        return sum(p.size for p in self.photos)

    def date_range(self) -> tuple[str, str] | None:
        dates = sorted(p.taken[:10] for p in self.photos if p.taken)
        return (dates[0], dates[-1]) if dates else None


@dataclass
class ImportResult:
    folder: str
    copied: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)  # (source path, reason)
    stopped: bool = False


def find_photos(
    root: str,
    known: Iterable[tuple[int, str]] = (),
    progress: Optional[Callable[[int], None]] = None,
    should_stop: Optional[Callable[[], bool]] = None,
) -> DeviceScan:
    """Every photo on the device, with its date taken. known: (file size,
    date taken) of photos already in the library."""
    known = set(known)
    photos: list[SourcePhoto] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith(".") and d.lower() not in _SKIP_FOLDERS)
        for name in sorted(filenames):
            if not is_photo_file(name):
                continue
            if should_stop and should_stop():
                return DeviceScan(photos, stopped=True)
            path = os.path.join(dirpath, name)
            try:
                size = os.path.getsize(path)
            except OSError:
                continue
            try:
                taken = read_photo_info(path).taken or ""
            except Exception:  # unreadable header: still import it
                try:
                    taken = file_date(path)
                except OSError:
                    taken = ""
            photos.append(SourcePhoto(path, size, taken, already_imported=(size, taken) in known))
            if progress:
                progress(len(photos))
    return DeviceScan(photos)


def safe_folder_name(name: str) -> str:
    """name with anything not allowed in a folder name on some OS replaced."""
    cleaned = "".join("-" if folder_name_problem(ch) and ch not in " .," else ch for ch in name)
    cleaned = " ".join(cleaned.split()).strip(" .")
    return cleaned or "Import"


def default_folder_name(device_name: str, today: Optional[_dt.date] = None) -> str:
    """'2026-10-27 KINGSTON' — the import's date first, so imports sort by date."""
    today = today or _dt.date.today()
    return safe_folder_name(f"{today.isoformat()} {device_name}")


def import_photos(
    photos: list[SourcePhoto],
    parent: str,
    folder_name: str,
    progress: Optional[Callable[[int, int], None]] = None,
    should_stop: Optional[Callable[[], bool]] = None,
) -> ImportResult:
    """Copy photos into parent/folder_name (or 'folder_name (2)' if that
    already exists). Never overwrites; one failure doesn't stop the rest."""
    problem = folder_name_problem(folder_name)
    if problem:
        raise ValueError(problem)
    folder = os.path.join(parent, free_name(parent, folder_name))
    os.makedirs(folder)
    result = ImportResult(folder=folder)
    taken_names: set[str] = set()
    for done, photo in enumerate(photos, start=1):
        if should_stop and should_stop():
            result.stopped = True
            break
        name = free_name(folder, os.path.basename(photo.path), taken_names)
        taken_names.add(name.lower())
        target = os.path.join(folder, name)
        partial = target + ".part"
        try:
            shutil.copyfile(photo.path, partial)
            shutil.copystat(photo.path, partial)
            # Cameras mark protected shots read-only; the copy should still be editable.
            os.chmod(partial, os.stat(partial).st_mode | stat.S_IWUSR)
            os.replace(partial, target)
        except OSError as error:
            result.failed.append((photo.path, error.strerror or str(error)))
            try:
                os.remove(partial)
            except OSError:
                pass
        else:
            result.copied.append(target)
        if progress:
            progress(done, len(photos))
    return result
