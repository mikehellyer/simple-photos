import json
import os
import shutil
import sqlite3

import pytest

from conftest import make_photo
from simple_photos.core import backup, restore
from simple_photos.core.library import Library, PhotoFilter


def back_up(roots, usb, db_path):
    files = [f for plan in backup.plan_backup(roots, str(usb)) for f in plan.to_copy]
    backup.run_backup(files)
    backup.save_library(db_path, str(usb))
    backup.write_manifest(roots, str(usb))


def restore_all(usb, roots, db_path):
    backup_dir = restore.find_backup(str(usb))
    plans = restore.plan_restore(backup_dir, roots)
    result = backup.run_backup([f for p in plans for f in p.to_copy], replace=False)
    library = Library(db_path)
    library.scan(roots)
    changed = library.add_backed_up_info(restore.backed_up_info(backup_dir, plans))
    return plans, result, library, changed


@pytest.fixture
def computer(tmp_path):
    pictures = tmp_path / "home" / "Pictures"
    for name in ("a.jpg", "b.jpg", "c.jpg"):
        make_photo(pictures / "2024" / name, color=(len(name) * 30, ord(name[0]), 9))
    (pictures / "2024" / "clip.mp4").write_bytes(b"video")
    library = Library(tmp_path / "lib.db")
    library.scan([pictures])
    ids = {row.filename: row.id for row in library.query(PhotoFilter())}
    library.set_rating([ids["a.jpg"]], 5)
    library.add_tag([ids["a.jpg"], ids["b.jpg"]], "Rome")
    library.set_caption([ids["b.jpg"]], "Colosseum")
    library.close()
    return pictures, tmp_path / "lib.db", tmp_path / "usb"


def tags_by_name(library):
    return {row.filename: (library.photo(row.id).rating, library.photo(row.id).caption, library.tags_for([row.id]))
            for row in library.query(PhotoFilter())}


def test_lost_photos_and_library_come_back(computer, tmp_path):
    pictures, db, usb = computer
    usb.mkdir()
    back_up([str(pictures)], usb, db)
    os.remove(pictures / "2024" / "a.jpg")  # deleted by mistake
    os.remove(db)  # and the library lost too
    plans, result, library, changed = restore_all(usb, [str(pictures)], db)
    assert [(p.name, p.matched, len(p.to_copy), p.already_here) for p in plans] == [("Pictures", True, 1, 3)]
    assert result.copied == 1 and (pictures / "2024" / "a.jpg").exists()
    assert tags_by_name(library)["a.jpg"] == (5, "", {"Rome": 1})
    assert tags_by_name(library)["b.jpg"] == (0, "Colosseum", {"Rome": 1})
    assert changed == 2


def test_nothing_on_the_computer_is_overwritten(computer):
    pictures, db, usb = computer
    usb.mkdir()
    back_up([str(pictures)], usb, db)
    edited = pictures / "2024" / "b.jpg"
    edited.write_bytes(edited.read_bytes() + b"edited since")
    plans = restore.plan_restore(restore.find_backup(str(usb)), [str(pictures)])
    assert (plans[0].different, plans[0].to_copy) == (1, [])
    assert edited.read_bytes().endswith(b"edited since")


def test_ratings_are_added_to_not_replaced(computer):
    pictures, db, usb = computer
    usb.mkdir()
    back_up([str(pictures)], usb, db)
    library = Library(db)
    ids = {row.filename: row.id for row in library.query(PhotoFilter())}
    library.set_rating([ids["a.jpg"]], 2)  # changed since the backup: kept
    library.add_tag([ids["a.jpg"]], "Italy")
    library.close()
    _, _, library, _ = restore_all(usb, [str(pictures)], db)
    assert tags_by_name(library)["a.jpg"] == (2, "", {"Italy": 1, "Rome": 1})


def test_a_new_computer_with_a_different_home_folder(computer, tmp_path):
    pictures, db, usb = computer
    usb.mkdir()
    back_up([str(pictures)], usb, db)
    new_pictures = tmp_path / "newhome" / "mike" / "Pictures"
    new_pictures.mkdir(parents=True)
    plans, result, library, _ = restore_all(usb, [str(new_pictures)], tmp_path / "new.db")
    assert result.copied == 4 and (new_pictures / "2024" / "clip.mp4").exists()
    assert tags_by_name(library)["a.jpg"] == (5, "", {"Rome": 1})


