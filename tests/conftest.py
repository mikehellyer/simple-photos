from fractions import Fraction
from pathlib import Path

import pytest
from PIL import Image
from PIL.TiffImagePlugin import IFDRational


@pytest.fixture(autouse=True)
def _private_home(tmp_path, monkeypatch):
    """Never let a test touch the real ~/Pictures, cache or config."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(home / ".cache"))
    monkeypatch.setenv("XDG_DATA_HOME", str(home / ".local" / "share"))


def make_photo(
    path: Path,
    size=(64, 48),
    color=(200, 80, 40),
    taken: str | None = "2024:07:14 18:03:22",
    make="Canon",
    model="Canon EOS R6",
    orientation: int | None = None,
    gps: tuple[float, float] | None = None,
    fmt="JPEG",
) -> Path:
    """A small real image file with whatever EXIF a test needs."""
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", size, color)
    exif = Image.Exif()
    if make:
        exif[271] = make
    if model:
        exif[272] = model
    if orientation:
        exif[274] = orientation
    sub = exif.get_ifd(0x8769)
    if taken:
        sub[36867] = taken
    sub[33434] = IFDRational(1, 250)
    sub[33437] = IFDRational(28, 10)
    sub[34855] = 400
    sub[37386] = IFDRational(35, 1)
    sub[42036] = "RF35mm F1.8 MACRO IS STM"
    if gps:
        lat, lon = gps
        g = exif.get_ifd(0x8825)
        g[1] = "N" if lat >= 0 else "S"
        g[2] = _dms(abs(lat))
        g[3] = "E" if lon >= 0 else "W"
        g[4] = _dms(abs(lon))
    image.save(path, fmt, exif=exif.tobytes())
    return path


def _dms(value: float):
    degrees = int(value)
    minutes = int((value - degrees) * 60)
    seconds = round(((value - degrees) * 60 - minutes) * 60, 4)
    seconds = Fraction(seconds).limit_denominator(10000)
    return (IFDRational(degrees, 1), IFDRational(minutes, 1), IFDRational(seconds.numerator, seconds.denominator))


def make_dng(path: Path, width=128, height=96, orientation=1, taken="2024:07:14 18:03:22", model="Test Cam") -> Path:
    """A tiny but real DNG, laid out as cameras write them: a small RGB
    preview (red left half, blue right) with the raw sensor data in a
    sub-directory. Needs tifffile (a test-only dependency)."""
    import numpy as np
    import tifffile

    path.parent.mkdir(parents=True, exist_ok=True)
    preview = np.zeros((height // 2, width // 2, 3), np.uint8)
    preview[:, : width // 4] = (220, 40, 40)
    preview[:, width // 4 :] = (40, 40, 220)
    sensor = np.full((height, width), 2000, np.uint16)
    sensor[:, : width // 2] = 30000
    tags = [
        (271, "s", 0, "Simple", True),
        (272, "s", 0, model, True),
        (274, "H", 1, orientation, True),
        (306, "s", 0, taken, True),
        (36867, "s", 0, taken, True),
        (50706, "B", 4, (1, 4, 0, 0), True),  # DNGVersion
        (50708, "s", 0, model, True),  # UniqueCameraModel
        (50721, "2i", 9, (1, 1, 0, 1, 0, 1, 0, 1, 1, 1, 0, 1, 0, 1, 0, 1, 1, 1), True),  # ColorMatrix1
        (50778, "H", 1, 21, True),  # CalibrationIlluminant1: D65
    ]
    with tifffile.TiffWriter(path) as tif:
        tif.write(preview, photometric="rgb", subfiletype=1, subifds=1, extratags=tags)
        tif.write(
            sensor,
            photometric=32803,  # CFA
            extratags=[
                (33421, "H", 2, (2, 2), True),  # CFARepeatPatternDim
                (33422, "B", 4, (0, 1, 1, 2), True),  # CFAPattern: RGGB
                (50714, "H", 1, 0, True),  # BlackLevel
                (50717, "H", 1, 65535, True),  # WhiteLevel
            ],
        )
    return path
