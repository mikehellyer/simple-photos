import datetime
import os
import stat

import pytest

from conftest import make_photo
from simple_photos.core.importer import (
    default_folder_name,
    find_photos,
    import_photos,
    safe_folder_name,
)
from simple_photos.core.library import Library


@pytest.fixture
def card(tmp_path):
    """An SD card the way cameras lay one out."""
    root = tmp_path / "EOS_DIGITAL"
    make_photo(root / "DCIM" / "100CANON" / "IMG_0001.JPG", taken="2026:10:20 09:00:00")
    make_photo(root / "DCIM" / "100CANON" / "IMG_0002.JPG", taken="2026:10:21 10:00:00", color=(1, 2, 3))
    make_photo(root / "DCIM" / "101CANON" / "IMG_0001.JPG", taken="2026:10:22 11:00:00", color=(9, 9, 9))  # same name, another folder
    (root / "DCIM" / "100CANON" / "MVI_0003.MP4").write_bytes(b"video")
    make_photo(root / "$RECYCLE.BIN" / "deleted.jpg")
    make_photo(root / ".Trashes" / "old.jpg")
    (root / "MISC").mkdir()
    return root


def test_finds_the_photos_and_skips_system_folders(card):
    scan = find_photos(str(card))
    assert sorted(os.path.relpath(p.path, card) for p in scan.photos) == [
        os.path.join("DCIM", "100CANON", "IMG_0001.JPG"),
        os.path.join("DCIM", "100CANON", "IMG_0002.JPG"),
        os.path.join("DCIM", "101CANON", "IMG_0001.JPG"),
    ]
    assert scan.date_range() == ("2026-10-20", "2026-10-22")
    assert scan.total_size == sum(p.size for p in scan.photos)


def test_photos_already_in_the_library_are_marked(card, tmp_path):
    library = Library(tmp_path / "lib.db")
    pictures = tmp_path / "Pictures"
    first = find_photos(str(card))
    import_photos(first.photos[:1], str(pictures), "earlier import")
    library.scan([pictures])
    again = find_photos(str(card), known=library.import_signatures())
    assert [p.already_imported for p in again.photos] == [True, False, False]
    assert len(again.new_photos) == 2


def test_import_copies_into_one_dated_folder_without_overwriting(card, tmp_path):
    pictures = tmp_path / "Pictures"
    pictures.mkdir()
    scan = find_photos(str(card))
    source = scan.photos[0].path
    os.chmod(source, stat.S_IREAD)  # cameras mark protected shots read-only
    original_mtime = os.stat(source).st_mtime_ns
    result = import_photos(scan.photos, str(pictures), "2026-10-27 EOS_DIGITAL")
    assert result.folder == str(pictures / "2026-10-27 EOS_DIGITAL")
    assert sorted(os.listdir(result.folder)) == ["IMG_0001 (2).JPG", "IMG_0001.JPG", "IMG_0002.JPG"]
    copied = os.path.join(result.folder, "IMG_0001.JPG")
    assert os.stat(copied).st_mtime_ns == original_mtime  # dates kept
    assert os.access(copied, os.W_OK)  # but editable
    assert os.path.exists(source)  # nothing on the card is touched
    again = import_photos(scan.photos[:1], str(pictures), "2026-10-27 EOS_DIGITAL")
    assert again.folder == str(pictures / "2026-10-27 EOS_DIGITAL (2)")  # a second import that day


def test_stopping_leaves_no_partial_files(card, tmp_path):
    scan = find_photos(str(card))
    calls = iter([False, True])
    result = import_photos(scan.photos, str(tmp_path), "Import", should_stop=lambda: next(calls, True))
    assert result.stopped and len(result.copied) == 1
    assert not [f for f in os.listdir(result.folder) if f.endswith(".part")]


def test_a_missing_source_is_reported_and_the_rest_imported(card, tmp_path):
    scan = find_photos(str(card))
    os.remove(scan.photos[1].path)  # the card was pulled out mid-way, say
    result = import_photos(scan.photos, str(tmp_path), "Import")
    assert len(result.copied) == 2 and len(result.failed) == 1


