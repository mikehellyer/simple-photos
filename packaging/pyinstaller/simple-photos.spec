# -*- mode: python ; coding: utf-8 -*-
"""Shared PyInstaller spec for all three platforms.

Built with: pyinstaller packaging/pyinstaller/simple-photos.spec
(run from the repo root, with the venv's PySide6 etc. on the path).
"""
import re
import sys
from pathlib import Path

# PySide6 6.12 drops a reference to None on every call to a method that
# returns nothing (QSlider.setValue, QLabel.setText, …). Before Python
# 3.12 None can actually be freed, so a few minutes of playback-progress
# updates used them all up and the app died with "Fatal Python error:
# none_dealloc". From 3.12 None is immortal and the bug is harmless —
# refuse to build a frozen app on anything older.
if sys.version_info < (3, 12):
    raise SystemExit(
        f"Simple-Photos must be built with Python 3.12+ (this is {sys.version.split()[0]}) "
        "— see the comment in simple-photos.spec"
    )

root = Path(SPECPATH).resolve().parent.parent
src = root / "src"
icons = root / "packaging" / "icons"

version = re.search(
    r'__version__ = "([^"]+)"', (src / "simple_photos" / "__init__.py").read_text()
).group(1)

if sys.platform == "win32":
    icon_path = str(icons / "icon.ico")
elif sys.platform == "darwin":
    icon_path = str(icons / "icon.icns")
else:
    icon_path = None

# Auto-Rotate's face detectors are data files inside OpenCV's package.
import cv2

cascades = Path(cv2.data.haarcascades)
cascade_files = [
    (str(cascades / name), "cv2/data")
    for name in ("haarcascade_frontalface_default.xml", "haarcascade_frontalface_alt2.xml", "haarcascade_eye.xml")
]

a = Analysis(
    [str(src / "simple_photos" / "app.py")],
    pathex=[str(src)],
    binaries=[],
    datas=[
        (str(src / "simple_photos" / "gui" / "resources" / "icon.png"), "simple_photos/gui/resources"),
        *cascade_files,
    ],
    hiddenimports=["pillow_heif", "cv2", "numpy"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Simple-Photos",
    debug=False,
    strip=False,
    upx=False,
    console=False,
    icon=icon_path,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="Simple-Photos",
)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="Simple-Photos.app",
        icon=icon_path,
        bundle_identifier="uk.hellyer.simplephotos",
        version=version,
        info_plist={
            "CFBundleShortVersionString": version,
            "CFBundleVersion": version,
            "NSHighResolutionCapable": True,
        },
    )
