import io
from unittest.mock import patch

import pytest

from simple_photos.core import geo


def test_round_trip_and_known_points():
    for lat, lon in ((51.5007, -0.1246), (-33.8568, 151.2153), (0, 0), (64.1, -21.9)):
        for zoom in (2, 10, 18):
            x, y = geo.to_world(lat, lon, zoom)
            back = geo.from_world(x, y, zoom)
            assert back == pytest.approx((lat, lon), abs=1e-6)
    assert geo.to_world(0, 0, 0) == pytest.approx((128, 128))
    # Agrees with the standard slippy-map tile formula (OSM wiki).
    import math

    for lat, lon, zoom in ((51.5007, -0.1246, 15), (-33.8568, 151.2153, 12), (64.1, -21.9, 7)):
        n = 2**zoom
        expected = (int((lon + 180) / 360 * n), int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n))
        x, y = geo.to_world(lat, lon, zoom)
        assert (int(x // 256), int(y // 256)) == expected


def test_tiles_cover_the_view_and_wrap_round_the_world():
    tiles = geo.tiles_for_view(256, 256, 512, 512, zoom=1)  # the whole world at zoom 1: 2×2 tiles
    assert sorted((t[0], t[1]) for t in tiles) == [(0, 0), (0, 1), (1, 0), (1, 1)]
    wrapped = geo.tiles_for_view(0, 256, 256, 256, zoom=1)  # straddling the date line
    assert sorted({t[0] for t in wrapped}) == [0, 1]
    assert all(0 <= t[1] < 2 for t in wrapped)


def test_fit_shows_everything():
    rome, cornwall = (41.9, 12.49), (50.1, -5.5)
    zoom, lat, lon = geo.fit([rome, cornwall], 800, 600)
    for point in (rome, cornwall):
        x, y = geo.to_world(*point, zoom)
        cx, cy = geo.to_world(lat, lon, zoom)
        assert abs(x - cx) <= 400 and abs(y - cy) <= 300
    assert zoom >= 3
    one_zoom, one_lat, one_lon = geo.fit([rome], 800, 600)
    assert one_zoom == 15 and (one_lat, one_lon) == pytest.approx(rome, abs=1e-6)
    assert geo.fit([], 800, 600)[0] == geo.MIN_ZOOM


def test_nearby_photos_cluster_and_split_when_zoomed_in():
    points = [(1, 41.9000, 12.4900), (2, 41.9005, 12.4905), (3, 41.9100, 12.5000), (4, 50.1, -5.5)]
    far = geo.cluster(points, zoom=5)
    assert sorted(sorted(c.ids) for c in far) == [[1, 2, 3], [4]]
    near = geo.cluster(points, zoom=16)
    assert sorted(sorted(c.ids) for c in near) == [[1, 2], [3], [4]]


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_tiles_are_fetched_once_with_a_user_agent(tmp_path):
    png = b"\x89PNG\r\n\x1a\nfake tile"
    with patch("urllib.request.urlopen", return_value=_Response(png)) as urlopen:
        path = geo.fetch_tile(3, 4, 2, tmp_path, "Simple-Photos/test")
        again = geo.fetch_tile(3, 4, 2, tmp_path, "Simple-Photos/test")
    assert path == again and path.read_bytes() == png
    assert urlopen.call_count == 1
    request = urlopen.call_args.args[0]
    assert request.full_url == "https://tile.openstreetmap.org/3/4/2.png"
    assert request.get_header("User-agent") == "Simple-Photos/test"


def test_offline_or_junk_gives_no_tile(tmp_path):
    with patch("urllib.request.urlopen", side_effect=OSError("offline")):
        assert geo.fetch_tile(3, 4, 2, tmp_path, "x") is None
    with patch("urllib.request.urlopen", return_value=_Response(b"<html>rate limited</html>")):
        assert geo.fetch_tile(3, 4, 2, tmp_path, "x") is None
    assert not geo.tile_path(3, 4, 2, tmp_path).exists()