def test_folder_names():
    assert default_folder_name("KINGSTON", datetime.date(2026, 10, 27)) == "2026-10-27 KINGSTON"
    assert default_folder_name("My: Card", datetime.date(2026, 10, 27)) == "2026-10-27 My- Card"
    assert safe_folder_name('a/b:c*d?') == "a-b-c-d-"
    assert safe_folder_name("  ...  ") == "Import"
    with pytest.raises(ValueError):
        import_photos([], "/tmp", "bad/name")


def test_byte_for_byte_check_only_skips_true_copies(card, tmp_path):
    from simple_photos.core.importer import not_really_imported

    pictures = tmp_path / "Pictures"
    scan = find_photos(str(card))
    import_photos(scan.photos[:2], str(pictures), "earlier")
    library = Library(tmp_path / "lib.db")
    library.scan([pictures])
    # Make the second library copy differ but keep its size and date:
    # same size + date taken, yet a different photo.
    imported = sorted((pictures / "earlier").iterdir())
    data = bytearray(imported[1].read_bytes())
    data[-3] ^= 0xFF
    stat = os.stat(imported[1])
    imported[1].write_bytes(bytes(data))
    os.utime(imported[1], ns=(stat.st_atime_ns, stat.st_mtime_ns))
    library.scan([pictures])

    again = find_photos(str(card), known=library.import_signatures())
    looks_imported = [p for p in again.photos if p.already_imported]
    assert len(looks_imported) == 2
    different = not_really_imported(looks_imported, library.import_matches())
    assert [os.path.basename(p.path) for p in different] == ["IMG_0002.JPG"]


def test_settings_remember_import_choices(tmp_path):
    from simple_photos.core.settings import SettingsStore

    store = SettingsStore(tmp_path / "settings.json")
    assert store.settings.import_folder == "" and store.settings.import_verify is False
    store.update(import_folder="/p/Imports", import_verify=True)
    again = SettingsStore(tmp_path / "settings.json").settings
    assert again.import_folder == "/p/Imports" and again.import_verify is True


def test_cutoff_dates():
    from simple_photos.core.importer import cutoff_date

    today = datetime.date(2026, 10, 27)
    assert cutoff_date(1, "all", today) is None
    assert cutoff_date(7, "days", today) == datetime.date(2026, 10, 20)
    assert cutoff_date(2, "weeks", today) == datetime.date(2026, 10, 13)
    assert cutoff_date(1, "months", today) == datetime.date(2026, 9, 27)
    assert cutoff_date(12, "months", today) == datetime.date(2025, 10, 27)
    assert cutoff_date(1, "months", datetime.date(2026, 3, 31)) == datetime.date(2026, 2, 28)  # no 31 February
    assert cutoff_date(1, "months", datetime.date(2026, 1, 15)) == datetime.date(2025, 12, 15)
    assert cutoff_date(0, "days", today) is None


def test_only_photos_taken_since_the_cutoff(card):
    from simple_photos.core.importer import SourcePhoto, taken_since

    scan = find_photos(str(card))  # taken 20, 21 and 22 October 2026
    assert len(taken_since(scan.photos, None)) == 3
    assert [p.taken[:10] for p in taken_since(scan.photos, datetime.date(2026, 10, 21))] == ["2026-10-21", "2026-10-22"]
    assert taken_since(scan.photos, datetime.date(2026, 10, 23)) == []
    undated = SourcePhoto("/x.jpg", 1, "")
    assert taken_since([undated], datetime.date(2026, 10, 23)) == [undated]


def test_import_period_settings(tmp_path):
    import json

    from simple_photos.core.settings import SettingsStore

    path = tmp_path / "settings.json"
    s = SettingsStore(path).settings
    assert (s.import_period_unit, s.import_period_count) == ("all", 7)
    path.write_text(json.dumps({"import_period_unit": "fortnights", "import_period_count": 0}))
    s = SettingsStore(path).settings
    assert (s.import_period_unit, s.import_period_count) == ("all", 1)
