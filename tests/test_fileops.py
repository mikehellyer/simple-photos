import os
from unittest.mock import patch

import pytest

from simple_photos.core.fileops import create_folder, folder_name_problem, move_photos, reveal_command


def test_macos_reveals_with_open():
    with patch("simple_photos.core.fileops.sys.platform", "darwin"):
        assert reveal_command("/p/a.jpg") == ["open", "-R", "/p/a.jpg"]


def test_windows_selects_in_explorer():
    with patch("simple_photos.core.fileops.sys.platform", "win32"):
        assert reveal_command("C:/p/a.jpg")[0] == "explorer"


def test_linux_asks_the_file_manager_over_dbus():
    with patch("simple_photos.core.fileops.sys.platform", "linux"), patch(
        "simple_photos.core.fileops.shutil.which", return_value="/usr/bin/dbus-send"
    ):
        command = reveal_command("/p/my photo.jpg")
    assert command[0] == "/usr/bin/dbus-send"
    uri = next(arg for arg in command if arg.startswith("array:string:"))
    assert uri.startswith("array:string:file:///") and uri.endswith("/p/my%20photo.jpg")  # (a drive letter in between on Windows)


def test_linux_without_dbus_gives_up():
    with patch("simple_photos.core.fileops.sys.platform", "linux"), patch(
        "simple_photos.core.fileops.shutil.which", return_value=None
    ):
        assert reveal_command("/p/a.jpg") is None


def test_folder_name_rules():
    assert folder_name_problem("Holiday 2025") is None
    assert folder_name_problem("Rome, May") is None
    for bad in ("", "  ", " lead", "trail ", "a/b", "a\\b", "what?", "x:y", ".hidden", "..", "dots.", "CON", "com1.txt"):
        assert folder_name_problem(bad), bad


def test_create_folder(tmp_path):
    path = create_folder(str(tmp_path), "New")
    assert os.path.isdir(path)
    with pytest.raises(ValueError, match="already"):
        create_folder(str(tmp_path), "New")
    with pytest.raises(ValueError):
        create_folder(str(tmp_path), "a/b")


def test_move_photos_never_overwrites(tmp_path):
    src, dest = tmp_path / "src", tmp_path / "dest"
    (src / "sub").mkdir(parents=True)
    dest.mkdir()
    (src / "a.jpg").write_text("A")
    (src / "sub" / "a.jpg").write_text("A2")
    (src / "b.jpg").write_text("B")
    (dest / "b.jpg").write_text("already here")
    result = move_photos([str(src / "a.jpg"), str(src / "sub" / "a.jpg"), str(src / "b.jpg")], str(dest))
    assert [os.path.basename(new) for _, new in result.moved] == ["a.jpg", "a (2).jpg", "b (2).jpg"]
    assert (dest / "a (2).jpg").read_text() == "A2"
    assert (dest / "b.jpg").read_text() == "already here"
    assert not (src / "a.jpg").exists()


def test_move_photos_skips_ones_already_there_and_reports_failures(tmp_path):
    (tmp_path / "x.jpg").write_text("X")
    result = move_photos([str(tmp_path / "x.jpg"), str(tmp_path / "gone" / "y.jpg")], str(tmp_path))
    assert result.skipped == [str(tmp_path / "x.jpg")]
    result = move_photos([str(tmp_path / "missing.jpg")], str(tmp_path / "sub-that-isnt-there"))
    assert len(result.failed) == 1
    (tmp_path / "d").mkdir()
    result = move_photos([str(tmp_path / "missing.jpg")], str(tmp_path / "d"))
    assert result.failed and result.failed[0][0].endswith("missing.jpg")


def test_rename_folder(tmp_path):
    from simple_photos.core.fileops import rename_folder

    (tmp_path / "Rome").mkdir()
    (tmp_path / "Paris").mkdir()
    new = rename_folder(str(tmp_path / "Rome"), "Rome 2024")
    assert new == str(tmp_path / "Rome 2024") and os.path.isdir(new)
    with pytest.raises(ValueError, match="already"):
        rename_folder(new, "Paris")
    with pytest.raises(ValueError):
        rename_folder(new, "bad:name")
    assert rename_folder(new, "Rome 2024") == new


def test_blank_folders(tmp_path):
    from simple_photos.core.fileops import find_blank_folders, is_blank_folder

    root = tmp_path / "Pictures"
    for folder in ("Empty", "Nested/Deeper/Deepest", "Clutter", "Video", "Mixed/EmptyChild", "Mixed/Full", ".hidden"):
        (root / folder).mkdir(parents=True)
    (root / "Clutter" / ".DS_Store").write_text("x")
    (root / "Clutter" / "Thumbs.db").write_text("x")
    (root / "Video" / "clip.mp4").write_text("not a photo, but still a file")
    (root / "Mixed" / "Full" / "a.jpg").write_text("x")

    assert find_blank_folders([str(root)]) == [
        str(root / "Clutter"),
        str(root / "Empty"),
        str(root / "Mixed" / "EmptyChild"),
        str(root / "Nested"),  # its whole branch is blank: just the top
    ]
    assert is_blank_folder(str(root / "Nested" / "Deeper"))
    assert not is_blank_folder(str(root / "Video"))
    assert not is_blank_folder(str(root / "Mixed"))
    assert not is_blank_folder(str(root / "missing"))


def test_an_empty_collection_folder_is_never_offered(tmp_path):
    from simple_photos.core.fileops import find_blank_folders

    (tmp_path / "Pictures").mkdir()
    assert find_blank_folders([str(tmp_path / "Pictures"), str(tmp_path / "unplugged")]) == []


def test_links_count_as_content(tmp_path):
    from simple_photos.core.fileops import is_blank_folder

    if not hasattr(os, "symlink"):
        pytest.skip("no symlinks")
    (tmp_path / "Linky").mkdir()
    (tmp_path / "Elsewhere").mkdir()
    try:
        os.symlink(tmp_path / "Elsewhere", tmp_path / "Linky" / "shortcut")
    except OSError:
        pytest.skip("can't make symlinks here")
    assert not is_blank_folder(str(tmp_path / "Linky"))


def test_copy_photos_never_overwrites_and_keeps_dates(tmp_path):
    from simple_photos.core.fileops import copy_photos

    src, dest = tmp_path / "src", tmp_path / "dest"
    src.mkdir()
    dest.mkdir()
    (src / "a.jpg").write_text("A")
    (src / "b.jpg").write_text("B")
    (dest / "b.jpg").write_text("already here")
    os.utime(src / "a.jpg", (1_600_000_000, 1_600_000_000))
    result = copy_photos([str(src / "a.jpg"), str(src / "b.jpg")], str(dest))
    assert [os.path.basename(new) for _, new in result.copied] == ["a.jpg", "b (2).jpg"]
    assert (dest / "b.jpg").read_text() == "already here"
    assert (src / "a.jpg").exists() and (src / "b.jpg").exists()  # originals stay
    assert os.stat(dest / "a.jpg").st_mtime == 1_600_000_000
    into_itself = copy_photos([str(src / "a.jpg")], str(src))
    assert os.path.basename(into_itself.copied[0][1]) == "a (2).jpg"
    assert not [f for f in os.listdir(dest) if f.endswith(".part")]


def test_copy_reports_failures(tmp_path):
    from simple_photos.core.fileops import copy_photos

    (tmp_path / "d").mkdir()
    result = copy_photos([str(tmp_path / "missing.jpg")], str(tmp_path / "d"))
    assert result.failed and not result.copied
    assert copy_photos([str(tmp_path / "x.jpg")], str(tmp_path / "nowhere")).failed
