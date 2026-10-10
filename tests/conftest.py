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
