import os

import pytest
from PIL import Image

from conftest import make_photo
from simple_photos.core.rotation import RotateError, can_rotate, read_jpeg_orientation, rotate_photo, turned
from simple_photos.core.thumbnails import load_image


def corner_colour(path):
    """Colour of the top-left corner as displayed (EXIF orientation applied)."""
    return load_image(path).getpixel((0, 0))[:3]


def two_tone(path, fmt="JPEG", exif=True, orientation=None):
    """A 60×40 image, red on the left and blue on the right — with the
    usual test EXIF (camera, date…) unless exif=False."""
    image = Image.new("RGB", (60, 40), (0, 0, 255))
    image.paste((255, 0, 0), (0, 0, 30, 40))
    if not exif:
        image.save(path, fmt)
        return path
    make_photo(path, size=(60, 40), orientation=orientation, fmt=fmt)
    with Image.open(path) as original:
        exif_bytes = original.info["exif"]
    image.save(path, fmt, exif=exif_bytes, quality=95)
    return path


def test_orientation_arithmetic_matches_pillow():
    """turned() must agree with how Pillow displays each orientation."""
    base = Image.new("RGB", (3, 2))
    for i, xy in enumerate([(0, 0), (1, 0), (2, 0), (0, 1), (1, 1), (2, 1)]):
        base.putpixel(xy, (i * 40, 0, 0))

    def shown(orientation):
        image = base.copy()
        exif = image.getexif()
        exif[274] = orientation
        image.info["exif"] = exif.tobytes()
        from PIL import ImageOps

        upright = ImageOps.exif_transpose(image)
        return upright.tobytes(), upright.size

    for orientation in range(1, 9):
        pixels, size = shown(orientation)
        expected = Image.frombytes("RGB", size, pixels).transpose(Image.Transpose.ROTATE_270)  # a quarter turn clockwise
        assert shown(turned(orientation, 1)) == (expected.tobytes(), expected.size), orientation


def test_jpeg_rotation_only_changes_the_tag(tmp_path):
    path = two_tone(tmp_path / "a.jpg", orientation=1)
    before = path.read_bytes()
    stat = os.stat(path)
    rotate_photo(path, 1)
    after = path.read_bytes()
    assert read_jpeg_orientation(after) == 6
    assert len(after) == len(before)
    assert sum(a != b for a, b in zip(before, after)) <= 2  # just the tag's value
    assert os.stat(path).st_mtime_ns == stat.st_mtime_ns
    assert load_image(path).size == (40, 60)
    assert corner_colour(path)[0] > 200  # turned clockwise: the red left side is now the top
    rotate_photo(path, -1)
    assert path.read_bytes() == before


def test_jpeg_without_orientation_tag_gets_one(tmp_path):
    path = two_tone(tmp_path / "a.jpg", orientation=None)
    assert read_jpeg_orientation(path.read_bytes()) == 1
    rotate_photo(path, 3)
    assert read_jpeg_orientation(path.read_bytes()) == 8
    with Image.open(path) as image:
        exif = image.getexif()
        assert exif[272] == "Canon EOS R6"  # the rest of the EXIF survives
        assert exif.get_ifd(0x8769)[36867] == "2024:07:14 18:03:22"
    assert load_image(path).size == (40, 60)


def test_jpeg_without_any_exif(tmp_path):
    path = two_tone(tmp_path / "plain.jpg", exif=False)
    pixels_before = Image.open(path).tobytes()
    rotate_photo(path, 2)
    assert read_jpeg_orientation(path.read_bytes()) == 3
    assert Image.open(path).tobytes() == pixels_before  # not re-encoded
    assert corner_colour(path)[2] > 200  # upside down: blue now on the left


def test_png_is_turned_losslessly(tmp_path):
    path = two_tone(tmp_path / "a.png", fmt="PNG", exif=False)
    rotate_photo(path, 1)
    with Image.open(path) as image:
        assert image.size == (40, 60)
        assert image.getpixel((0, 0)) == (255, 0, 0)  # red was left, now top
        assert image.getpixel((0, 59)) == (0, 0, 255)


def test_unsupported_formats_are_refused(tmp_path):
    path = tmp_path / "a.webp"
    Image.new("RGB", (10, 10)).save(path)
    assert not can_rotate(path)
    with pytest.raises(RotateError):
        rotate_photo(path, 1)
    gif = tmp_path / "anim.gif"
    frames = [Image.new("RGB", (10, 10), colour) for colour in ("red", "blue")]
    frames[0].save(gif, save_all=True, append_images=frames[1:])
    with pytest.raises(RotateError):
        rotate_photo(gif, 1)
