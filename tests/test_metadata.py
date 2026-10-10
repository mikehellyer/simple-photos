import os
import time

import pytest
from PIL import Image

from conftest import make_photo
from simple_photos.core.metadata import (
    HEIF_SUPPORTED,
    format_aperture,
    format_camera,
    format_exposure,
    is_photo_file,
    parse_exif_date,
    read_photo_info,
)


def test_reads_camera_exposure_and_date(tmp_path):
    info = read_photo_info(make_photo(tmp_path / "a.jpg"))
    assert (info.width, info.height) == (64, 48)
    assert info.taken == "2024-07-14 18:03:22"
    assert info.camera_make == "Canon" and info.camera_model == "Canon EOS R6"
    assert info.lens == "RF35mm F1.8 MACRO IS STM"
    assert info.iso == 400
    assert abs(info.exposure_time - 0.004) < 1e-9
    assert info.f_number == 2.8
    assert info.focal_length == 35


def test_sideways_photo_reports_its_upright_size(tmp_path):
    info = read_photo_info(make_photo(tmp_path / "a.jpg", size=(64, 48), orientation=6))
    assert (info.width, info.height) == (48, 64)


def test_reads_gps(tmp_path):
    info = read_photo_info(make_photo(tmp_path / "a.jpg", gps=(-33.8568, 151.2153)))
    assert abs(info.gps_lat - -33.8568) < 1e-4
    assert abs(info.gps_lon - 151.2153) < 1e-4


def test_falls_back_to_file_date_without_exif(tmp_path):
    path = tmp_path / "plain.png"
    Image.new("RGB", (10, 10)).save(path)
    stamp = time.mktime((2019, 3, 2, 10, 30, 0, 0, 0, -1))
    os.utime(path, (stamp, stamp))
    info = read_photo_info(path)
    assert info.taken == "2019-03-02 10:30:00"
    assert info.camera_model == ""


def test_placeholder_dates_are_ignored():
    assert parse_exif_date("0000:00:00 00:00:00") is None
    assert parse_exif_date("    :  :     :  :  ") is None
    assert parse_exif_date("2024:07:14 18:03:22\x00") == "2024-07-14 18:03:22"
    assert parse_exif_date(b"2001:01:02 03:04:05") == "2001-01-02 03:04:05"


def test_photo_files_are_recognised():
    assert is_photo_file("/x/IMG_0001.JPG")
    assert is_photo_file("holiday.tiff")
    assert not is_photo_file("notes.txt")
    assert not is_photo_file("._IMG_0001.jpg")  # macOS resource-fork litter


def test_display_formatting():
    assert format_exposure(1 / 250) == "1/250 s"
    assert format_exposure(2.5) == "2.5 s"
    assert format_aperture(2.8) == "f/2.8"
    assert format_aperture(8.0) == "f/8"
    assert format_camera("Canon", "Canon EOS R6") == "Canon EOS R6"
    assert format_camera("NIKON CORPORATION", "NIKON Z 6") == "NIKON Z 6"
    assert format_camera("Apple", "iPhone 15") == "Apple iPhone 15"


@pytest.mark.skipif(not HEIF_SUPPORTED, reason="pillow-heif not installed")
def test_reads_iphone_heic(tmp_path):
    import pillow_heif

    path = tmp_path / "IMG_0001.HEIC"
    pillow_heif.from_pillow(Image.new("RGB", (50, 40), "red")).save(path)
    assert is_photo_file(path)
    info = read_photo_info(path)
    assert (info.width, info.height) == (50, 40)
