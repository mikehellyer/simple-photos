"""A row of five clickable stars."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPalette
from PySide6.QtWidgets import QWidget

GOLD = QColor("#f2b01e")
STAR_SIZE = 20


def star_path(size: float) -> QPainterPath:
    import math

    path = QPainterPath()
    centre, outer, inner = size / 2, size / 2, size * 0.2
    for point in range(10):
        radius = outer if point % 2 == 0 else inner
        angle = math.radians(-90 + point * 36)
        position = QPointF(centre + radius * math.cos(angle), centre + radius * math.sin(angle) + size * 0.04)
        if point == 0:
            path.moveTo(position)
        else:
            path.lineTo(position)
    path.closeSubpath()
    return path


class StarRating(QWidget):
    """Click a star to rate; click the current rating's star again to clear
    it. A mixed selection (several photos with different ratings) shows no
    stars filled until one is clicked."""

    rating_changed = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rating = 0
        self._hover = 0
        self._path = star_path(STAR_SIZE)
        self.setMouseTracking(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Click to rate (keys 0–5 work too)")

    def sizeHint(self) -> QSize:
        return QSize(STAR_SIZE * 5 + 4 * 4, STAR_SIZE)

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def rating(self) -> int:
        return self._rating

    def set_rating(self, rating: int) -> None:
        self._rating = max(0, min(5, rating))
        self.update()

    def _star_at(self, x: float) -> int:
        return max(0, min(5, int(x // (STAR_SIZE + 4)) + 1))

    def mouseMoveEvent(self, event) -> None:
        self._hover = self._star_at(event.position().x()) if self.isEnabled() else 0
        self.update()

    def leaveEvent(self, event) -> None:
        self._hover = 0
        self.update()

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.LeftButton or not self.isEnabled():
            return
        star = self._star_at(event.position().x())
        self._rating = 0 if star == self._rating else star
        self.update()
        self.rating_changed.emit(self._rating)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        shown = self._hover or self._rating
        empty = self.palette().color(QPalette.Mid)
        for star in range(5):
            painter.save()
            painter.translate(star * (STAR_SIZE + 4), 0)
            filled = star < shown
            colour = GOLD.lighter(115) if filled and self._hover else GOLD
            painter.setPen(colour if filled else empty)
            painter.setBrush(colour if filled else Qt.NoBrush)
            painter.drawPath(self._path)
            painter.restore()
