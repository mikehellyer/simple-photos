import os

import pytest

from conftest import make_photo
from simple_photos.core.library import Library, PhotoFilter


@pytest.fixture
def collection(tmp_path):
    root = tmp_path / "Pictures"
    make_photo(root / "2023" / "beach.jpg", taken="2023:08:01 10:00:00")
    make_photo(root / "2023" / "sunset.jpg", taken="2023:08:02 20:00:00")
    make_photo(root / "2024" / "party" / "cake.jpg", taken="2024:01:05 19:00:00")
    make_photo(root / ".hidden" / "secret.jpg")
    (root / "notes.txt").write_text("not a photo")
    return root


@pytest.fixture
def library(tmp_path):
    lib = Library(tmp_path / "lib.db")
    yield lib
    lib.close()


def names(rows):
    return [row.filename for row in rows]


def test_scan_finds_photos_and_skips_hidden_folders(library, collection):
    result = library.scan([collection])
    assert result.added == 3
    assert names(library.query(PhotoFilter())) == ["beach.jpg", "sunset.jpg", "cake.jpg"]


def test_rescan_only_reads_changed_files_and_keeps_ratings(library, collection):
    library.scan([collection])
    beach = library.query(PhotoFilter(text="beach"))[0]
    library.set_rating([beach.id], 4)
    library.add_tag([beach.id], "Holiday")

    assert library.scan([collection]).added == 0

    make_photo(collection / "2023" / "beach.jpg", taken="2023:08:01 11:00:00")
    stat = os.stat(collection / "2023" / "beach.jpg")
    os.utime(collection / "2023" / "beach.jpg", (stat.st_atime, stat.st_mtime + 10))
    result = library.scan([collection])
    assert (result.added, result.updated) == (0, 1)
    photo = library.photo(beach.id)
    assert photo.taken == "2023-08-01 11:00:00"
    assert photo.rating == 4
    assert library.tags_for([beach.id]) == {"Holiday": 1}


def test_deleted_files_are_dropped(library, collection):
    library.scan([collection])
    (collection / "2023" / "sunset.jpg").unlink()
    assert library.scan([collection]).removed == 1
    assert library.count() == 2


def test_missing_root_keeps_its_photos(library, collection, tmp_path):
    library.scan([collection])
    collection.rename(tmp_path / "unplugged")
    assert library.scan([collection]).removed == 0
    assert library.count() == 3


def test_removed_root_drops_its_photos(library, collection, tmp_path):
    library.scan([collection])
    other = tmp_path / "Other"
    make_photo(other / "x.jpg")
    assert library.scan([other]).removed == 3
    assert library.count() == 1


def test_unreadable_file_is_still_listed(library, tmp_path):
    root = tmp_path / "p"
    root.mkdir()
    (root / "broken.jpg").write_bytes(b"not really a jpeg")
    result = library.scan([root])
    assert result.added == 1 and result.failed == 1


def test_folder_filter_with_and_without_subfolders(library, collection):
    library.scan([collection])
    assert names(library.query(PhotoFilter(folder=str(collection / "2024")))) == ["cake.jpg"]
    assert names(library.query(PhotoFilter(folder=str(collection / "2024"), include_subfolders=False))) == []
    assert len(library.query(PhotoFilter(folder=str(collection)))) == 3


def test_folder_filter_does_not_match_similarly_named_folders(library, tmp_path):
    make_photo(tmp_path / "p" / "a_b" / "1.jpg")
    make_photo(tmp_path / "p" / "axb" / "2.jpg")
    make_photo(tmp_path / "p" / "a_b2" / "3.jpg")
    library.scan([tmp_path / "p"])
    assert names(library.query(PhotoFilter(folder=str(tmp_path / "p" / "a_b")))) == ["1.jpg"]


def test_dates_and_date_filter(library, collection):
    library.scan([collection])
    assert library.date_counts() == [(2023, 8, 2), (2024, 1, 1)]
    assert names(library.query(PhotoFilter(year=2023, month=8))) == ["beach.jpg", "sunset.jpg"]
    assert names(library.query(PhotoFilter(year=2024))) == ["cake.jpg"]


