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
REMOVED_FOLDER = "Removed from computer"  # inside the backup: what tidying up keeps
LIBRARY_COPY = "Simple-Photos library.db"
MANIFEST = "Simple-Photos backup.json"  # which folder in the backup came from where
TIME_SLACK = 2.0  # seconds


@dataclass
class FileToCopy:
    source: str
    target: str
    size: int


@dataclass
class Move:
    """A backed-up file to move within the backup, because it was moved or
    renamed (or its folder was) on the computer."""

    old: str
    new: str
    size: int
    source: str = ""  # the file on the computer, to copy instead if the move can't be done


@dataclass
class FolderPlan:
    """What backing up one collection folder involves."""

    root: str
    target: str  # its folder inside the backup
    to_copy: list[FileToCopy] = field(default_factory=list)
    moves: list[Move] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)  # in the backup, deleted from the computer
    leftovers: list[str] = field(default_factory=list)  # in the backup twice: an old place's copy
    up_to_date: int = 0
    unreadable: int = 0

    @property
    def bytes_to_copy(self) -> int:
        return sum(f.size for f in self.to_copy)


@dataclass
class BackupResult:
    copied: int = 0
    bytes_copied: int = 0
    skipped: int = 0  # already there, when not replacing
    moved: int = 0  # followed moves and renames
    tidied: Optional["TidyResult"] = None
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


def same_content(a: str, b: str, chunk: int = 65536) -> bool:
    """Whether two files of the same size hold the same data, judged by
    their first and last 64 KB — enough to tell photos apart, without
    reading whole files from a slow USB drive."""
    try:
        size = os.path.getsize(a)
        if size != os.path.getsize(b):
            return False
        with open(a, "rb") as fa, open(b, "rb") as fb:
            if fa.read(chunk) != fb.read(chunk):
                return False
            if size > chunk:
                fa.seek(max(chunk, size - chunk))
                fb.seek(max(chunk, size - chunk))
                return fa.read(chunk) == fb.read(chunk)
            return True
    except OSError:
        return False


def _walk_files(top: str, skip_dir=None):
    """(relative path, full path, stat) of every ordinary file under top."""
    for dirpath, dirnames, filenames in os.walk(top):
        dirnames[:] = sorted(
            d for d in dirnames if not _skip(d) and not (skip_dir and skip_dir(os.path.join(dirpath, d)))
        )
        for name in sorted(filenames):
            if _skip(name):
                continue
            path = os.path.join(dirpath, name)
            try:
                info = os.lstat(path)
            except OSError:
                yield os.path.relpath(path, top), path, None
                continue
            if stat.S_ISREG(info.st_mode):
                yield os.path.relpath(path, top), path, info


def plan_backup(
    roots: Iterable[str],
    destination: str,
    progress: Optional[Callable[[int], None]] = None,
    should_stop: Optional[Callable[[], bool]] = None,
) -> list[FolderPlan]:
    """Compare each collection folder with its copy in the backup. Doesn't
    write anything.

    A file missing from the backup that matches one the backup has at a
    place no longer on the computer (same size, date and content) was
    moved or renamed: it becomes a Move, not a copy. Of what's left at
    old places, copies of something the backup already has elsewhere are
    leftovers (from backups made before moves were followed); the rest
    were deleted from the computer."""
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
        computer: dict[str, tuple[str, os.stat_result]] = {}
        for relative, path, info in _walk_files(
            root, skip_dir=lambda d: os.path.normcase(os.path.abspath(d)) == backup  # never back up the backup
        ):
            if should_stop and should_stop():
                return plans
            if info is None:
                plan.unreadable += 1
            else:
                computer[relative] = (path, info)
        backed_up: dict[str, tuple[str, os.stat_result]] = {}
        if os.path.isdir(plan.target):
            for relative, path, info in _walk_files(plan.target):
                if info is not None:
                    backed_up[relative] = (path, info)

        missing: list[tuple[str, str, os.stat_result]] = []
        for relative, (path, info) in computer.items():
            there = backed_up.get(relative)
            if there is not None and same_file(info, there[1]):
                plan.up_to_date += 1
            elif there is not None:
                plan.to_copy.append(FileToCopy(path, there[0], info.st_size))  # changed
            else:
                missing.append((relative, path, info))

        # Old places: backed-up files whose place is gone from the computer.
        stale_by_size: dict[int, list[str]] = {}
        for relative, (_, info) in backed_up.items():
            if relative not in computer:
                stale_by_size.setdefault(info.st_size, []).append(relative)

        for relative, path, info in missing:
            if should_stop and should_stop():
                return plans
            candidates = [r for r in stale_by_size.get(info.st_size, []) if same_file(info, backed_up[r][1])]
            # The same file name first: moving a photo or renaming its folder keeps it.
            candidates.sort(key=lambda r: os.path.basename(r) != os.path.basename(relative))
            match = next((r for r in candidates if same_content(path, backed_up[r][0])), None)
            new_path = os.path.normpath(os.path.join(plan.target, relative))
            if match is not None:
                stale_by_size[info.st_size].remove(match)
                plan.moves.append(Move(backed_up[match][0], new_path, info.st_size, source=path))
            else:
                plan.to_copy.append(FileToCopy(path, new_path, info.st_size))
                seen += 1
                if progress and seen % 50 == 0:
                    progress(seen)

        # What the backup will hold where the computer has it, for spotting leftovers.
        current_by_size: dict[int, list[str]] = {}
        for relative, (path, info) in computer.items():
            there = backed_up.get(relative)
            if there is not None and same_file(info, there[1]):
                current_by_size.setdefault(info.st_size, []).append(there[0])
        for move in plan.moves:
            current_by_size.setdefault(move.size, []).append(move.old)  # its content, about to be in place
        for size, relatives in stale_by_size.items():
            for relative in relatives:
                old_path, info = backed_up[relative]
                if any(
                    same_file(info, os.stat(other)) and same_content(old_path, other)
                    for other in current_by_size.get(size, [])
                    if os.path.exists(other)
                ):
                    plan.leftovers.append(old_path)
                else:
                    plan.removed.append(old_path)
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
    replace: bool = True,
) -> BackupResult:
    """Copy each file — a changed one replacing its old copy, unless
    replace is False (restoring), when a file that's already there is left
    exactly as it is. Each goes to a .part file first and is renamed once
    complete, keeping the original's dates. progress(bytes done, total)."""
    result = BackupResult()
    total = sum(f.size for f in files)
    for item in files:
        if should_stop and should_stop():
            result.stopped = True
            break
        if not replace and os.path.lexists(item.target):
            result.skipped += 1
            continue
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


