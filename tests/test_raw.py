import os

import pytest

from conftest import make_dng, make_photo
from simple_photos.core import raw
from simple_photos.core.duplicates import Candidate, group_duplicates, raw_jpeg_pairs
from simple_photos.core.importer import find_photos
from simple_photos.core.library import Library, PhotoFilter
from simple_photos.core.metadata import is_photo_file, read_photo_info
from simple_photos.core.rotation import RotateError, can_rotate, rotate_photo
from simple_photos.core.thumbnails import ensure_thumbnail, load_image

pytestmark = pytest.mark.skipif(not raw.available(), reason="rawpy not installed")


def test_raw_files_are_recognised():
    for name in ("IMG_0001.CR3", "DSC_0001.NEF", "_DSC0001.ARW", "DSCF0001.RAF", "P1000001.RW2", "IMG_0001.dng"):
        assert is_photo_file(name), name
        assert raw.is_raw(name)
    assert not raw.is_raw("IMG_0001.JPG")


def test_reads_size_date_and_camera(tmp_path):
    info = read_photo_info(make_dng(tmp_path / "a.dng"))
    assert (info.width, info.height) == (128, 96)
    assert info.taken == "2024-07-14 18:03:22"
    assert info.camera_model == "Test Cam"


def test_a_sideways_raw_is_shown_upright(tmp_path):
    path = make_dng(tmp_path / "side.dng", orientation=6)
    info = read_photo_info(path)
    assert (info.width, info.height) == (96, 128)
    preview = raw.preview(path)
    assert preview.size == (48, 64)
    # Turned clockwise: the red left half is now the top.
    assert preview.getpixel((0, 0))[0] > 200
    assert preview.getpixel((0, preview.height - 1))[2] > 200
    assert raw.develop(path).size == (96, 128)


def test_thumbnails_come_from_the_preview(tmp_path):
    path = make_dng(tmp_path / "a.dng")
    assert load_image(path, 40).size == (40, 30)
    assert ensure_thumbnail(path, 1.0, tmp_path / "cache") is not None


def test_a_small_preview_gets_developed_for_the_viewer(tmp_path):
    path = make_dng(tmp_path / "a.dng")  # 64-wide preview of a 128-wide photo
    assert raw.needs_developing(path, 64, 48)
    assert not raw.needs_developing(path, 128, 96)


def test_a_damaged_raw_is_an_oserror(tmp_path):
    path = tmp_path / "broken.cr3"
    path.write_bytes(b"not a raw file")
    with pytest.raises(OSError):
        read_photo_info(path)
    with pytest.raises(OSError):
        raw.preview(path)
    assert ensure_thumbnail(path, 1.0, tmp_path / "cache") is None


def test_raws_are_scanned_and_imported(tmp_path):
    card = tmp_path / "card" / "DCIM"
    make_dng(card / "IMG_0001.dng")
    make_photo(card / "IMG_0001.JPG")
    assert sorted(os.path.basename(p.path) for p in find_photos(str(tmp_path / "card")).photos) == ["IMG_0001.JPG", "IMG_0001.dng"]
    library = Library(tmp_path / "lib.db")
    library.scan([tmp_path / "card"])
    assert sorted(row.filename for row in library.query(PhotoFilter())) == ["IMG_0001.JPG", "IMG_0001.dng"]


def test_raws_are_not_rotated(tmp_path):
    path = make_dng(tmp_path / "a.dng")
    assert not can_rotate(path)
    with pytest.raises(RotateError):
        rotate_photo(path, 1)


def test_a_cameras_raw_and_jpeg_of_one_shot_are_not_duplicates():
    def cand(i, path):
        return Candidate(i, path, 100, 100, 1000, 0, 0, 0.0)

    shots = [cand(1, "/p/IMG_0001.CR3"), cand(2, "/p/IMG_0001.JPG"), cand(3, "/q/IMG_0001.JPG"), cand(4, "/p/IMG_0002.CR3")]
    pairs = raw_jpeg_pairs(shots)
    assert pairs == {frozenset((1, 2))}
    same = 0x0F0F0F0F0F0F0F0F
    assert group_duplicates({}, {1: same, 2: same}, 4, pairs) == []
    assert group_duplicates({}, {1: same, 3: same}, 4, pairs) == [[1, 3]]  # a copy elsewhere still is one
