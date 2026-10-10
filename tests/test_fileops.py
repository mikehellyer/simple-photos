from unittest.mock import patch

from simple_photos.core.fileops import reveal_command


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
    assert "array:string:file:///p/my%20photo.jpg" in command


def test_linux_without_dbus_gives_up():
    with patch("simple_photos.core.fileops.sys.platform", "linux"), patch(
        "simple_photos.core.fileops.shutil.which", return_value=None
    ):
        assert reveal_command("/p/a.jpg") is None
