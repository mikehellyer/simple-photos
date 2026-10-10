from PIL import Image

from conftest import make_photo
from simple_photos.core.thumbnails import ensure_thumbnail, load_image


def test_thumbnail_is_made_once_and_cached(tmp_path):
    photo = make_photo(tmp_path / "big.jpg", size=(1200, 800))
    cache = tmp_path / "cache"
    first = ensure_thumbnail(photo, 1.0, cache, size=200)
    assert first is not None and first.exists()
    with Image.open(first) as thumb:
        assert thumb.size == (200, 133)
    stamp = first.stat().st_mtime_ns
    assert ensure_thumbnail(photo, 1.0, cache, size=200) == first
    assert first.stat().st_mtime_ns == stamp
    assert ensure_thumbnail(photo, 2.0, cache, size=200) != first  # file changed → new thumbnail


def test_thumbnail_follows_exif_orientation(tmp_path):
    photo = make_photo(tmp_path / "side.jpg", size=(300, 200), orientation=6)
    with Image.open(ensure_thumbnail(photo, 1.0, tmp_path / "cache", size=150)) as thumb:
        assert thumb.size == (100, 150)


def test_transparent_png_thumbnail(tmp_path):
    path = tmp_path / "logo.png"
    Image.new("RGBA", (40, 40), (0, 0, 0, 0)).save(path)
    with Image.open(ensure_thumbnail(path, 1.0, tmp_path / "cache", size=20)) as thumb:
        assert thumb.getpixel((5, 5)) == (255, 255, 255)


def test_broken_file_gives_no_thumbnail(tmp_path):
    path = tmp_path / "broken.jpg"
    path.write_bytes(b"nope")
    assert ensure_thumbnail(path, 1.0, tmp_path / "cache") is None


def test_load_image_full_size_is_upright(tmp_path):
    photo = make_photo(tmp_path / "side.jpg", size=(300, 200), orientation=8)
    assert load_image(photo).size == (200, 300)


def test_move_thumbnail_keeps_the_cached_file(tmp_path):
    from simple_photos.core.thumbnails import move_thumbnail, thumbnail_path

    photo = make_photo(tmp_path / "a.jpg")
    cache = tmp_path / "cache"
    ensure_thumbnail(photo, 1.0, cache)
    move_thumbnail(photo, tmp_path / "moved" / "a.jpg", 1.0, cache)
    assert thumbnail_path(tmp_path / "moved" / "a.jpg", 1.0, cache).exists()
    move_thumbnail(tmp_path / "nothing.jpg", tmp_path / "x.jpg", 1.0, cache)  # no thumbnail: no error
