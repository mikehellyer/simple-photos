"""Finding duplicate photos.

Two kinds:

- Exact copies: the very same file twice. Only photos that share a file
  size with another can be copies, so only those are read and hashed
  (BLAKE2) — a big collection with few duplicates costs little.
- Look-alikes: the same picture saved differently (resized, re-saved,
  sent through a messaging app). Each photo gets a 64-bit "difference
  hash" of its thumbnail: shrink to 9×8 grey pixels and record whether
  each pixel is brighter than the one to its right. Copies of one
  picture come out the same or a few bits apart; different pictures
  around half their bits apart. Near-blank photos (all black, all sky)
  hash alike whatever they show, so they're never matched this way.

Each photo is also hashed turned a quarter, half and three-quarter
turn, so a rotated copy is still found. Matching every look-alike
against every other is done with numpy, a row at a time, which handles
tens of thousands of photos in seconds.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Iterable, Optional

from PIL import Image

# How many of the 64 hash bits may differ, for each strictness.
MODES = {
    "exact": None,  # identical files only
    "same": 4,  # the same photo, resized or re-saved
    "similar": 10,  # also near-identical shots (bursts, a slight crop)
}
DEFAULT_MODE = "same"

_FLAT_BITS = 6  # a hash with fewer (or more than 64 minus this) bits set is a near-blank photo


def dhash(image: Image.Image) -> int:
    """The 64-bit difference hash of an image (any size or mode)."""
    small = image.convert("L").resize((9, 8), Image.LANCZOS)
    pixels = small.tobytes()
    value = 0
    for row in range(8):
        for col in range(8):
            left, right = pixels[row * 9 + col], pixels[row * 9 + col + 1]
            value = (value << 1) | (left > right)
    return value


def dhash_turns(image: Image.Image) -> tuple[int, int, int, int]:
    """The hash of the image as it is and turned a quarter, half and
    three-quarter turn — so a rotated copy still matches."""
    turns = (None, Image.Transpose.ROTATE_270, Image.Transpose.ROTATE_180, Image.Transpose.ROTATE_90)
    return tuple(dhash(image if turn is None else image.transpose(turn)) for turn in turns)


def encode_hashes(hashes: tuple[int, ...]) -> str:
    return ",".join(f"{h:016x}" for h in hashes)


def decode_hashes(text: str) -> tuple[int, ...]:
    return tuple(int(part, 16) for part in text.split(","))


def is_flat(hash_value: int) -> bool:
    bits = bin(hash_value).count("1")
    return bits < _FLAT_BITS or bits > 64 - _FLAT_BITS


def file_digest(path: str) -> str:
    digest = hashlib.blake2b(digest_size=20)
    with open(path, "rb") as file:
        for chunk in iter(lambda: file.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


class _Groups:
    """Union-find: photos joined by any match end up in one group."""

    def __init__(self):
        self._parent: dict[int, int] = {}

    def find(self, item: int) -> int:
        self._parent.setdefault(item, item)
        while self._parent[item] != item:
            self._parent[item] = self._parent[self._parent[item]]
            item = self._parent[item]
        return item

    def join(self, a: int, b: int) -> None:
        self._parent[self.find(a)] = self.find(b)

    def groups(self) -> list[list[int]]:
        by_root: dict[int, list[int]] = {}
        for item in self._parent:
            by_root.setdefault(self.find(item), []).append(item)
        return [sorted(members) for members in by_root.values() if len(members) > 1]


def same_size_groups(sizes: dict[int, int]) -> list[list[int]]:
    """Photo ids sharing a file size — the only possible exact copies."""
    by_size: dict[int, list[int]] = {}
    for photo_id, size in sizes.items():
        by_size.setdefault(size, []).append(photo_id)
    return [ids for size, ids in by_size.items() if len(ids) > 1 and size > 0]


def group_duplicates(
    digests: dict[int, str],
    hashes: dict[int, int | tuple[int, ...]],
    max_distance: Optional[int],
    never_pair: frozenset[frozenset[int]] = frozenset(),
) -> list[list[int]]:
    """Groups of photo ids that are duplicates: the same digest, or (if
    max_distance isn't None) hashes at most that many bits apart. A hash
    may be a tuple from dhash_turns(): then a photo also matches a copy
    of itself that's been turned. Pairs in never_pair aren't matched by
    look (a camera's RAW + JPEG of one shot)."""
    groups = _Groups()
    by_digest: dict[str, int] = {}
    for photo_id, digest in digests.items():
        if digest in by_digest:
            groups.join(photo_id, by_digest[digest])
        else:
            by_digest[digest] = photo_id

    if max_distance is not None:
        import numpy as np

        variants = {pid: (h if isinstance(h, tuple) else (h,)) for pid, h in hashes.items()}
        usable = [(pid, v) for pid, v in variants.items() if not is_flat(v[0])]
        if len(usable) > 1:
            ids = np.array([pid for pid, _ in usable], dtype=np.int64)
            upright = np.array([v[0] for _, v in usable], dtype=np.uint64)
            for i in range(len(upright) - 1):
                rest = upright[i + 1 :]
                distances = np.min(
                    [np.bitwise_count(rest ^ np.uint64(turned)) for turned in usable[i][1]], axis=0
                )
                for j in np.nonzero(distances <= max_distance)[0]:
                    a, b = int(ids[i]), int(ids[i + 1 + j])
                    if frozenset((a, b)) not in never_pair:
                        groups.join(a, b)
    return sorted(groups.groups(), key=lambda g: g[0])


@dataclass
class Candidate:
    """What's needed to pick which copy to keep."""

    id: int
    path: str
    width: int
    height: int
    file_size: int
    rating: int
    tag_count: int
    date_added: float
    file_mtime: float = 0.0


def best_copy(candidates: Iterable[Candidate]) -> int:
    """The id to keep: the most pixels, then the biggest file (least
    compressed), then the one you've rated or tagged, then the one that
    was in the library first."""
    return min(
        candidates,
        key=lambda c: (-(c.width * c.height), -c.file_size, -c.rating, -c.tag_count, c.date_added, c.path),
    ).id


def raw_jpeg_pairs(candidates: Iterable[Candidate]) -> frozenset[frozenset[int]]:
    """A camera set to RAW + JPEG saves IMG_0001.CR3 and IMG_0001.JPG for
    one shot. They look alike but both are wanted: these pairs."""
    import os

    from simple_photos.core.raw import is_raw

    by_shot: dict[tuple[str, str], list[Candidate]] = {}
    for c in candidates:
        folder, name = os.path.split(c.path)
        by_shot.setdefault((os.path.normcase(folder), os.path.splitext(name)[0].lower()), []).append(c)
    pairs = set()
    for shot in by_shot.values():
        raws = [c.id for c in shot if is_raw(c.path)]
        others = [c.id for c in shot if not is_raw(c.path)]
        pairs.update(frozenset((r, o)) for r in raws for o in others)
    return frozenset(pairs)


@dataclass
class SearchResult:
    groups: list[list[Candidate]]  # best copy first in each
    thumbnails: dict[int, str]  # photo id → cached thumbnail path
    checked: int
    stopped: bool = False


def search(
    library,
    candidates: list[Candidate],
    mode: str,
    progress=None,
    should_stop=None,
    cache_dir=None,
    workers: int = 4,
) -> SearchResult:
    """Find duplicates among candidates. library is a Library used only
    from this thread; fingerprints made here are saved to it, so the next
    search only has to look at new or changed photos.

    progress(done, total, stage) is called as work is done."""
    from concurrent.futures import ThreadPoolExecutor

    from simple_photos.core.thumbnails import ensure_thumbnail

    max_distance = MODES.get(mode, MODES[DEFAULT_MODE])
    by_id = {c.id: c for c in candidates}
    known = library.fingerprints()
    stopped = False

    def stop() -> bool:
        return bool(should_stop and should_stop())

    # Which photos still need which fingerprint.
    need_digest = [pid for group in same_size_groups({c.id: c.file_size for c in candidates}) for pid in group]
    need_digest = [pid for pid in need_digest if not (known.get(pid) or (None, None))[0]]
    need_hash = []
    if max_distance is not None:
        need_hash = [c.id for c in candidates if (known.get(c.id) or (None, None))[1] is None]

    def make_digest(pid):
        try:
            return pid, file_digest(by_id[pid].path), None
        except OSError:
            return pid, None, None

    def make_hash(pid):
        c = by_id[pid]
        thumb = ensure_thumbnail(c.path, c.file_mtime, cache_dir)
        if thumb is None:
            return pid, None, None
        try:
            with Image.open(thumb) as image:
                return pid, None, encode_hashes(dhash_turns(image))
        except OSError:
            return pid, None, None

    jobs = [(make_digest, pid, "Comparing files") for pid in need_digest] + [(make_hash, pid, "Fingerprinting photos") for pid in need_hash]
    total = len(jobs)
    pending: list[tuple[int, Optional[str], Optional[int]]] = []
    with ThreadPoolExecutor(max(1, workers)) as pool:
        futures = [pool.submit(fn, pid) for fn, pid, _ in jobs]
        for done, (future, (_, _, stage)) in enumerate(zip(futures, jobs), start=1):
            if stop():
                stopped = True
                pool.shutdown(wait=True, cancel_futures=True)
                break
            pid, digest, hash_value = future.result()
            if digest is not None or hash_value is not None:
                pending.append((pid, digest, hash_value))
                old_digest, old_hash = known.get(pid) or (None, None)
                known[pid] = (digest or old_digest, hash_value if hash_value is not None else old_hash)
            if len(pending) >= 200:
                library.save_fingerprints(pending)
                pending = []
            if progress:
                progress(done, total, stage)
    library.save_fingerprints(pending)

    digests = {pid: d for pid, (d, _) in known.items() if pid in by_id and d}
    hashes = {pid: decode_hashes(h) for pid, (_, h) in known.items() if pid in by_id and h}
    if progress:
        progress(total, total, "Matching")
    pairs = raw_jpeg_pairs(candidates)
    id_groups = group_duplicates(digests, hashes, max_distance, pairs) if not stopped else []

    groups, thumbnails = [], {}
    for ids in id_groups:
        members = [by_id[pid] for pid in ids]
        keep = best_copy(members)
        members.sort(key=lambda c: (c.id != keep, c.path.lower()))
        groups.append(members)
        for c in members:
            thumb = ensure_thumbnail(c.path, c.file_mtime, cache_dir)
            if thumb:
                thumbnails[c.id] = str(thumb)
    groups.sort(key=lambda g: g[0].path.lower())
    return SearchResult(groups=groups, thumbnails=thumbnails, checked=len(candidates), stopped=stopped)

