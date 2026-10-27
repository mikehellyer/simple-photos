"""The photo library: a SQLite database of every photo found under the
user's collection folders, with the ratings, tags and captions they've
given them.

Scanning is incremental — a file whose size and modified-time haven't
changed since the last scan isn't re-read — so rescanning a large
collection is quick. A changed file is re-read in place, keeping its
rating and tags. Files that have disappeared are dropped.

A Library is used from one thread at a time. The GUI scans on a
background thread with its own Library instance on the same database
file, then refreshes its views from the main thread's instance.
"""
from __future__ import annotations

import os
import sqlite3
import time
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Callable, Iterable, Optional

from platformdirs import user_data_dir

from simple_photos.core.metadata import PhotoInfo, file_date, is_photo_file, read_photo_info

SCHEMA = """
CREATE TABLE IF NOT EXISTS photos (
    id INTEGER PRIMARY KEY,
    path TEXT NOT NULL UNIQUE,
    folder TEXT NOT NULL,
    filename TEXT NOT NULL,
    file_size INTEGER NOT NULL DEFAULT 0,
    file_mtime REAL NOT NULL DEFAULT 0,
    date_added REAL NOT NULL,
    taken TEXT NOT NULL,
    width INTEGER NOT NULL DEFAULT 0,
    height INTEGER NOT NULL DEFAULT 0,
    camera_make TEXT NOT NULL DEFAULT '',
    camera_model TEXT NOT NULL DEFAULT '',
    lens TEXT NOT NULL DEFAULT '',
    iso INTEGER,
    exposure_time REAL,
    f_number REAL,
    focal_length REAL,
    gps_lat REAL,
    gps_lon REAL,
    rating INTEGER NOT NULL DEFAULT 0,
    caption TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_photos_folder ON photos(folder);
CREATE INDEX IF NOT EXISTS idx_photos_taken ON photos(taken);
-- Every folder under the collection, including empty ones, so a folder
-- that has just been made (or emptied) still shows in the folder tree.
CREATE TABLE IF NOT EXISTS folders (
    path TEXT PRIMARY KEY
);
CREATE TABLE IF NOT EXISTS tags (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE
);
CREATE TABLE IF NOT EXISTS photo_tags (
    photo_id INTEGER NOT NULL REFERENCES photos(id) ON DELETE CASCADE,
    tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    PRIMARY KEY (photo_id, tag_id)
);
-- Fingerprints for Find Duplicates, valid while the file's size and
-- modified time match. digest: the file's BLAKE2 hash (only made for
-- photos that share a size with another); dhashes: the look-alike
-- hashes of the photo as it is and turned 90°, 180° and 270°, in hex.
CREATE TABLE IF NOT EXISTS fingerprints (
    photo_id INTEGER PRIMARY KEY REFERENCES photos(id) ON DELETE CASCADE,
    file_size INTEGER NOT NULL,
    file_mtime REAL NOT NULL,
    digest TEXT,
    dhashes TEXT
);
"""

SCAN_BATCH = 200  # photos read between commits (and progress reports)

SORTS = {
    "taken": "taken {dir}, filename COLLATE NOCASE",
    "name": "filename COLLATE NOCASE {dir}, taken",
    "rating": "rating {dir}, taken",
    "added": "date_added {dir}, taken",
    "size": "file_size {dir}, taken",
}

_INFO_COLUMNS = [f.name for f in fields(PhotoInfo)]


def default_db_path() -> Path:
    return Path(user_data_dir("Simple-Photos")) / "library.db"


@dataclass
class PhotoRow:
    """What the thumbnail grid needs for one photo."""

    id: int
    path: str
    filename: str
    taken: str
    rating: int
    file_mtime: float


@dataclass
class Photo(PhotoInfo):
    """Everything known about one photo, for the info panel."""

    id: int = 0
    path: str = ""
    folder: str = ""
    filename: str = ""
    file_size: int = 0
    file_mtime: float = 0.0
    date_added: float = 0.0
    rating: int = 0
    caption: str = ""


