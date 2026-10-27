"""Backing up the collection to a USB drive (or any folder).

Everything in the collection folders — photos, and anything else kept
alongside them, like videos — is copied into one folder on the drive,
"Simple-Photos Backup", mirroring the folders. Only new or changed files
are copied, so later backups are quick. Nothing is ever deleted from the
backup: a photo deleted from the computer keeps its copy there.

A snapshot of the library (ratings, tags, captions) is saved alongside,
since those live in Simple-Photos' database, not in the photos.

"Changed" is judged by size and modified time. USB drives are mostly
FAT32 or exFAT, which keep times to the nearest 2 seconds and in local
time — so a time within 2 seconds, or exactly an hour out (a clock
change between backups), still counts as the same file.
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional

BACKUP_FOLDER = "Simple-Photos Backup"
LIBRARY_COPY = "Simple-Photos library.db"
TIME_SLACK = 2.0  # seconds


@dataclass
class FileToCopy:
    source: str
    target: str
    size: int


@dataclass
class FolderPlan:
    """What backing up one collection folder involves."""

    root: str
    target: str  # its folder inside the backup
    to_copy: list[FileToCopy] = field(default_factory=list)
    up_to_date: int = 0
    unreadable: int = 0

    @property
    def bytes_to_copy(self) -> int:
        return sum(f.size for f in self.to_copy)


@dataclass
class BackupResult:
    copied: int = 0
    bytes_copied: int = 0
    failed: list[tuple[str, str]] = field(default_factory=list)  # (source, reason)
    stopped: bool = False
    library_saved: bool = False


def backup_root(destination: str) -> str:
    return os.path.join(destination, BACKUP_FOLDER)


def folder_names(roots: Iterable[str]) -> dict[str, str]:
    """Each collection folder → its folder name inside the backup: its own
    name ("Pictures"), with " (2)" added if two share one."""
    names: dict[str, str] = {}
    used: set[str] = set()
    for root in roots:
        base = os.path.basename(os.path.normpath(root)) or "Collection"
        name, n = base, 1
        while name.lower() in used:
            n += 1
            name = f"{base} ({n})"
        used.add(name.lower())
        names[root] = name
    return names


def same_file(source: os.stat_result, target: os.stat_result) -> bool:
    if source.st_size != target.st_size:
        return False
    gap = abs(source.st_mtime - target.st_mtime)
    return gap <= TIME_SLACK or abs(gap - 3600) <= TIME_SLACK


def _skip(name: str) -> bool:
    return name.startswith(".") or name.endswith(".part")


def plan_backup(
    roots: Iterable[str],
    destination: str,
    progress: Optional[Callable[[int], None]] = None,
    should_stop: Optional[Callable[[], bool]] = None,
) -> list[FolderPlan]:
    """Compare each collection folder with its copy in the backup. Doesn't
    write anything."""
    roots = [os.path.normpath(os.path.abspath(r)) for r in roots]
    names = folder_names(roots)
    backup = os.path.normcase(os.path.abspath(backup_root(destination)))
    plans = []
    seen = 0
    for root in roots:
        plan = FolderPlan(root=root, target=os.path.join(backup_root(destination), names[root]))
        plans.append(plan)
        if not os.path.isdir(root):
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            # Never back up the backup (if it's inside the collection).
            dirnames[:] = sorted(
                d for d in dirnames
                if not _skip(d) and os.path.normcase(os.path.abspath(os.path.join(dirpath, d))) != backup
            )
            relative = os.path.relpath(dirpath, root)
            for name in sorted(filenames):
                if _skip(name):
                    continue
                if should_stop and should_stop():
                    return plans
                source = os.path.join(dirpath, name)
                try:
                    source_stat = os.lstat(source)
                except OSError:
                    plan.unreadable += 1
                    continue
                if not stat.S_ISREG(source_stat.st_mode):
                    continue  # links and the like
                target = os.path.normpath(os.path.join(plan.target, relative, name))
                try:
                    if same_file(source_stat, os.stat(target)):
                        plan.up_to_date += 1
                        continue
                except OSError:
                    pass  # not backed up yet
                plan.to_copy.append(FileToCopy(source, target, source_stat.st_size))
                seen += 1
                if progress and seen % 50 == 0:
                    progress(seen)
    return plans


def free_space(path: str) -> Optional[int]:
    try:
        return shutil.disk_usage(path).free
    except OSError:
        return None


def run_backup(
    files: list[FileToCopy],
    progress: Optional[Callable[[int, int], None]] = None,
    should_stop: Optional[Callable[[], bool]] = None,
) -> BackupResult:
    """Copy each file (a changed one replaces its old backup copy). Each
    goes to a .part file first and is renamed once complete, keeping the
    original's dates. progress(bytes done, total bytes)."""
    result = BackupResult()
    total = sum(f.size for f in files)
    for item in files:
        if should_stop and should_stop():
            result.stopped = True
            break
        partial = item.target + ".part"
        try:
            os.makedirs(os.path.dirname(item.target), exist_ok=True)
            shutil.copyfile(item.source, partial)
            try:
                shutil.copystat(item.source, partial)
            except OSError:
                pass  # some USB file systems can't take every attribute; the copy is what counts
            os.replace(partial, item.target)
        except OSError as error:
            result.failed.append((item.source, error.strerror or str(error)))
            try:
                os.remove(partial)
            except OSError:
                pass
        else:
            result.copied += 1
            result.bytes_copied += item.size
        if progress:
            progress(result.bytes_copied, total)
    return result


def save_library(db_path: str | Path, destination: str) -> bool:
    """A consistent snapshot of the library (even while it's in use) next
    to the backed-up folders. False if it couldn't be written."""
    target = os.path.join(backup_root(destination), LIBRARY_COPY)
    partial = target + ".part"
    try:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        source = sqlite3.connect(str(db_path))
        try:
            copy = sqlite3.connect(partial)
            try:
                source.backup(copy)
            finally:
                copy.close()
        finally:
            source.close()
        os.replace(partial, target)
    except (OSError, sqlite3.Error):
        try:
            os.remove(partial)
        except OSError:
            pass
        return False
    return True
