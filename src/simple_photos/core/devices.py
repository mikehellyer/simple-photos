"""Which plugged-in devices could hold photos: USB sticks, SD cards,
external drives, and cameras/phones.

The list of mounted volumes comes from Qt (QStorageInfo) in the GUI;
deciding which of them are devices, and what to call them, is done here
so it can be tested on any OS:

- Linux: anything the desktop mounted under /media or /run/media, plus
  cameras and phones in photo-transfer mode (MTP/PTP), which GNOME makes
  readable as folders under /run/user/<uid>/gvfs.
- macOS: volumes under /Volumes, except the startup disk and read-only
  ones (installer disk images).
- Windows: drive letters for removable and local drives, except the
  Windows drive itself (network drives and CD drives are left out).
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import Callable, Iterable, Optional
from urllib.parse import unquote


@dataclass(frozen=True)
class Volume:
    """What Qt reports about one mounted volume."""

    root: str
    label: str = ""
    read_only: bool = False


@dataclass(frozen=True)
class Device:
    path: str
    name: str  # e.g. "KINGSTON" — also used to name the import folder
    kind: str  # "drive" or "camera"
    where: str = ""  # e.g. "E:" on Windows

    @property
    def description(self) -> str:
        if self.kind == "camera":
            return f"{self.name} (camera or phone)"
        return f"{self.name} ({self.where})" if self.where else self.name


# Windows GetDriveTypeW values worth importing from: removable (USB sticks,
# SD cards) and fixed (USB hard drives also report as fixed).
_DRIVE_REMOVABLE, _DRIVE_FIXED = 2, 3


def _windows_drive_type(root: str) -> int:
    import ctypes

    return ctypes.windll.kernel32.GetDriveTypeW(root)


def pick_devices(
    volumes: Iterable[Volume],
    platform: str = sys.platform,
    gvfs_dir: Optional[str] = None,
    system_drive: Optional[str] = None,
    drive_type: Optional[Callable[[str], int]] = None,
) -> list[Device]:
    devices: list[Device] = []
    seen: set[str] = set()

    def add(path: str, name: str, kind: str, where: str = "") -> None:
        key = os.path.normcase(os.path.normpath(path))
        if key not in seen:
            seen.add(key)
            devices.append(Device(path=path, name=name, kind=kind, where=where))

    for volume in volumes:
        root = volume.root.rstrip("/\\") or volume.root
        base = os.path.basename(root)
        if platform.startswith("linux"):
            if root.startswith(("/media/", "/run/media/")):
                add(root, volume.label or base, "drive")
        elif platform == "darwin":
            if root.startswith("/Volumes/") and not base.startswith(".") and not volume.read_only:
                add(root, volume.label or base, "drive")
        elif platform == "win32":
            drive = root[:2].upper()
            system = (system_drive or os.environ.get("SystemDrive", "C:")).upper()[:2]
            if len(drive) == 2 and drive[1] == ":" and drive != system:
                kind = (drive_type or _windows_drive_type)(drive + "\\")
                if kind in (_DRIVE_REMOVABLE, _DRIVE_FIXED):
                    add(drive + "\\", volume.label or f"Drive {drive[0]}", "drive", where=drive)

    if platform.startswith("linux"):
        if gvfs_dir is None:
            gvfs_dir = f"/run/user/{os.getuid()}/gvfs" if hasattr(os, "getuid") else ""
        for entry in _list_dir(gvfs_dir):
            if entry.startswith(("mtp:", "gphoto2:")):
                add(os.path.join(gvfs_dir, entry), camera_name(entry), "camera")
    return devices


def _list_dir(path: str) -> list[str]:
    try:
        return sorted(os.listdir(path)) if path else []
    except OSError:
        return []


def camera_name(gvfs_entry: str) -> str:
    """'mtp:host=Google_Pixel_7_2B1C3D' → 'Google Pixel 7'. GNOME names a
    device by its USB vendor_model_serial."""
    host = gvfs_entry.split("host=", 1)[-1]
    host = unquote(host).replace("_", " ").strip()
    parts = host.split(" ")
    # Drop a trailing serial number: long, and mostly digits/hex.
    if len(parts) > 1 and len(parts[-1]) >= 6 and sum(ch.isdigit() for ch in parts[-1]) >= 3:
        parts = parts[:-1]
    if parts and parts[0].startswith("[usb:"):
        return "Camera"
    return " ".join(parts) or "Camera"
