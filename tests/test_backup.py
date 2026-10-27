import os
import sqlite3
import time

import pytest

from conftest import make_photo
from simple_photos.core import backup
from simple_photos.core.library import Library, PhotoFilter


@pytest.fixture
def setup(tmp_path):
    pictures = tmp_path / "Pictures"
    make_photo(pictures / "2024" / "Rome" / "a.jpg")
    make_photo(pictures / "2024" / "Rome" / "b.jpg", color=(1, 2, 3))
    (pictures / "2024" / "Rome" / "clip.mp4").write_bytes(b"a video is kept too")
    (pictures / ".hidden").mkdir()
    (pictures / ".hidden" / "x.jpg").write_text("skip me")
    (pictures / "half.jpg.part").write_text("skip me")
    usb = tmp_path / "usb"
    usb.mkdir()
    return pictures, usb


def everything(plans):
    return [f for p in plans for f in p.to_copy]


def test_first_backup_copies_everything_but_hidden_files(setup):
    pictures, usb = setup
    plans = backup.plan_backup([str(pictures)], str(usb))
    assert len(plans) == 1 and plans[0].target == str(usb / "Simple-Photos Backup" / "Pictures")
    assert sorted(os.path.relpath(f.source, pictures) for f in everything(plans)) == [
        os.path.join("2024", "Rome", "a.jpg"),
        os.path.join("2024", "Rome", "b.jpg"),
        os.path.join("2024", "Rome", "clip.mp4"),
    ]
    result = backup.run_backup(everything(plans))
    assert (result.copied, result.failed, result.stopped) == (3, [], False)
    copy = usb / "Simple-Photos Backup" / "Pictures" / "2024" / "Rome" / "a.jpg"
    assert copy.read_bytes() == (pictures / "2024" / "Rome" / "a.jpg").read_bytes()
    assert abs(os.stat(copy).st_mtime - os.stat(pictures / "2024" / "Rome" / "a.jpg").st_mtime) < 1


def test_second_backup_copies_only_whats_new_or_changed(setup):
    pictures, usb = setup
    backup.run_backup(everything(backup.plan_backup([str(pictures)], str(usb))))
    make_photo(pictures / "2025" / "new.jpg")
    changed = pictures / "2024" / "Rome" / "b.jpg"
    changed.write_bytes(changed.read_bytes() + b"edited")
    plans = backup.plan_backup([str(pictures)], str(usb))
    assert sorted(os.path.basename(f.source) for f in everything(plans)) == ["b.jpg", "new.jpg"]
    assert plans[0].up_to_date == 2
    backup.run_backup(everything(plans))
    assert everything(backup.plan_backup([str(pictures)], str(usb))) == []


def test_nothing_is_deleted_from_the_backup(setup):
    pictures, usb = setup
    backup.run_backup(everything(backup.plan_backup([str(pictures)], str(usb))))
    os.remove(pictures / "2024" / "Rome" / "a.jpg")
    backup.run_backup(everything(backup.plan_backup([str(pictures)], str(usb))))
    assert (usb / "Simple-Photos Backup" / "Pictures" / "2024" / "Rome" / "a.jpg").exists()


def test_usb_drive_time_quirks_count_as_the_same_file(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.write_text("same")
    b.write_text("same")
    now = time.time()
    for gap, same in ((0, True), (1.9, True), (3600, True), (3601.5, True), (5, False), (7200, False)):
        os.utime(a, (now, now))
        os.utime(b, (now + gap, now + gap))
        assert backup.same_file(os.stat(a), os.stat(b)) is same, gap
    b.write_text("different size")
    assert not backup.same_file(os.stat(a), os.stat(b))


def test_two_collection_folders_with_the_same_name(tmp_path):
    names = backup.folder_names([str(tmp_path / "a" / "Pictures"), str(tmp_path / "b" / "Pictures"), str(tmp_path / "Phone")])
    assert list(names.values()) == ["Pictures", "Pictures (2)", "Phone"]


def test_a_backup_inside_the_collection_isnt_backed_up_into_itself(setup):
    pictures, _ = setup
    backup.run_backup(everything(backup.plan_backup([str(pictures)], str(pictures))))
    again = backup.plan_backup([str(pictures)], str(pictures))
    assert everything(again) == []


def test_stopping_leaves_no_partial_files(setup):
    pictures, usb = setup
    files = everything(backup.plan_backup([str(pictures)], str(usb)))
    calls = iter([False, True])
    result = backup.run_backup(files, should_stop=lambda: next(calls, True))
    assert result.stopped and result.copied == 1
    leftovers = [f for _, _, names in os.walk(usb) for f in names if f.endswith(".part")]
    assert leftovers == []


def test_a_failed_copy_is_reported_and_the_rest_continue(setup):
    pictures, usb = setup
    files = everything(backup.plan_backup([str(pictures)], str(usb)))
    os.remove(files[0].source)
    result = backup.run_backup(files)
    assert result.copied == 2 and len(result.failed) == 1


def test_library_snapshot(tmp_path, setup):
    pictures, usb = setup
    library = Library(tmp_path / "lib.db")
    library.scan([pictures])
    library.add_tag([library.query(PhotoFilter())[0].id], "Holiday")
    assert backup.save_library(tmp_path / "lib.db", str(usb))
    saved = sqlite3.connect(usb / "Simple-Photos Backup" / "Simple-Photos library.db")
    assert saved.execute("SELECT name FROM tags").fetchall() == [("Holiday",)]
    assert backup.free_space(str(usb)) > 0
