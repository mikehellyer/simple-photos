import shutil
from pathlib import Path

import pytest
from PIL import Image

from simple_photos.core import duplicates
from simple_photos.core.duplicates import Candidate, best_copy, dhash, group_duplicates, is_flat, search
from simple_photos.core.library import Library

DATA = Path(__file__).parent / "data"


def distance(a, b):
    return bin(a ^ b).count("1")


def test_resized_and_resaved_copies_hash_alike():
    original = Image.open(DATA / "astronaut.jpg")
    smaller = original.resize((original.width // 3, original.height // 3))
    assert distance(dhash(original), dhash(smaller)) <= duplicates.MODES["same"]
    assert distance(dhash(original), dhash(original.convert("L"))) <= duplicates.MODES["same"]


def test_different_photos_hash_far_apart():
    hashes = [dhash(Image.open(DATA / name)) for name in ("astronaut.jpg", "cat.jpg", "coffee.jpg")]
    for i in range(3):
        for j in range(i + 1, 3):
            assert distance(hashes[i], hashes[j]) > duplicates.MODES["similar"]


def test_rotated_copies_match():
    from simple_photos.core.duplicates import dhash_turns

    original = Image.open(DATA / "astronaut.jpg")
    turned = original.rotate(90, expand=True)
    hashes = {1: dhash_turns(original), 2: dhash_turns(turned), 3: dhash_turns(Image.open(DATA / "cat.jpg"))}
    assert group_duplicates({}, hashes, max_distance=duplicates.MODES["same"]) == [[1, 2]]


def test_blank_photos_are_never_matched_by_look():
    black = dhash(Image.new("RGB", (100, 80)))
    assert is_flat(black)
    assert group_duplicates({}, {1: black, 2: black}, max_distance=10) == []
    assert group_duplicates({1: "x", 2: "x"}, {1: black, 2: black}, max_distance=10) == [[1, 2]]  # unless identical files


def test_grouping_joins_chains_and_respects_strictness():
    a = 0x0F0F0F0F0F0F0F0F
    b = a ^ 0b111  # 3 bits from a
    c = b ^ (0b111 << 20)  # 3 bits from b, 6 from a
    far = ~a & (2**64 - 1)
    hashes = {1: a, 2: b, 3: c, 4: far}
    assert group_duplicates({}, hashes, max_distance=3) == [[1, 2, 3]]
    assert group_duplicates({}, hashes, max_distance=2) == []
    assert group_duplicates({5: "d", 6: "d", 7: "e"}, {}, max_distance=None) == [[5, 6]]


def test_best_copy_prefers_resolution_then_size_then_your_work():
    def cand(i, w=100, h=100, size=1000, rating=0, tags=0, added=0.0):
        return Candidate(i, f"/p/{i}.jpg", w, h, size, rating, tags, added)

    assert best_copy([cand(1), cand(2, w=200)]) == 2
    assert best_copy([cand(1, size=900), cand(2)]) == 2
    assert best_copy([cand(1), cand(2, rating=3)]) == 2
    assert best_copy([cand(1, tags=1), cand(2)]) == 1
    assert best_copy([cand(1, added=5.0), cand(2, added=1.0)]) == 2


@pytest.fixture
def collection(tmp_path):
    root = tmp_path / "Pictures"
    (root / "Originals").mkdir(parents=True)
    (root / "Backup").mkdir()
    (root / "WhatsApp").mkdir()
    shutil.copy(DATA / "astronaut.jpg", root / "Originals" / "astronaut.jpg")
    shutil.copy(DATA / "astronaut.jpg", root / "Backup" / "astronaut copy.jpg")  # exact copy
    small = Image.open(DATA / "astronaut.jpg")
    small.resize((small.width // 2, small.height // 2)).save(root / "WhatsApp" / "IMG-0001.jpg", quality=60)
    shutil.copy(DATA / "cat.jpg", root / "Originals" / "cat.jpg")
    shutil.copy(DATA / "coffee.jpg", root / "Originals" / "coffee.jpg")
    Image.open(DATA / "astronaut.jpg").rotate(-90, expand=True).save(root / "Backup" / "turned.jpg", quality=92)
    return root


def names(result):
    return [[Path(c.path).name for c in group] for group in result.groups]


def test_search_exact_only(tmp_path, collection):
    library = Library(tmp_path / "lib.db")
    library.scan([collection])
    result = search(library, library.duplicate_candidates(), "exact", cache_dir=tmp_path / "cache")
    assert sorted(map(sorted, names(result))) == [["astronaut copy.jpg", "astronaut.jpg"]]


def test_search_same_photo_finds_the_resized_copy_and_keeps_the_best(tmp_path, collection):
    library = Library(tmp_path / "lib.db")
    library.scan([collection])
    result = search(library, library.duplicate_candidates(), "same", cache_dir=tmp_path / "cache")
    assert len(result.groups) == 1
    group = result.groups[0]
    assert sorted(Path(c.path).name for c in group) == ["IMG-0001.jpg", "astronaut copy.jpg", "astronaut.jpg", "turned.jpg"]
    assert Path(group[0].path).name != "IMG-0001.jpg"  # the full-size one is kept, not the WhatsApp copy
    assert set(result.thumbnails) == {c.id for c in group}


def test_fingerprints_are_reused(tmp_path, collection, monkeypatch):
    library = Library(tmp_path / "lib.db")
    library.scan([collection])
    search(library, library.duplicate_candidates(), "same", cache_dir=tmp_path / "cache")
    calls = []
    monkeypatch.setattr(duplicates, "file_digest", lambda path: calls.append(path) or "x")
    monkeypatch.setattr(duplicates, "dhash_turns", lambda image: calls.append(image) or (0, 0, 0, 0))
    again = search(library, library.duplicate_candidates(), "same", cache_dir=tmp_path / "cache")
    assert calls == [] and len(again.groups) == 1


def test_a_changed_file_is_fingerprinted_again(tmp_path, collection):
    library = Library(tmp_path / "lib.db")
    library.scan([collection])
    search(library, library.duplicate_candidates(), "exact", cache_dir=tmp_path / "cache")
    copy = next(c for c in library.duplicate_candidates() if c.path.endswith("astronaut copy.jpg"))
    assert library.fingerprints()[copy.id][0]
    library.refresh_files([copy.path])  # e.g. after rotating it
    assert copy.id not in library.fingerprints()


def test_stopping_early_reports_no_groups(tmp_path, collection):
    library = Library(tmp_path / "lib.db")
    library.scan([collection])
    result = search(library, library.duplicate_candidates(), "same", should_stop=lambda: True, cache_dir=tmp_path / "cache")
    assert result.stopped and result.groups == []


def test_merge_into_keeps_your_ratings_tags_and_caption(tmp_path, collection):
    library = Library(tmp_path / "lib.db")
    library.scan([collection])
    ids = {Path(c.path).name: c.id for c in library.duplicate_candidates()}
    keep, other = ids["astronaut.jpg"], ids["IMG-0001.jpg"]
    library.set_rating([other], 4)
    library.add_tag([other], "Space")
    library.set_caption([other], "Eileen Collins")
    library.add_tag([keep], "NASA")
    library.merge_into(keep, [other])
    photo = library.photo(keep)
    assert photo.rating == 4 and photo.caption == "Eileen Collins"
    assert library.tags_for([keep]) == {"NASA": 1, "Space": 1}
