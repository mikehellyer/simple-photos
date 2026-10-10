"""Showing a photo in the system's file manager, with it selected."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional


def reveal_command(path: str) -> Optional[list[str]]:
    """The command that opens path's folder with path selected, or None
    if this system has no way to do that (the caller then just opens the
    folder)."""
    if sys.platform == "darwin":
        return ["open", "-R", path]
    if sys.platform == "win32":
        return ["explorer", f"/select,{os.path.normpath(path)}"]
    dbus_send = shutil.which("dbus-send")
    if dbus_send:
        # The freedesktop FileManager1 interface: Files (Nautilus), Dolphin,
        # Nemo, Caja and Thunar all answer it.
        return [
            dbus_send,
            "--session",
            "--dest=org.freedesktop.FileManager1",
            "--type=method_call",
            "/org/freedesktop/FileManager1",
            "org.freedesktop.FileManager1.ShowItems",
            f"array:string:{Path(path).absolute().as_uri()}",
            "string:",
        ]
    return None


def reveal_in_file_manager(path: str) -> bool:
    """True if a file manager was asked to show it."""
    command = reveal_command(path)
    if command is None:
        return False
    try:
        if sys.platform.startswith("linux"):
            return subprocess.run(command, timeout=5, capture_output=True).returncode == 0
        subprocess.Popen(command)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return True
