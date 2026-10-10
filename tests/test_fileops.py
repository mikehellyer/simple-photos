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
