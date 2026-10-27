"""The map view: where the photos shown were taken, on OpenStreetMap.

Drawn here with QPainter — tiles and photo pins — rather than in an
embedded web browser, which would add a couple of hundred megabytes to
the app. Photos close together share one pin (a thumbnail with a count)
that splits apart as you zoom in.
"""
from __future__ import annotations

import time
from collections import OrderedDict

from PySide6.QtCore import QObject, QPoint, QPointF, QRect, QRectF, QRunnable, Qt, QThreadPool, QUrl, Signal, Slot
from PySide6.QtGui import QColor, QDesktopServices, QFont, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QToolButton, QToolTip, QWidget

from simple_photos import __version__
from simple_photos.core import geo

USER_AGENT = f"Simple-Photos/{__version__} (+https://github.com/mikehellyer/simple-photos)"
PIN = 54  # px, a pin's thumbnail
TILE_MEMORY = 300  # tiles kept decoded
RETRY_FAILED_AFTER = 30  # seconds before a tile that failed is asked for again
LAND = QColor("#e8e4dc")
ACCENT = QColor("#2f6fd6")


class _TileJob(QRunnable):
    def __init__(self, relay: "_TileRelay", key: tuple[int, int, int]):
        super().__init__()
        self._relay, self._key = relay, key

    def run(self) -> None:
        zoom, x, y = self._key
        path = geo.fetch_tile(zoom, x, y, self._relay.cache_dir, USER_AGENT)
        self._relay.loaded.emit(zoom, x, y, str(path) if path else "")