@dataclass
class PhotoFilter:
    folder: Optional[str] = None
    include_subfolders: bool = True
    year: Optional[int] = None
    month: Optional[int] = None
    tag_id: Optional[int] = None
    untagged: bool = False
    min_rating: int = 0
    text: str = ""
    sort: str = "taken"
    descending: bool = False
    ids: Optional[frozenset[int]] = None  # just these photos (picked on the map)


@dataclass
class ScanResult:
    added: int = 0
    updated: int = 0
    removed: int = 0
    failed: int = 0


def _like_prefix(folder: str) -> str:
    escaped = folder.rstrip("/\\").replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return escaped + ("\\\\" if os.sep == "\\" else os.sep) + "%"


def _norm(path: str | Path) -> str:
    return os.path.normpath(os.path.abspath(str(path)))


def _is_under(path: str, roots: list[str]) -> bool:
    return any(path == root or path.startswith(root.rstrip(os.sep) + os.sep) for root in roots)


class Library:
    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = Path(db_path or default_db_path())
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(self.db_path), timeout=30)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys = ON")
        self._db.execute("PRAGMA journal_mode = WAL")
        self._db.executescript(SCHEMA)

    def close(self) -> None:
        self._db.close()

    # --- scanning -------------------------------------------------------

    def scan(
        self,
        roots: Iterable[str | Path],
        progress: Optional[Callable[[int, str], None]] = None,
        should_stop: Optional[Callable[[], bool]] = None,
    ) -> ScanResult:
        """Bring the database in line with the photos on disk under roots.
        progress(files_seen, current_folder) is called every SCAN_BATCH
        files, just after a commit, so another connection can already see
        what's been found so far. A root that isn't there right now (an
        unplugged drive) keeps its photos rather than losing their tags."""
        roots = [_norm(r) for r in roots]
        result = ScanResult()
        known = {
            row["path"]: (row["file_size"], row["file_mtime"])
            for row in self._db.execute("SELECT path, file_size, file_mtime FROM photos")
        }
        seen: set[str] = set()
        seen_folders: set[str] = set()
        available_roots = [r for r in roots if os.path.isdir(r)]
        count = 0
        for root in available_roots:
            for dirpath, dirnames, filenames in os.walk(root):
                if should_stop and should_stop():
                    self._db.commit()
                    return result
                dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
                seen_folders.add(dirpath)
                for name in sorted(filenames):
                    if not is_photo_file(name):
                        continue
                    if should_stop and should_stop():
                        self._db.commit()
                        return result
                    path = os.path.join(dirpath, name)
                    try:
                        stat = os.stat(path)
                    except OSError:
                        continue
                    seen.add(path)
                    previous = known.get(path)
                    if previous == (stat.st_size, stat.st_mtime):
                        continue
                    self._store(path, stat, exists=previous is not None, result=result)
                    count += 1
                    if count % SCAN_BATCH == 0:
                        self._db.commit()
                        if progress:
                            progress(count, dirpath)

        gone = [
            path
            for path in known
            if path not in seen and (not _is_under(path, roots) or _is_under(path, available_roots))
        ]
        for start in range(0, len(gone), 500):
            chunk = gone[start : start + 500]
            self._db.execute(f"DELETE FROM photos WHERE path IN ({','.join('?' * len(chunk))})", chunk)
        result.removed = len(gone)
        self._sync_folders(seen_folders, roots, available_roots)
        self._db.execute("DELETE FROM tags WHERE id NOT IN (SELECT DISTINCT tag_id FROM photo_tags)")
        self._db.commit()
        if progress:
            progress(count, "")
        return result

    def _sync_folders(self, seen: set[str], roots: list[str], available_roots: list[str]) -> None:
        known = {row[0] for row in self._db.execute("SELECT path FROM folders")}
        stale = [
            path
            for path in known - seen
            if not _is_under(path, roots) or _is_under(path, available_roots)
        ]
        self._db.executemany("DELETE FROM folders WHERE path = ?", [(path,) for path in stale])
        self._db.executemany("INSERT OR IGNORE INTO folders (path) VALUES (?)", [(path,) for path in seen - known])

    def _store(self, path: str, stat: os.stat_result, exists: bool, result: ScanResult) -> None:
        try:
            info = read_photo_info(path)
        except Exception:  # not really an image, or truncated — still list it
            info = PhotoInfo(taken=file_date(path))
            result.failed += 1
        values = {name: getattr(info, name) for name in _INFO_COLUMNS}
        values.update(
            path=path,
            folder=os.path.dirname(path),
            filename=os.path.basename(path),
            file_size=stat.st_size,
            file_mtime=stat.st_mtime,
        )
        if exists:
            assignments = ", ".join(f"{name} = :{name}" for name in values if name != "path")
            self._db.execute(f"UPDATE photos SET {assignments} WHERE path = :path", values)
            # The file changed (a rotation can even keep its size and date).
            self._db.execute("DELETE FROM fingerprints WHERE photo_id = (SELECT id FROM photos WHERE path = ?)", (path,))
            result.updated += 1
        else:
            values["date_added"] = time.time()
            columns = ", ".join(values)
            self._db.execute(
                f"INSERT INTO photos ({columns}) VALUES ({', '.join(':' + c for c in values)})", values
            )
            result.added += 1

    # --- browsing -------------------------------------------------------

    def count(self) -> int:
        return self._db.execute("SELECT COUNT(*) FROM photos").fetchone()[0]

    def folder_counts(self) -> dict[str, int]:
        """Photos directly in each folder (not counting subfolders).
        Folders with no photos in them are included, with 0."""
        counts = {row[0]: 0 for row in self._db.execute("SELECT path FROM folders")}
        counts.update({row[0]: row[1] for row in self._db.execute("SELECT folder, COUNT(*) FROM photos GROUP BY folder")})
        return counts

    def date_counts(self) -> list[tuple[int, int, int]]:
        """(year, month, photos) for every month that has any, oldest first."""
        rows = self._db.execute(
            "SELECT CAST(substr(taken, 1, 4) AS INTEGER) AS y, CAST(substr(taken, 6, 2) AS INTEGER) AS m, COUNT(*) "
            "FROM photos GROUP BY y, m ORDER BY y, m"
        )
        return [(row[0], row[1], row[2]) for row in rows]

    def tag_counts(self) -> list[tuple[int, str, int]]:
        """(tag id, name, photos) for every tag, alphabetically."""
        rows = self._db.execute(
            "SELECT tags.id, tags.name, COUNT(photo_tags.photo_id) FROM tags "
            "LEFT JOIN photo_tags ON photo_tags.tag_id = tags.id "
            "GROUP BY tags.id ORDER BY tags.name COLLATE NOCASE"
        )
        return [(row[0], row[1], row[2]) for row in rows]

    def untagged_count(self) -> int:
        return self._db.execute(
            "SELECT COUNT(*) FROM photos WHERE id NOT IN (SELECT photo_id FROM photo_tags)"
        ).fetchone()[0]

    def query(self, photo_filter: PhotoFilter) -> list[PhotoRow]:
        where, params = [], []
        f = photo_filter
        if f.folder:
            folder = _norm(f.folder)
            if f.include_subfolders:
                where.append("(folder = ? OR folder LIKE ? ESCAPE '\\')")
                params += [folder, _like_prefix(folder)]
            else:
                where.append("folder = ?")
                params.append(folder)
        if f.year:
            where.append("substr(taken, 1, 4) = ?")
            params.append(f"{f.year:04d}")
            if f.month:
                where.append("substr(taken, 6, 2) = ?")
                params.append(f"{f.month:02d}")
        if f.tag_id is not None:
            where.append("id IN (SELECT photo_id FROM photo_tags WHERE tag_id = ?)")
            params.append(f.tag_id)
        if f.untagged:
            where.append("id NOT IN (SELECT photo_id FROM photo_tags)")
        if f.min_rating:
            where.append("rating >= ?")
            params.append(f.min_rating)
        if f.ids is not None:
            # Safe to inline: they're ints from our own database.
            where.append(f"id IN ({','.join(str(int(i)) for i in f.ids) or 'NULL'})")
        for word in f.text.split():
            pattern = f"%{word}%"
            where.append(
                "(filename LIKE ? OR caption LIKE ? OR camera_model LIKE ? OR id IN "
                "(SELECT photo_id FROM photo_tags JOIN tags ON tags.id = tag_id WHERE tags.name LIKE ?))"
            )
            params += [pattern] * 4
        order = SORTS.get(f.sort, SORTS["taken"]).format(dir="DESC" if f.descending else "ASC")
        sql = "SELECT id, path, filename, taken, rating, file_mtime FROM photos"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += f" ORDER BY {order}"
        return [PhotoRow(*row) for row in self._db.execute(sql, params)]

    def locations(self, photo_ids: Iterable[int]) -> dict[int, tuple[float, float]]:
        """photo id → (latitude, longitude), for those that have one."""
        wanted = set(photo_ids)
        rows = self._db.execute("SELECT id, gps_lat, gps_lon FROM photos WHERE gps_lat IS NOT NULL AND gps_lon IS NOT NULL")
        return {row[0]: (row[1], row[2]) for row in rows if row[0] in wanted}

    def photo(self, photo_id: int) -> Optional[Photo]:
        row = self._db.execute("SELECT * FROM photos WHERE id = ?", (photo_id,)).fetchone()
        if row is None:
            return None
        names = {f.name for f in fields(Photo)}
        return Photo(**{key: row[key] for key in row.keys() if key in names})

    # --- ratings, captions and tags ------------------------------------

    def _in(self, ids: Iterable[int]) -> tuple[str, list[int]]:
        ids = list(ids)
        return ",".join("?" * len(ids)), ids

    def set_rating(self, photo_ids: Iterable[int], rating: int) -> None:
        marks, ids = self._in(photo_ids)
        if ids:
            self._db.execute(f"UPDATE photos SET rating = ? WHERE id IN ({marks})", [max(0, min(5, rating)), *ids])
            self._db.commit()

    def set_caption(self, photo_ids: Iterable[int], caption: str) -> None:
        marks, ids = self._in(photo_ids)
        if ids:
            self._db.execute(f"UPDATE photos SET caption = ? WHERE id IN ({marks})", [caption.strip(), *ids])
            self._db.commit()

    def tags_for(self, photo_ids: Iterable[int]) -> dict[str, int]:
        """Tag name → how many of these photos have it."""
        marks, ids = self._in(photo_ids)
        if not ids:
            return {}
        rows = self._db.execute(
            f"SELECT tags.name, COUNT(*) FROM photo_tags JOIN tags ON tags.id = tag_id "
            f"WHERE photo_id IN ({marks}) GROUP BY tags.id ORDER BY tags.name COLLATE NOCASE",
            ids,
        )
        return {row[0]: row[1] for row in rows}

    def add_tag(self, photo_ids: Iterable[int], name: str) -> None:
        name = " ".join(name.split())
        marks, ids = self._in(photo_ids)
        if not name or not ids:
            return
        self._db.execute("INSERT OR IGNORE INTO tags (name) VALUES (?)", (name,))
        tag_id = self._db.execute("SELECT id FROM tags WHERE name = ?", (name,)).fetchone()[0]
        self._db.executemany(
            "INSERT OR IGNORE INTO photo_tags (photo_id, tag_id) VALUES (?, ?)", [(i, tag_id) for i in ids]
        )
        self._db.commit()

    def remove_tag(self, photo_ids: Iterable[int], name: str) -> None:
        marks, ids = self._in(photo_ids)
        if not ids:
            return
        self._db.execute(
            f"DELETE FROM photo_tags WHERE photo_id IN ({marks}) AND tag_id = (SELECT id FROM tags WHERE name = ?)",
            [*ids, name],
        )
        self._db.execute("DELETE FROM tags WHERE id NOT IN (SELECT DISTINCT tag_id FROM photo_tags)")
        self._db.commit()

    def rename_tag(self, tag_id: int, new_name: str) -> None:
        """Renaming onto an existing tag's name merges the two."""
        new_name = " ".join(new_name.split())
        if not new_name:
            return
        existing = self._db.execute("SELECT id FROM tags WHERE name = ?", (new_name,)).fetchone()
        if existing and existing[0] != tag_id:
            self._db.execute(
                "INSERT OR IGNORE INTO photo_tags (photo_id, tag_id) SELECT photo_id, ? FROM photo_tags WHERE tag_id = ?",
                (existing[0], tag_id),
            )
            self._db.execute("DELETE FROM tags WHERE id = ?", (tag_id,))
        else:
            self._db.execute("UPDATE tags SET name = ? WHERE id = ?", (new_name, tag_id))
        self._db.commit()

    def delete_tag(self, tag_id: int) -> None:
        self._db.execute("DELETE FROM tags WHERE id = ?", (tag_id,))
        self._db.commit()

    def all_tag_names(self) -> list[str]:
        return [row[0] for row in self._db.execute("SELECT name FROM tags ORDER BY name COLLATE NOCASE")]

    def import_signatures(self) -> set[tuple[int, str]]:
        """(file size, date taken) of every photo — how Import tells a
        photo it has seen before."""
        return {(row[0], row[1]) for row in self._db.execute("SELECT file_size, taken FROM photos")}

    def import_matches(self) -> dict[tuple[int, str], list[str]]:
        """(file size, date taken) → paths of the library's photos with
        them, for Import's byte-for-byte check."""
        matches: dict[tuple[int, str], list[str]] = {}
        for size, taken, path in self._db.execute("SELECT file_size, taken, path FROM photos"):
            matches.setdefault((size, taken), []).append(path)
        return matches

    # --- duplicates -----------------------------------------------------

    def duplicate_candidates(self, photo_ids: Optional[Iterable[int]] = None) -> list["Candidate"]:
        """Every photo (or just these), with what Find Duplicates needs."""
        from simple_photos.core.duplicates import Candidate

        sql = (
            "SELECT id, path, width, height, file_size, rating, date_added, "
            "(SELECT COUNT(*) FROM photo_tags WHERE photo_id = photos.id), file_mtime FROM photos"
        )
        rows = self._db.execute(sql).fetchall()
        if photo_ids is not None:
            wanted = set(photo_ids)
            rows = [row for row in rows if row[0] in wanted]
        return [
            Candidate(id=r[0], path=r[1], width=r[2], height=r[3], file_size=r[4], rating=r[5], date_added=r[6], tag_count=r[7],
                      file_mtime=r[8])
            for r in rows
        ]

    def fingerprints(self) -> dict[int, tuple[Optional[str], Optional[int]]]:
        """photo id → (digest, dhashes), only where still valid for the file."""
        rows = self._db.execute(
            "SELECT f.photo_id, f.digest, f.dhashes FROM fingerprints f JOIN photos p ON p.id = f.photo_id "
            "WHERE f.file_size = p.file_size AND f.file_mtime = p.file_mtime"
        )
        return {row[0]: (row[1], row[2]) for row in rows}

    def save_fingerprints(self, entries: Iterable[tuple[int, Optional[str], Optional[str]]]) -> None:
        """(photo id, digest, dhashes) — either may be None to keep what's known."""
        for photo_id, digest, hash_value in entries:
            self._db.execute(
                "INSERT INTO fingerprints (photo_id, file_size, file_mtime, digest, dhashes) "
                "SELECT id, file_size, file_mtime, ?, ? FROM photos WHERE id = ? "
                "ON CONFLICT(photo_id) DO UPDATE SET "
                "digest = COALESCE(excluded.digest, CASE WHEN fingerprints.file_size = excluded.file_size "
                "  AND fingerprints.file_mtime = excluded.file_mtime THEN fingerprints.digest END), "
                "dhashes = COALESCE(excluded.dhashes, CASE WHEN fingerprints.file_size = excluded.file_size "
                "  AND fingerprints.file_mtime = excluded.file_mtime THEN fingerprints.dhashes END), "
                "file_size = excluded.file_size, file_mtime = excluded.file_mtime",
                (digest, hash_value, photo_id),
            )
        self._db.commit()

    def merge_into(self, keep_id: int, other_ids: Iterable[int]) -> None:
        """Before removing duplicates: the copy kept takes the best rating,
        every tag, and a caption if it has none."""
        marks, ids = self._in(other_ids)
        if not ids:
            return
        self._db.execute(
            f"UPDATE photos SET rating = MAX(rating, (SELECT MAX(rating) FROM photos WHERE id IN ({marks}))) WHERE id = ?",
            [*ids, keep_id],
        )
        self._db.execute(
            f"INSERT OR IGNORE INTO photo_tags (photo_id, tag_id) SELECT ?, tag_id FROM photo_tags WHERE photo_id IN ({marks})",
            [keep_id, *ids],
        )
        self._db.execute(
            f"UPDATE photos SET caption = (SELECT caption FROM photos WHERE id IN ({marks}) AND caption != '' LIMIT 1) "
            f"WHERE id = ? AND caption = '' AND EXISTS (SELECT 1 FROM photos WHERE id IN ({marks}) AND caption != '')",
            [*ids, keep_id, *ids],
        )
        self._db.commit()

    # --- files ----------------------------------------------------------

    def add_folder(self, path: str) -> None:
        """Note a folder that's just been made, before the next scan."""
        self._db.execute("INSERT OR IGNORE INTO folders (path) VALUES (?)", (_norm(path),))
        self._db.commit()

    def rename_folder(self, old: str, new: str) -> list[tuple[str, str, float]]:
        """A folder was renamed on disk: re-point everything in and below
        it. Returns (old path, new path, mtime) for each photo moved, so
        their cached thumbnails can follow."""
        old, new = _norm(old), _norm(new)
        params = (old, _like_prefix(old))
        where = "(folder = ? OR folder LIKE ? ESCAPE '\\')"
        changed = []
        for photo_id, path, folder, mtime in self._db.execute(
            f"SELECT id, path, folder, file_mtime FROM photos WHERE {where}", params
        ).fetchall():
            new_path = new + path[len(old):]
            self._db.execute(
                "UPDATE photos SET path = ?, folder = ? WHERE id = ?", (new_path, new + folder[len(old):], photo_id)
            )
            changed.append((path, new_path, mtime))
        folders = self._db.execute(f"SELECT path FROM folders WHERE {where.replace('folder', 'path')}", params).fetchall()
        for (folder,) in folders:
            self._db.execute("DELETE FROM folders WHERE path = ?", (folder,))
            self._db.execute("INSERT OR IGNORE INTO folders (path) VALUES (?)", (new + folder[len(old):],))
        self._db.commit()
        return changed

    def refresh_files(self, paths: Iterable[str]) -> None:
        """Re-read these photos now (e.g. after rotating them), keeping
        their ratings and tags."""
        result = ScanResult()
        for path in paths:
            try:
                stat = os.stat(path)
            except OSError:
                continue
            self._store(_norm(path), stat, exists=True, result=result)
        self._db.commit()

    def forget_folder(self, path: str) -> None:
        """A folder (and everything below it) has gone from disk."""
        path = _norm(path)
        params = (path, _like_prefix(path))
        self._db.execute("DELETE FROM folders WHERE path = ? OR path LIKE ? ESCAPE '\\'", params)
        self._db.execute("DELETE FROM photos WHERE folder = ? OR folder LIKE ? ESCAPE '\\'", params)
        self._db.execute("DELETE FROM tags WHERE id NOT IN (SELECT DISTINCT tag_id FROM photo_tags)")
        self._db.commit()

    def relocate(self, moves: Iterable[tuple[str, str]]) -> None:
        """Photos moved on disk: (old path, new path) pairs. They keep
        their ratings, tags and captions."""
        for old, new in moves:
            new = _norm(new)
            self._db.execute(
                "UPDATE photos SET path = ?, folder = ?, filename = ? WHERE path = ?",
                (new, os.path.dirname(new), os.path.basename(new), _norm(old)),
            )
            self._db.execute("INSERT OR IGNORE INTO folders (path) VALUES (?)", (os.path.dirname(new),))
        self._db.commit()

    def forget(self, photo_ids: Iterable[int]) -> None:
        """Drop photos from the library (their files were moved to the trash)."""
        marks, ids = self._in(photo_ids)
        if ids:
            self._db.execute(f"DELETE FROM photos WHERE id IN ({marks})", ids)
            self._db.execute("DELETE FROM tags WHERE id NOT IN (SELECT DISTINCT tag_id FROM photo_tags)")
            self._db.commit()