def test_tags(library, collection):
    library.scan([collection])
    ids = {row.filename: row.id for row in library.query(PhotoFilter())}
    library.add_tag([ids["beach.jpg"], ids["sunset.jpg"]], "  Summer   2023 ")
    library.add_tag([ids["sunset.jpg"]], "sky")
    tags = {name: (tag_id, count) for tag_id, name, count in library.tag_counts()}
    assert {name: count for name, (_, count) in tags.items()} == {"sky": 1, "Summer 2023": 2}
    assert names(library.query(PhotoFilter(tag_id=tags["sky"][0]))) == ["sunset.jpg"]
    assert names(library.query(PhotoFilter(untagged=True))) == ["cake.jpg"]
    assert library.tags_for(ids.values()) == {"sky": 1, "Summer 2023": 2}

    library.add_tag([ids["cake.jpg"]], "SKY")  # same tag, case aside
    assert library.tags_for([ids["cake.jpg"]]) == {"sky": 1}

    library.remove_tag([ids["sunset.jpg"], ids["cake.jpg"]], "sky")
    assert "sky" not in library.all_tag_names()


def test_renaming_a_tag_onto_another_merges_them(library, collection):
    library.scan([collection])
    ids = [row.id for row in library.query(PhotoFilter())]
    library.add_tag(ids[:1], "Family")
    library.add_tag(ids[1:], "family pics")
    family_pics = next(t for t, name, _ in library.tag_counts() if name == "family pics")
    library.rename_tag(family_pics, "Family")
    assert library.tag_counts()[0][1:] == ("Family", 3)


def test_rating_filter_search_and_sort(library, collection):
    library.scan([collection])
    ids = {row.filename: row.id for row in library.query(PhotoFilter())}
    library.set_rating([ids["cake.jpg"]], 5)
    library.set_rating([ids["sunset.jpg"]], 3)
    library.set_caption([ids["beach.jpg"]], "Waves at Bondi")
    assert names(library.query(PhotoFilter(min_rating=3))) == ["sunset.jpg", "cake.jpg"]
    assert names(library.query(PhotoFilter(text="bondi"))) == ["beach.jpg"]
    assert names(library.query(PhotoFilter(text="EOS"))) == ["beach.jpg", "sunset.jpg", "cake.jpg"]
    assert names(library.query(PhotoFilter(sort="rating", descending=True))) == ["cake.jpg", "sunset.jpg", "beach.jpg"]
    assert names(library.query(PhotoFilter(sort="name"))) == ["beach.jpg", "cake.jpg", "sunset.jpg"]


def test_forget(library, collection):
    library.scan([collection])
    first = library.query(PhotoFilter())[0]
    library.forget([first.id])
    assert library.photo(first.id) is None
    assert library.count() == 2


def test_empty_folders_are_listed(library, collection):
    (collection / "Empty" / "Deeper").mkdir(parents=True)
    library.scan([collection])
    counts = library.folder_counts()
    assert counts[str(collection / "Empty")] == 0
    assert counts[str(collection / "Empty" / "Deeper")] == 0
    assert str(collection / ".hidden") not in counts


def test_added_folder_shows_before_a_scan_and_goes_when_deleted(library, collection):
    library.scan([collection])
    new = collection / "Made Just Now"
    new.mkdir()
    library.add_folder(str(new))
    assert library.folder_counts()[str(new)] == 0
    new.rmdir()
    library.scan([collection])
    assert str(new) not in library.folder_counts()


def test_relocated_photos_keep_their_tags(library, collection):
    library.scan([collection])
    beach = library.query(PhotoFilter(text="beach"))[0]
    library.set_rating([beach.id], 5)
    library.add_tag([beach.id], "Sea")
    new_path = collection / "2024" / "beach.jpg"
    os.replace(beach.path, new_path)
    library.relocate([(beach.path, str(new_path))])
    moved = library.photo(beach.id)
    assert (moved.path, moved.folder, moved.filename) == (str(new_path), str(collection / "2024"), "beach.jpg")
    assert library.scan([collection]).added == 0  # the scan agrees nothing changed
    assert library.photo(beach.id).rating == 5
    assert library.tags_for([beach.id]) == {"Sea": 1}


def test_stopped_scan_removes_nothing(library, collection):
    library.scan([collection])
    (collection / "2023" / "sunset.jpg").unlink()
    make_photo(collection / "2025" / "new.jpg")
    result = library.scan([collection], should_stop=lambda: True)
    assert (result.added, result.removed) == (0, 0)
    assert library.count() == 3
