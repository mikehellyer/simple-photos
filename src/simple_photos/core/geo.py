"""Map arithmetic and map tiles, for the map view.

The map is the standard "slippy map" every web map uses: Web Mercator,
cut into 256-pixel tiles at each zoom level (the whole world is one tile
at zoom 0, four at zoom 1, …). A "world pixel" here is a position on the
whole map at a given zoom.

Tiles come from OpenStreetMap. Its tile usage policy asks apps to send a
User-Agent that identifies them, to cache tiles, to keep to two
connections, and to credit OpenStreetMap on the map — all done here and
in the map view. Tiles are kept on disk, so places already seen work
offline too.
"""
from __future__ import annotations

import math
import os
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from simple_photos.core.http import SSL_CONTEXT

TILE_SIZE = 256
MIN_ZOOM, MAX_ZOOM = 2, 18
MAX_LATITUDE = 85.05112878  # Web Mercator's limit
TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
ATTRIBUTION = "© OpenStreetMap contributors"
ATTRIBUTION_URL = "https://www.openstreetmap.org/copyright"
TILE_TIMEOUT_SECONDS = 10
MAX_TILE_CONNECTIONS = 2  # as OpenStreetMap's policy asks


def world_size(zoom: int) -> int:
    return TILE_SIZE << zoom


def to_world(lat: float, lon: float, zoom: int) -> tuple[float, float]:
    """Latitude/longitude → world pixel at this zoom."""
    lat = max(-MAX_LATITUDE, min(MAX_LATITUDE, lat))
    size = world_size(zoom)
    x = (lon + 180.0) / 360.0 * size
    sin = math.sin(math.radians(lat))
    y = (0.5 - math.log((1 + sin) / (1 - sin)) / (4 * math.pi)) * size
    return x, y


def from_world(x: float, y: float, zoom: int) -> tuple[float, float]:
    """World pixel at this zoom → latitude/longitude."""
    size = world_size(zoom)
    lon = x / size * 360.0 - 180.0
    n = math.pi - 2 * math.pi * y / size
    lat = math.degrees(math.atan(math.sinh(n)))
    return lat, lon


def tiles_for_view(centre_x: float, centre_y: float, width: int, height: int, zoom: int) -> list[tuple[int, int, int, int]]:
    """(tile x, tile y, screen x, screen y) for every tile the view shows.
    Tile x wraps round the world; tile y doesn't (the poles are the edge)."""
    left, top = centre_x - width / 2, centre_y - height / 2
    count = 1 << zoom
    first_x, first_y = math.floor(left / TILE_SIZE), math.floor(top / TILE_SIZE)
    last_x, last_y = math.floor((left + width - 1) / TILE_SIZE), math.floor((top + height - 1) / TILE_SIZE)
    tiles = []
    for ty in range(max(0, first_y), min(count - 1, last_y) + 1):
        for tx in range(first_x, last_x + 1):
            screen_x = round(tx * TILE_SIZE - left)
            screen_y = round(ty * TILE_SIZE - top)
            tiles.append((tx % count, ty, screen_x, screen_y))
    return tiles


def fit(points: Iterable[tuple[float, float]], width: int, height: int, padding: int = 60) -> tuple[int, float, float]:
    """(zoom, centre lat, centre lon) showing every point. One point, or
    points very close together, get a street-level zoom, not the maximum."""
    points = list(points)
    if not points:
        return MIN_ZOOM, 30.0, 0.0
    lats = [p[0] for p in points]
    lons = [p[1] for p in points]
    centre = ((min(lats) + max(lats)) / 2, (min(lons) + max(lons)) / 2)
    usable_w, usable_h = max(1, width - 2 * padding), max(1, height - 2 * padding)
    zoom = 15
    for candidate in range(15, MIN_ZOOM - 1, -1):
        x1, y1 = to_world(max(lats), min(lons), candidate)
        x2, y2 = to_world(min(lats), max(lons), candidate)
        if x2 - x1 <= usable_w and y2 - y1 <= usable_h:
            zoom = candidate
            break
        zoom = candidate
    cx, cy = to_world(*centre, zoom)
    lat, lon = from_world(cx, cy, zoom)
    return zoom, lat, lon


@dataclass
class Cluster:
    x: float  # world pixel at the zoom it was made for
    y: float
    ids: list[int]


def cluster(points: Iterable[tuple[int, float, float]], zoom: int, cell: int = 64) -> list[Cluster]:
    """Group (photo id, lat, lon) points that would sit within about a
    cell of each other on screen at this zoom. Each cluster sits at the
    average position of its photos."""
    cells: dict[tuple[int, int], list[tuple[int, float, float]]] = {}
    for photo_id, lat, lon in points:
        x, y = to_world(lat, lon, zoom)
        cells.setdefault((int(x // cell), int(y // cell)), []).append((photo_id, x, y))
    clusters = []
    for members in cells.values():
        clusters.append(
            Cluster(
                x=sum(m[1] for m in members) / len(members),
                y=sum(m[2] for m in members) / len(members),
                ids=[m[0] for m in members],
            )
        )
    # Merge neighbours that ended up either side of a cell border.
    merged: list[Cluster] = []
    for c in sorted(clusters, key=lambda c: -len(c.ids)):
        for m in merged:
            if abs(m.x - c.x) < cell * 0.75 and abs(m.y - c.y) < cell * 0.75:
                total = len(m.ids) + len(c.ids)
                m.x = (m.x * len(m.ids) + c.x * len(c.ids)) / total
                m.y = (m.y * len(m.ids) + c.y * len(c.ids)) / total
                m.ids.extend(c.ids)
                break
        else:
            merged.append(Cluster(c.x, c.y, list(c.ids)))
    return merged


# --- tiles -------------------------------------------------------------------


def tile_cache_dir() -> Path:
    from platformdirs import user_cache_dir

    return Path(user_cache_dir("Simple-Photos")) / "map-tiles"


def tile_path(zoom: int, x: int, y: int, cache_dir: Path) -> Path:
    return cache_dir / str(zoom) / str(x) / f"{y}.png"


def fetch_tile(zoom: int, x: int, y: int, cache_dir: Path, user_agent: str) -> Optional[Path]:
    """The tile's file, downloading it first if it isn't cached. None if
    it can't be had (offline, say). Never raises."""
    dest = tile_path(zoom, x, y, cache_dir)
    if dest.exists():
        return dest
    request = urllib.request.Request(TILE_URL.format(z=zoom, x=x, y=y), headers={"User-Agent": user_agent})
    try:
        with urllib.request.urlopen(request, timeout=TILE_TIMEOUT_SECONDS, context=SSL_CONTEXT) as response:
            data = response.read()
        if not data.startswith(b"\x89PNG"):
            return None
        dest.parent.mkdir(parents=True, exist_ok=True)
        partial = dest.with_suffix(f".{os.getpid()}.part")
        partial.write_bytes(data)
        os.replace(partial, dest)
    except (OSError, ValueError):
        return None
    return dest