def test_backups_from_before_the_manifest_still_restore_tags(computer, tmp_path):
    pictures, db, usb = computer
    usb.mkdir()
    back_up([str(pictures)], usb, db)
    os.remove(usb / "Simple-Photos Backup" / "Simple-Photos backup.json")
    new_pictures = tmp_path / "other" / "Pictures"
    new_pictures.mkdir(parents=True)
    _, _, library, _ = restore_all(usb, [str(new_pictures)], tmp_path / "new.db")
    assert tags_by_name(library)["b.jpg"] == (0, "Colosseum", {"Rome": 1})


def test_a_backup_made_on_windows(computer, tmp_path):
    pictures, db, usb = computer
    usb.mkdir()
    back_up([str(pictures)], usb, db)
    backup_dir = usb / "Simple-Photos Backup"
    saved = sqlite3.connect(backup_dir / "Simple-Photos library.db")
    for photo_id, path in saved.execute("SELECT id, path FROM photos").fetchall():
        tail = os.path.relpath(path, pictures).replace(os.sep, "\\")
        saved.execute("UPDATE photos SET path = ? WHERE id = ?", ("C:\\Users\\Mike\\Pictures\\" + tail, photo_id))
    saved.commit()
    saved.close()
    (backup_dir / "Simple-Photos backup.json").write_text(json.dumps({"folders": {"Pictures": "C:\\Users\\Mike\\Pictures"}}))
    new_pictures = tmp_path / "linux" / "Pictures"
    new_pictures.mkdir(parents=True)
    _, _, library, _ = restore_all(usb, [str(new_pictures)], tmp_path / "new.db")
    assert tags_by_name(library)["a.jpg"] == (5, "", {"Rome": 1})


def test_a_folder_with_no_match_goes_inside_the_first_collection_folder(computer, tmp_path):
    pictures, db, usb = computer
    usb.mkdir()
    back_up([str(pictures)], usb, db)
    shutil.copytree(usb / "Simple-Photos Backup" / "Pictures", usb / "Simple-Photos Backup" / "Old Phone")
    plans = restore.plan_restore(restore.find_backup(str(usb)), [str(pictures)])
    old_phone = next(p for p in plans if p.name == "Old Phone")
    assert (old_phone.matched, old_phone.target) == (False, str(pictures / "Old Phone"))


def test_finding_the_backup(tmp_path):
    (tmp_path / "usb" / "Simple-Photos Backup").mkdir(parents=True)
    assert restore.find_backup(str(tmp_path / "usb")) == str(tmp_path / "usb" / "Simple-Photos Backup")
    assert restore.find_backup(str(tmp_path / "usb" / "Simple-Photos Backup")) == str(tmp_path / "usb" / "Simple-Photos Backup")
    assert restore.find_backup(str(tmp_path)) is None


def test_restore_never_replaces_a_file_that_appeared_meanwhile(tmp_path):
    source = tmp_path / "backup.jpg"
    source.write_text("from the backup")
    target = tmp_path / "here.jpg"
    target.write_text("made since")
    result = backup.run_backup([backup.FileToCopy(str(source), str(target), 15)], replace=False)
    assert (result.copied, result.skipped) == (0, 1)
    assert target.read_text() == "made since"


def test_a_photo_moved_since_the_backup_isnt_brought_back_twice(computer):
    pictures, db, usb = computer
    usb.mkdir()
    back_up([str(pictures)], usb, db)
    (pictures / "Favourites").mkdir()
    os.rename(pictures / "2024" / "a.jpg", pictures / "Favourites" / "a.jpg")
    plans = restore.plan_restore(restore.find_backup(str(usb)), [str(pictures)])
    assert (plans[0].elsewhere, plans[0].to_copy) == (1, [])


def test_photos_put_aside_by_tidying_are_offered_but_not_ticked(computer):
    pictures, db, usb = computer
    usb.mkdir()
    back_up([str(pictures)], usb, db)
    os.remove(pictures / "2024" / "b.jpg")
    plans = backup.plan_backup([str(pictures)], str(usb))
    backup.tidy_up(plans, str(usb))
    plans = restore.plan_restore(restore.find_backup(str(usb)), [str(pictures)])
    assert [(p.name, p.removed, len(p.to_copy)) for p in plans] == [("Pictures", False, 0), ("Pictures", True, 1)]
    assert plans[1].to_copy[0].target == str(pictures / "2024" / "b.jpg")  # back where it was
