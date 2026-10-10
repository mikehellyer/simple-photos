"""File operations on the collection itself: making folders, moving
photos between them, and showing a photo in the system's file manager."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

# Characters no folder name may contain on at least one of the three OSes.
_FORBIDDEN = set('<>:"/\\|?*') | {chr(c) for c in range(32)}
_RESERVED_WINDOWS = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def folder_name_problem(name: str) -> Optional[str]:
    """Why name can't be used for a new folder, or None if it's fine.
    Checked against all three OSes, so a collection on a shared drive
    works everywhere."""
    if not name.strip():
        return "Type a name for the folder."
    if name != name.strip():
        return "Folder names can't start or end with a space."
    if name in (".", "..") or name.startswith("."):
        return "Folder names can't start with a dot (they'd be hidden)."
    bad = sorted({ch for ch in name if ch in _FORBIDDEN})
    if bad:
        shown = " ".join(ch if ch.isprintable() else "control characters" for ch in bad)
        return f"Folder names can't contain {shown}"
    if name.endswith("."):
        return "Folder names can't end with a dot."
    if name.split(".")[0].upper() in _RESERVED_WINDOWS:
        return f"“{name}” is a reserved name on Windows."
    return None


def create_folder(parent: str, name: str) -> str:
    """Make parent/name and return its path. Raises ValueError with a
    message for the user if the name isn't allowed or already exists,
    and OSError if the folder can't be made."""
    problem = folder_name_problem(name)
    if problem:
        raise ValueError(problem)
    path = os.path.join(parent, name)
    if os.path.lexists(path):
        raise ValueError(f"There's already something called “{name}” in that folder.")
    os.mkdir(path)
    return path


def rename_folder(path: str, new_name: str) -> str:
    """Rename the folder in place and return its new path. Raises
    ValueError with a message for the user, or OSError."""
    problem = folder_name_problem(new_name)
    if problem:
        raise ValueError(problem)
    path = os.path.normpath(path)
    target = os.path.join(os.path.dirname(path), new_name)
    if target == path:
        return path
    # On macOS/Windows "Rome" → "rome" is the same folder: allowed.
    if os.path.lexists(target) and not os.path.samefile(path, target):
        raise ValueError(f"There's already something called “{new_name}” there.")
    os.rename(path, target)
    return target


def free_name(folder: str, filename: str, taken: set[str] = frozenset()) -> str:
    """filename, or 'name (2).ext', 'name (3).ext'… — whichever doesn't
    already exist in folder (or in taken, names already claimed)."""
    stem, ext = os.path.splitext(filename)
    candidate, n = filename, 1
    while os.path.lexists(os.path.join(folder, candidate)) or candidate.lower() in taken:
        n += 1
        candidate = f"{stem} ({n}){ext}"
    return candidate


@dataclass
class MoveResult:
    moved: list[tuple[str, str]] = field(default_factory=list)  # (old path, new path)
    skipped: list[str] = field(default_factory=list)  # already in the destination
    failed: list[tuple[str, str]] = field(default_factory=list)  # (path, reason)


def move_photos(
    paths: list[str], destination: str, progress: Optional[Callable[[int, int], None]] = None
) -> MoveResult:
    """Move each file into destination, never overwriting: a clash gets
    ' (2)' added to its name. Moving between drives copies then deletes,
    keeping the file's dates. One file failing doesn't stop the rest."""
    result = MoveResult()
    taken: set[str] = set()
    destination = os.path.normpath(destination)
    if not os.path.isdir(destination):
        result.failed = [(p, f"{destination} isn't there any more") for p in paths]
        return result
    for done, path in enumerate(paths, start=1):
        if os.path.normcase(os.path.dirname(os.path.normpath(path))) == os.path.normcase(destination):
            result.skipped.append(path)
        else:
            name = free_name(destination, os.path.basename(path), taken)
            taken.add(name.lower())
            target = os.path.join(destination, name)
            try:
                shutil.move(path, target)
            except OSError as error:
                result.failed.append((path, error.strerror or str(error)))
            else:
                result.moved.append((path, target))
        if progress:
            progress(done, len(paths))
    return result


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