def write_manifest(roots: Iterable[str], destination: str) -> None:
    """Note which collection folder each backed-up folder came from, so a
    restore — maybe on another computer — can match photos to their
    ratings and tags. Never raises."""
    import datetime as _dt
    import json

    roots = [os.path.normpath(os.path.abspath(r)) for r in roots]
    data = {
        "app": "Simple-Photos",
        "saved": _dt.datetime.now().isoformat(timespec="seconds"),
        "folders": {name: root for root, name in folder_names(roots).items()},
    }
    target = os.path.join(backup_root(destination), MANIFEST)
    try:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target + ".part", "w", encoding="utf-8") as out:
            json.dump(data, out, indent=2)
        os.replace(target + ".part", target)
    except OSError:
        pass


def run_moves(moves: list[Move]) -> tuple[int, list[Move]]:
    """Rearrange the backup to match moves and renames on the computer —
    within the drive, so it's quick. Returns (how many moved, the ones
    that couldn't be, to copy afresh instead)."""
    done, could_not = 0, []
    for move in moves:
        if os.path.lexists(move.new):
            could_not.append(move)
            continue
        try:
            os.makedirs(os.path.dirname(move.new), exist_ok=True)
            os.replace(move.old, move.new)
        except OSError:
            could_not.append(move)
        else:
            done += 1
    return done, could_not


@dataclass
class TidyResult:
    put_aside: int = 0  # moved into "Removed from computer"
    leftovers_removed: int = 0
    failed: list[tuple[str, str]] = field(default_factory=list)


def tidy_up(plans: list[FolderPlan], destination: str) -> TidyResult:
    """Move files deleted from the computer into "Removed from computer"
    in the backup (kept, just out of the way), and delete leftover copies
    — each the same as a file the backup keeps at its current place.
    Then remove folders left empty in the backup."""
    from simple_photos.core.fileops import free_name

    result = TidyResult()
    root = backup_root(destination)
    removed_root = os.path.join(root, REMOVED_FOLDER)
    for plan in plans:
        for path in plan.removed:
            relative = os.path.relpath(path, root)  # keeps the folder's own name
            target = os.path.join(removed_root, relative)
            try:
                os.makedirs(os.path.dirname(target), exist_ok=True)
                target = os.path.join(os.path.dirname(target), free_name(os.path.dirname(target), os.path.basename(target)))
                os.replace(path, target)
            except OSError as error:
                result.failed.append((path, error.strerror or str(error)))
            else:
                result.put_aside += 1
        for path in plan.leftovers:
            try:
                os.remove(path)
            except OSError as error:
                result.failed.append((path, error.strerror or str(error)))
            else:
                result.leftovers_removed += 1
    prune_empty_folders(root, keep=[removed_root])
    return result


def prune_empty_folders(root: str, keep: Iterable[str] = ()) -> None:
    """Remove empty folders under root (never root itself). Never raises."""
    keep = {os.path.normcase(os.path.abspath(k)) for k in keep} | {os.path.normcase(os.path.abspath(root))}
    for dirpath, dirnames, filenames in os.walk(root, topdown=False):
        if os.path.normcase(os.path.abspath(dirpath)) in keep:
            continue
        try:
            if not os.listdir(dirpath):
                os.rmdir(dirpath)
        except OSError:
            pass
