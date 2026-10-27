"""Importing photos from a device (or any folder) into the collection.

Everything found is copied into one new folder named with the day of
the import. Nothing on the device is changed. Photos the library already
has — the same file size and the same date taken — can be skipped, so
putting the same card in again doesn't make copies; optionally each of
those is first compared byte for byte with the library's copy, and only
skipped if they really are identical.

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


def not_really_imported(
    photos: list[SourcePhoto],
    matches: dict[tuple[int, str], list[str]],
    progress: Optional[Callable[[int, int], None]] = None,
    should_stop: Optional[Callable[[], bool]] = None,
) -> list[SourcePhoto]:
    """The byte-for-byte check: of the photos that look already imported
    (same size and date taken as one in the library), those that aren't
    actually identical to any of the library's matching files — so they
    should be imported after all. matches: (size, taken) → library paths.
    A library file that can't be read doesn't count as a match."""
    import filecmp

    different = []
    for done, photo in enumerate(photos, start=1):
        if should_stop and should_stop():
            break
        identical = False
        for library_path in matches.get((photo.size, photo.taken), []):
            try:
                if filecmp.cmp(photo.path, library_path, shallow=False):
                    identical = True
                    break
            except OSError:
                continue
        if not identical:
            different.append(photo)
        if progress:
            progress(done, len(photos))
    return different


PERIOD_UNITS = ("all", "days", "weeks", "months")


def cutoff_date(count: int, unit: str, today: Optional[_dt.date] = None) -> Optional[_dt.date]:
    """The first day of "the last <count> <unit>": photos taken on or after
    it are imported. None for "all". A month back from 31 March is the
    last day of February."""
    if unit not in ("days", "weeks", "months") or count < 1:
        return None
    today = today or _dt.date.today()
    if unit == "days":
        return today - _dt.timedelta(days=count)
    if unit == "weeks":
        return today - _dt.timedelta(weeks=count)
    month_index = today.year * 12 + (today.month - 1) - count
    year, month = divmod(month_index, 12)
    month += 1
    last_day = (_dt.date(year + (month == 12), month % 12 + 1, 1) - _dt.timedelta(days=1)).day
    return _dt.date(year, month, min(today.day, last_day))


def taken_since(photos: list[SourcePhoto], cutoff: Optional[_dt.date]) -> list[SourcePhoto]:
    """The photos taken on or after cutoff (all of them if it's None). A
    photo with no date at all is kept rather than silently left behind."""
    if cutoff is None:
        return list(photos)
    first = cutoff.isoformat()
    return [p for p in photos if not p.taken or p.taken[:10] >= first]


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
