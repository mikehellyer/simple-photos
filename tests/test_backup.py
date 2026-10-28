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


# --- following moves and renames ---------------------------------------------


def do_backup(pictures, usb, tidy=False):
    plans = backup.plan_backup([str(pictures)], str(usb))
    moved, could_not = backup.run_moves([m for p in plans for m in p.moves])
    files = everything(plans) + [backup.FileToCopy(m.source, m.new, m.size) for m in could_not]
    result = backup.run_backup(files)
    tidied = backup.tidy_up(plans, str(usb)) if tidy else None
    backup.prune_empty_folders(backup.backup_root(str(usb)), keep=[os.path.join(backup.backup_root(str(usb)), backup.REMOVED_FOLDER)])
    return plans, moved, result, tidied


def backed_up_files(usb):
    root = usb / "Simple-Photos Backup"
    return sorted(os.path.relpath(os.path.join(d, f), root) for d, _, fs in os.walk(root) for f in fs if not f.endswith((".db", ".json")))


def test_a_renamed_folder_is_moved_in_the_backup_not_copied_again(setup):
    pictures, usb = setup
    do_backup(pictures, usb)
    os.rename(pictures / "2024" / "Rome", pictures / "2024" / "Rome trip")
    plans, moved, result, _ = do_backup(pictures, usb)
    assert (moved, result.copied, len(plans[0].removed), len(plans[0].leftovers)) == (3, 0, 0, 0)
    assert backed_up_files(usb) == [
        os.path.join("Pictures", "2024", "Rome trip", name) for name in ("a.jpg", "b.jpg", "clip.mp4")
    ]  # and the old, now empty, Rome folder is gone from the backup
    assert everything(backup.plan_backup([str(pictures)], str(usb))) == []


def test_a_moved_and_a_renamed_photo_are_followed(setup):
    pictures, usb = setup
    do_backup(pictures, usb)
    (pictures / "Best").mkdir()
    os.rename(pictures / "2024" / "Rome" / "a.jpg", pictures / "Best" / "a.jpg")
    os.rename(pictures / "2024" / "Rome" / "b.jpg", pictures / "2024" / "Rome" / "Colosseum.jpg")
    plans, moved, result, _ = do_backup(pictures, usb)
    assert (moved, result.copied) == (2, 0)
    assert os.path.join("Pictures", "Best", "a.jpg") in backed_up_files(usb)
    assert os.path.join("Pictures", "2024", "Rome", "Colosseum.jpg") in backed_up_files(usb)


def test_an_edited_and_moved_photo_is_copied_and_its_old_copy_kept(setup):
    pictures, usb = setup
    do_backup(pictures, usb)
    (pictures / "Edited").mkdir()
    os.rename(pictures / "2024" / "Rome" / "a.jpg", pictures / "Edited" / "a.jpg")
    with open(pictures / "Edited" / "a.jpg", "ab") as f:
        f.write(b"edited")
    plans, moved, result, _ = do_backup(pictures, usb)
    assert (moved, result.copied) == (0, 1)
    assert plans[0].removed == [str(usb / "Simple-Photos Backup" / "Pictures" / "2024" / "Rome" / "a.jpg")]
    assert os.path.join("Pictures", "2024", "Rome", "a.jpg") in backed_up_files(usb)  # not tidied: kept where it was


def test_tidying_puts_deleted_photos_aside_and_keeps_them(setup):
    pictures, usb = setup
    do_backup(pictures, usb)
    os.remove(pictures / "2024" / "Rome" / "b.jpg")
    _, _, _, tidied = do_backup(pictures, usb, tidy=True)
    assert (tidied.put_aside, tidied.leftovers_removed) == (1, 0)
    assert os.path.join("Removed from computer", "Pictures", "2024", "Rome", "b.jpg") in backed_up_files(usb)
    assert os.path.join("Pictures", "2024", "Rome", "b.jpg") not in backed_up_files(usb)
    again = backup.plan_backup([str(pictures)], str(usb))
    assert (everything(again), again[0].removed, again[0].moves) == ([], [], [])  # the Removed folder is left alone


def test_tidying_clears_leftovers_from_older_backups(setup):
    """Backups made before moves were followed copied a renamed folder
    again and kept the old one: tidying removes the old copies — each
    identical to a file still backed up at its current place."""
    pictures, usb = setup
    do_backup(pictures, usb)
    os.rename(pictures / "2024" / "Rome", pictures / "2024" / "Roma")
    import shutil

    shutil.copytree(usb / "Simple-Photos Backup" / "Pictures" / "2024" / "Rome", usb / "Simple-Photos Backup" / "Pictures" / "2024" / "Roma")
    plans = backup.plan_backup([str(pictures)], str(usb))
    assert (len(plans[0].moves), len(plans[0].leftovers), len(plans[0].removed)) == (0, 3, 0)
    _, _, _, tidied = do_backup(pictures, usb, tidy=True)
    assert (tidied.put_aside, tidied.leftovers_removed) == (0, 3)
    assert backed_up_files(usb) == [os.path.join("Pictures", "2024", "Roma", n) for n in ("a.jpg", "b.jpg", "clip.mp4")]


def test_same_content_reads_only_the_ends(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    data = bytes(range(256)) * 2000  # 512 KB
    a.write_bytes(data)
    b.write_bytes(data)
    assert backup.same_content(str(a), str(b))
    b.write_bytes(data[:-1] + b"\x00")
    assert not backup.same_content(str(a), str(b))
    b.write_bytes(data[:10])
    assert not backup.same_content(str(a), str(b))


def test_a_move_that_cant_be_done_is_copied_from_the_computer_instead(setup):
    pictures, usb = setup
    do_backup(pictures, usb)
    os.rename(pictures / "2024" / "Rome" / "a.jpg", pictures / "2024" / "Rome" / "z.jpg")
    plans = backup.plan_backup([str(pictures)], str(usb))
    (move,) = plans[0].moves
    assert move.source == str(pictures / "2024" / "Rome" / "z.jpg")
    with open(move.new, "w") as blocker:  # something in the way
        blocker.write("x")
    moved, could_not = backup.run_moves([move])
    assert (moved, could_not) == (0, [move])
