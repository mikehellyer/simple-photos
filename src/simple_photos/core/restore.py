"""Restoring from a backup made by backup.py.

Only files missing from the computer are copied back: nothing there is
ever overwritten or deleted, so a restore can't lose anything. That
covers both bringing back photos deleted by mistake and filling a new
computer.

Each folder in the backup goes back into the collection folder with the
same name ("Pictures" → your Pictures); one with no match goes inside
the first collection folder, under its own name.

Ratings, tags and captions come from the library copy in the backup and
are added to what's already here — a photo gets the backup's rating only
if it has none, tags are combined, a caption only fills an empty one.
"""
from __future__ import annotations

import json
import os
import sqlite3
from collections import Counter
from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional

from simple_photos.core.backup import LIBRARY_COPY, MANIFEST, FileToCopy, backup_root, folder_names, same_file


@dataclass
class RestorePlan:
    name: str  # the folder in the backup
    source: str  # its path
    target: str  # where it goes back to
    matched: bool  # target is the collection folder of the same name
    to_copy: list[FileToCopy] = field(default_factory=list)
    already_here: int = 0
    different: int = 0  # here too, but not the same: the computer's version is kept

    @property
    def bytes_to_copy(self) -> int:
        return sum(f.size for f in self.to_copy)


def find_backup(path: str) -> Optional[str]:
    """The backup folder at path: path itself if it is one, or its
    "Simple-Photos Backup" folder."""
    path = os.path.normpath(path)
    if os.path.basename(path) == os.path.basename(backup_root("")) and os.path.isdir(path):
        return path
    candidate = backup_root(path)
    return candidate if os.path.isdir(candidate) else None


def _skip(name: str) -> bool:
    return name.startswith(".") or name.endswith(".part")


def plan_restore(
    backup_dir: str,
    roots: list[str],
    progress: Optional[Callable[[int], None]] = None,
    should_stop: Optional[Callable[[], bool]] = None,
) -> list[RestorePlan]:
    roots = [os.path.normpath(os.path.abspath(r)) for r in roots]
    by_name = {name.lower(): root for root, name in folder_names(roots).items()}
    plans = []
    found = 0
    for name in sorted(os.listdir(backup_dir)):
        source = os.path.join(backup_dir, name)
        if _skip(name) or not os.path.isdir(source):
            continue
        root = by_name.get(name.lower())
        plan = RestorePlan(
            name=name,
            source=source,
            target=root or os.path.join(roots[0], name),
            matched=root is not None,
        )
        plans.append(plan)
        for dirpath, dirnames, filenames in os.walk(source):
            dirnames[:] = sorted(d for d in dirnames if not _skip(d))
            relative = os.path.relpath(dirpath, source)
            for filename in sorted(filenames):
                if _skip(filename):
                    continue
                if should_stop and should_stop():
                    return plans
                backed_up = os.path.join(dirpath, filename)
                target = os.path.normpath(os.path.join(plan.target, relative, filename))
                try:
                    backed_up_stat = os.stat(backed_up)
                except OSError:
                    continue
                try:
                    here = os.stat(target)
                except OSError:
                    plan.to_copy.append(FileToCopy(backed_up, target, backed_up_stat.st_size))
                    found += 1
                    if progress and found % 50 == 0:
                        progress(found)
                    continue
                if same_file(backed_up_stat, here):
                    plan.already_here += 1
                else:
                    plan.different += 1
    return plans


# --- ratings, tags and captions ---------------------------------------------


def _parts(path: str) -> list[str]:
    """A path's parts, whichever OS wrote it (the backup may come from a
    Windows computer and be restored on Linux, or the other way round)."""
    return [p for p in path.replace("\\", "/").split("/") if p]


def _old_roots(backup_dir: str, names: Iterable[str], old_paths: list[str]) -> dict[str, list[str]]:
    """Each backed-up folder's name → its path on the computer it was
    backed up from, as parts. From the backup's own note when there is
    one; otherwise (backups made before there was one) worked out from
    the library's paths: the commonest place a folder of that name was."""
    try:
        with open(os.path.join(backup_dir, MANIFEST), encoding="utf-8") as file:
            folders = json.load(file).get("folders", {})
        found = {name: _parts(path) for name, path in folders.items() if isinstance(path, str)}
        if found:
            return found
    except (OSError, ValueError, AttributeError):
        pass
    guesses: dict[str, list[str]] = {}
    for name in names:
        counts: Counter = Counter()
        for path in old_paths:
            parts = _parts(path)
            for i, part in enumerate(parts[:-1]):
                if part == name:
                    counts[tuple(parts[: i + 1])] += 1
                    break
        if counts:
            guesses[name] = list(counts.most_common(1)[0][0])
    return guesses


@dataclass
class BackedUpInfo:
    path: str  # where the photo is (or will be) on this computer
    rating: int
    caption: str
    tags: list[str]


def backed_up_info(backup_dir: str, plans: list[RestorePlan]) -> list[BackedUpInfo]:
    """Ratings, tags and captions from the backup's library copy, with
    each photo's path translated to where it is on this computer. Only
    photos that have any. Empty if there's no library copy."""
    db_path = os.path.join(backup_dir, LIBRARY_COPY)
    if not os.path.exists(db_path):
        return []
    try:
        db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            rows = db.execute("SELECT id, path, rating, caption FROM photos").fetchall()
            tag_rows = db.execute(
                "SELECT photo_tags.photo_id, tags.name FROM photo_tags JOIN tags ON tags.id = photo_tags.tag_id"
            ).fetchall()
        finally:
            db.close()
    except sqlite3.Error:
        return []
    tags: dict[int, list[str]] = {}
    for photo_id, name in tag_rows:
        tags.setdefault(photo_id, []).append(name)
    old_roots = _old_roots(backup_dir, [p.name for p in plans], [row[1] for row in rows])
    targets = {p.name: p.target for p in plans}
    info = []
    for photo_id, old_path, rating, caption in rows:
        if not (rating or caption or tags.get(photo_id)):
            continue
        parts = _parts(old_path)
        for name, root in old_roots.items():
            if name in targets and parts[: len(root)] == root and len(parts) > len(root):
                info.append(BackedUpInfo(os.path.join(targets[name], *parts[len(root):]), rating or 0, caption or "", tags.get(photo_id, [])))
                break
    return info