class _TileRelay(QObject):
    """Emitted from a pool thread; connected queued to the map's slot."""

    loaded = Signal(int, int, int, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.cache_dir = geo.tile_cache_dir()


def _button(text: str, tip: str, parent) -> QToolButton:
    button = QToolButton(parent)
    button.setText(text)
    button.setToolTip(tip)
    button.setCursor(Qt.PointingHandCursor)
    button.setFocusPolicy(Qt.NoFocus)
    button.setFixedSize(34, 34)
    button.setStyleSheet(
        "QToolButton { background: white; color: #333; border: 1px solid #bbb; border-radius: 6px;"
        " font-size: 18px; font-weight: 600; }"
        "QToolButton:hover { background: #f0f0f0; }"
    )
    return button


class MapView(QWidget):
    """set_photos() gives it the photos to place. Clicking a pin emits
    pin_clicked(photo ids); double-clicking one emits pin_activated."""

    pin_clicked = Signal(list)
    pin_activated = Signal(list)

    def __init__(self, thumbnail_for, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setMinimumSize(200, 200)
        self._thumbnail_for = thumbnail_for  # callable(PhotoRow) -> QPixmap | None (and asks for it to load)
        self._rows: dict[int, object] = {}
        self._points: list[tuple[int, float, float]] = []
        self._total = 0
        self._zoom = geo.MIN_ZOOM
        self._centre = geo.to_world(30.0, 0.0, self._zoom)
        self._clusters: list[geo.Cluster] = []
        self._fitted_ids: frozenset[int] | None = None
        self._drag_from: QPoint | None = None
        self._dragged = False

        self._tiles: OrderedDict[tuple[int, int, int], QPixmap] = OrderedDict()
        self._pending: set[tuple[int, int, int]] = set()
        self._failed: dict[tuple[int, int, int], float] = {}
        self._relay = _TileRelay(self)
        self._relay.loaded.connect(self._on_tile_loaded, Qt.QueuedConnection)
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(geo.MAX_TILE_CONNECTIONS)

        self._zoom_in = _button("+", "Zoom in", self)
        self._zoom_in.clicked.connect(lambda: self.zoom_by(1, self._view_centre()))
        self._zoom_out = _button("−", "Zoom out", self)
        self._zoom_out.clicked.connect(lambda: self.zoom_by(-1, self._view_centre()))
        self._fit_button = _button("⤢", "Show all these photos", self)
        self._fit_button.clicked.connect(self.fit_all)

    # --- photos ---------------------------------------------------------

    def set_photos(self, rows: list, locations: dict[int, tuple[float, float]]) -> None:
        """rows: the photos shown (PhotoRow); locations: id → (lat, lon)
        for those that have one. Re-fits the map when a different set of
        photos is placed on it."""
        self._rows = {row.id: row for row in rows}
        self._total = len(rows)
        self._points = [(pid, lat, lon) for pid, (lat, lon) in locations.items() if pid in self._rows]
        ids = frozenset(pid for pid, _, _ in self._points)
        if ids != self._fitted_ids:
            self._fitted_ids = ids
            self.fit_all()
        else:
            self._recluster()

    def fit_all(self) -> None:
        if self._points:
            zoom, lat, lon = geo.fit([(lat, lon) for _, lat, lon in self._points], self.width(), self.height())
            self._set_view(zoom, geo.to_world(lat, lon, zoom))
        else:
            self._recluster()
            self.update()

    def show_location(self, lat: float, lon: float, zoom: int = 15) -> None:
        self._set_view(zoom, geo.to_world(lat, lon, zoom))

    def _set_view(self, zoom: int, centre: tuple[float, float]) -> None:
        self._zoom = max(geo.MIN_ZOOM, min(geo.MAX_ZOOM, zoom))
        self._centre = centre
        self._clamp()
        self._recluster()
        self.update()

    def _recluster(self) -> None:
        self._clusters = geo.cluster(self._points, self._zoom, cell=PIN + 10)

    # --- geometry -------------------------------------------------------

    def _view_centre(self) -> QPointF:
        return QPointF(self.width() / 2, self.height() / 2)

    def _clamp(self) -> None:
        size = geo.world_size(self._zoom)
        x, y = self._centre
        half = self.height() / 2
        y = min(max(y, half), size - half) if size > self.height() else size / 2
        self._centre = (x % size, y)

    def _to_screen(self, x: float, y: float) -> QPointF:
        size = geo.world_size(self._zoom)
        dx = (x - self._centre[0] + size / 2) % size - size / 2  # nearest copy round the world
        return QPointF(dx + self.width() / 2, y - self._centre[1] + self.height() / 2)

    def zoom_by(self, steps: int, anchor: QPointF) -> None:
        new_zoom = max(geo.MIN_ZOOM, min(geo.MAX_ZOOM, self._zoom + steps))
        if new_zoom == self._zoom:
            return
        # Keep the spot under the pointer where it is.
        wx = self._centre[0] + anchor.x() - self.width() / 2
        wy = self._centre[1] + anchor.y() - self.height() / 2
        scale = 2 ** (new_zoom - self._zoom)
        self._zoom = new_zoom
        self._centre = (wx * scale - (anchor.x() - self.width() / 2), wy * scale - (anchor.y() - self.height() / 2))
        self._clamp()
        self._recluster()
        self.update()

    def _cluster_at(self, pos: QPointF) -> geo.Cluster | None:
        for c in reversed(self._clusters):  # drawn last = on top
            centre = self._to_screen(c.x, c.y)
            if abs(pos.x() - centre.x()) <= PIN / 2 + 3 and abs(pos.y() - (centre.y() - PIN / 2)) <= PIN / 2 + 3:
                return c
        return None

    # --- tiles ----------------------------------------------------------

    def _tile(self, key: tuple[int, int, int]) -> QPixmap | None:
        pixmap = self._tiles.get(key)
        if pixmap is not None:
            self._tiles.move_to_end(key)
            return pixmap
        on_disk = geo.tile_path(*key, self._relay.cache_dir)
        if on_disk.exists():
            pixmap = QPixmap(str(on_disk))
            if not pixmap.isNull():
                self._remember(key, pixmap)
                return pixmap
        failed_at = self._failed.get(key)
        if key not in self._pending and (failed_at is None or time.monotonic() - failed_at > RETRY_FAILED_AFTER):
            self._pending.add(key)
            self._pool.start(_TileJob(self._relay, key))
        return None

    def _remember(self, key, pixmap: QPixmap) -> None:
        self._tiles[key] = pixmap
        while len(self._tiles) > TILE_MEMORY:
            self._tiles.popitem(last=False)

    @Slot(int, int, int, str)
    def _on_tile_loaded(self, zoom: int, x: int, y: int, path: str) -> None:
        key = (zoom, x, y)
        self._pending.discard(key)
        pixmap = QPixmap(path) if path else QPixmap()
        if pixmap.isNull():
            self._failed[key] = time.monotonic()
        else:
            self._failed.pop(key, None)
            self._remember(key, pixmap)
            if zoom == self._zoom:
                self.update()

    def shutdown(self) -> None:
        self._pool.clear()
        self._pool.waitForDone(3000)

    # --- drawing --------------------------------------------------------

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        painter.fillRect(self.rect(), LAND)

        # Ask for the tiles in view nearest the middle first.
        tiles = geo.tiles_for_view(*self._centre, self.width(), self.height(), self._zoom)
        middle = QPointF(self.width() / 2 - geo.TILE_SIZE / 2, self.height() / 2 - geo.TILE_SIZE / 2)
        tiles.sort(key=lambda t: (t[2] - middle.x()) ** 2 + (t[3] - middle.y()) ** 2)
        for tx, ty, sx, sy in tiles:
            pixmap = self._tile((self._zoom, tx, ty))
            if pixmap is not None:
                painter.drawPixmap(sx, sy, pixmap)
            else:
                painter.setPen(QPen(QColor(0, 0, 0, 18)))
                painter.drawRect(sx, sy, geo.TILE_SIZE, geo.TILE_SIZE)

        for c in sorted(self._clusters, key=lambda c: c.y):  # southern pins in front
            self._draw_pin(painter, c)

        self._draw_overlays(painter)

    def _draw_pin(self, painter: QPainter, c: geo.Cluster) -> None:
        tip = self._to_screen(c.x, c.y)
        if not QRectF(self.rect()).adjusted(-PIN, -PIN, PIN, PIN * 2).contains(tip):
            return
        box = QRectF(tip.x() - PIN / 2, tip.y() - PIN - 8, PIN, PIN)
        # The pointer under the thumbnail.
        pointer = QPainterPath()
        pointer.moveTo(tip)
        pointer.lineTo(tip.x() - 7, box.bottom() - 1)
        pointer.lineTo(tip.x() + 7, box.bottom() - 1)
        pointer.closeSubpath()
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(0, 0, 0, 60))
        painter.drawRoundedRect(box.translated(1.5, 2.5), 7, 7)
        painter.setBrush(Qt.white)
        painter.drawPath(pointer)
        painter.drawRoundedRect(box, 7, 7)

        inner = box.adjusted(3, 3, -3, -3)
        row = self._rows.get(c.ids[0])
        pixmap = self._thumbnail_for(row) if row is not None else None
        if pixmap is not None and not pixmap.isNull():
            # Fill the square, cropping the middle.
            scaled = pixmap.scaled(int(inner.width()), int(inner.height()), Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
            source = QRect(
                (scaled.width() - int(inner.width())) // 2, (scaled.height() - int(inner.height())) // 2,
                int(inner.width()), int(inner.height()),
            )
            clip = QPainterPath()
            clip.addRoundedRect(inner, 5, 5)
            painter.save()
            painter.setClipPath(clip)
            painter.drawPixmap(inner.toRect(), scaled, source)
            painter.restore()
        else:
            painter.setBrush(QColor("#ccc"))
            painter.drawRoundedRect(inner, 5, 5)

        if len(c.ids) > 1:
            text = f"{len(c.ids):,}" if len(c.ids) < 1000 else f"{len(c.ids) // 1000}k"
            font = QFont(self.font())
            font.setPixelSize(11)
            font.setBold(True)
            painter.setFont(font)
            width = max(20, painter.fontMetrics().horizontalAdvance(text) + 10)
            badge = QRectF(box.right() - width + 6, box.top() - 8, width, 20)
            painter.setBrush(ACCENT)
            painter.setPen(QPen(Qt.white, 2))
            painter.drawRoundedRect(badge, 10, 10)
            painter.setPen(Qt.white)
            painter.drawText(badge, Qt.AlignCenter, text)

    def _draw_overlays(self, painter: QPainter) -> None:
        font = QFont(self.font())
        font.setPixelSize(11)
        painter.setFont(font)
        # Credit OpenStreetMap, as its licence asks — clickable.
        metrics = painter.fontMetrics()
        text = geo.ATTRIBUTION
        self._credit_rect = QRect(0, 0, metrics.horizontalAdvance(text) + 12, metrics.height() + 6)
        self._credit_rect.moveBottomRight(self.rect().bottomRight())
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(255, 255, 255, 210))
        painter.drawRect(self._credit_rect)
        painter.setPen(QColor("#333"))
        painter.drawText(self._credit_rect, Qt.AlignCenter, text)

        # How many of the photos shown are on the map.
        located = len(self._points)
        if self._total and located < self._total:
            note = (
                f"{located:,} of {self._total:,} photos have a location"
                if located
                else "None of these photos has a location. Phones record where photos are taken; many cameras don't."
            )
            font.setPixelSize(12)
            painter.setFont(font)
            metrics = painter.fontMetrics()
            rect = QRect(12, 12, metrics.horizontalAdvance(note) + 20, metrics.height() + 10)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(255, 255, 255, 225))
            painter.drawRoundedRect(rect, 6, 6)
            painter.setPen(QColor("#333"))
            painter.drawText(rect, Qt.AlignCenter, note)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        right = self.width() - 12 - 34
        for i, button in enumerate((self._zoom_in, self._zoom_out, self._fit_button)):
            button.move(right, 12 + i * 40)
        self._clamp()

    # --- mouse ----------------------------------------------------------

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._drag_from = event.position().toPoint()
            self._dragged = False

    def mouseMoveEvent(self, event) -> None:
        pos = event.position()
        if self._drag_from is not None and event.buttons() & Qt.LeftButton:
            delta = pos.toPoint() - self._drag_from
            if self._dragged or delta.manhattanLength() > 3:
                self._dragged = True
                self.setCursor(Qt.ClosedHandCursor)
                self._centre = (self._centre[0] - delta.x(), self._centre[1] - delta.y())
                self._drag_from = pos.toPoint()
                self._clamp()
                self.update()
            return
        cluster = self._cluster_at(pos)
        self.setCursor(Qt.PointingHandCursor if cluster or self._over_credit(pos) else Qt.OpenHandCursor)
        if cluster is not None:
            QToolTip.showText(event.globalPosition().toPoint(), self._describe(cluster), self)
        else:
            QToolTip.hideText()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() != Qt.LeftButton:
            return
        was_drag = self._dragged
        self._drag_from = None
        self._dragged = False
        self.setCursor(Qt.OpenHandCursor)
        if was_drag:
            return
        pos = event.position()
        if self._over_credit(pos):
            QDesktopServices.openUrl(QUrl(geo.ATTRIBUTION_URL))
            return
        cluster = self._cluster_at(pos)
        if cluster is not None:
            self.pin_clicked.emit(list(cluster.ids))

    def mouseDoubleClickEvent(self, event) -> None:
        cluster = self._cluster_at(event.position())
        if cluster is not None:
            self.pin_activated.emit(list(cluster.ids))
        else:
            self.zoom_by(1, event.position())

    def wheelEvent(self, event) -> None:
        steps = event.angleDelta().y() // 120
        if steps:
            self.zoom_by(int(steps), event.position())

    def _over_credit(self, pos: QPointF) -> bool:
        return hasattr(self, "_credit_rect") and self._credit_rect.contains(pos.toPoint())

    def _describe(self, cluster: geo.Cluster) -> str:
        rows = [self._rows[i] for i in cluster.ids if i in self._rows]
        if len(rows) == 1:
            return f"{rows[0].filename}\n{rows[0].taken}\nClick to show it, double-click to open it"
        dates = sorted(row.taken[:10] for row in rows if row.taken)
        when = (dates[0] if dates[0] == dates[-1] else f"{dates[0]} – {dates[-1]}") if dates else ""
        return f"{len(rows):,} photos\n{when}\nClick to show them"
