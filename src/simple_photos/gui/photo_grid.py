"""The thumbnail grid: a list model of photos, a background thumbnail
loader with a small in-memory cache in front of the on-disk one, and the
delegate that paints each tile."""
from __future__ import annotations

from collections import OrderedDict

from PySide6.QtCore import (
    QAbstractListModel,
    QMimeData,
    QModelIndex,
    QObject,
    QRect,
    QRunnable,
    QSize,
    Qt,
    QThreadPool,
    QUrl,
    Signal,
    Slot,
)
from PySide6.QtGui import QColor, QImage, QPainter, QPalette, QPixmap
from PySide6.QtWidgets import QAbstractItemView, QListView, QStyle, QStyledItemDelegate

from simple_photos.core.library import PhotoRow
from simple_photos.core.thumbnails import ensure_thumbnail, thumbnail_cache_dir
from simple_photos.gui.star_rating import GOLD

PhotoRole = Qt.UserRole + 1
PHOTO_IDS_MIME = "application/x-simple-photos-ids"  # comma-separated photo ids
MEMORY_CACHE_SIZE = 400  # thumbnails kept decoded; up to ~600 KB each


class _ThumbnailJob(QRunnable):
    def __init__(self, loader: "ThumbnailLoader", photo_id: int, path: str, mtime: float, generation: int):
        super().__init__()
        self._loader = loader
        self._args = (photo_id, path, mtime, generation)

    def run(self) -> None:
        photo_id, path, mtime, generation = self._args
        if generation != self._loader.generation:
            return  # the grid moved on before this job started
        thumb = ensure_thumbnail(path, mtime, self._loader.cache_dir)
        image = QImage(str(thumb)) if thumb else QImage()
        self._loader.loaded.emit(photo_id, generation, image)


class ThumbnailLoader(QObject):
    """Makes/reads thumbnails on a thread pool. `loaded` is emitted from a
    pool thread, so it's always connected queued to a slot on the GUI
    thread; QImage (unlike QPixmap) is safe to build off it."""

    loaded = Signal(int, int, QImage)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.cache_dir = thumbnail_cache_dir()
        self.generation = 0
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(max(2, QThreadPool.globalInstance().maxThreadCount() - 1))

    def request(self, row: PhotoRow) -> None:
        self._pool.start(_ThumbnailJob(self, row.id, row.path, row.file_mtime, self.generation))

    def cancel_pending(self) -> None:
        self.generation += 1
        self._pool.clear()

    def shutdown(self, timeout_ms: int = 3000) -> None:
        self.cancel_pending()
        self._pool.waitForDone(timeout_ms)


class PhotoModel(QAbstractListModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: list[PhotoRow] = []
        self._row_of: dict[int, int] = {}
        self._pixmaps: OrderedDict[tuple[int, float], QPixmap] = OrderedDict()
        self._pending: set[int] = set()
        self._failed: set[tuple[int, float]] = set()
        self.loader = ThumbnailLoader(self)
        self.loader.loaded.connect(self._on_loaded, Qt.QueuedConnection)

    # --- contents -------------------------------------------------------

    def set_photos(self, rows: list[PhotoRow]) -> None:
        self.loader.cancel_pending()
        self._pending.clear()
        self.beginResetModel()
        self._rows = rows
        self._row_of = {row.id: i for i, row in enumerate(rows)}
        self.endResetModel()

    def photo_at(self, row: int) -> PhotoRow | None:
        return self._rows[row] if 0 <= row < len(self._rows) else None

    def row_of(self, photo_id: int) -> int | None:
        return self._row_of.get(photo_id)

    def photos(self) -> list[PhotoRow]:
        return self._rows

    def set_rating(self, photo_ids, rating: int) -> None:
        for photo_id in photo_ids:
            row = self._row_of.get(photo_id)
            if row is not None:
                self._rows[row].rating = rating
                index = self.index(row)
                self.dataChanged.emit(index, index, [PhotoRole])

    def cached_pixmap(self, row: PhotoRow) -> QPixmap | None:
        return self._pixmaps.get((row.id, row.file_mtime))

    # --- Qt model API ---------------------------------------------------

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._rows)

    def data(self, index: QModelIndex, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        row = self._rows[index.row()]
        if role == Qt.DisplayRole:
            return row.filename
        if role == PhotoRole:
            return row
        if role == Qt.DecorationRole:
            key = (row.id, row.file_mtime)
            pixmap = self._pixmaps.get(key)
            if pixmap is not None:
                self._pixmaps.move_to_end(key)
                return pixmap
            if key not in self._failed and row.id not in self._pending:
                self._pending.add(row.id)
                self.loader.request(row)
            return None
        if role == Qt.ToolTipRole:
            return f"{row.filename}\n{row.taken}"
        return None

    def flags(self, index: QModelIndex):
        flags = super().flags(index)
        return flags | Qt.ItemIsDragEnabled if index.isValid() else flags

    def mimeTypes(self) -> list[str]:
        return [PHOTO_IDS_MIME, "text/uri-list"]

    def supportedDragActions(self):
        # Copy only, as far as other apps are concerned: a file manager
        # moving a photo would leave the library pointing at nothing. A
        # drop on a sidebar folder does Simple-Photos' own move instead.
        return Qt.CopyAction

    def mimeData(self, indexes) -> QMimeData:
        """Photo ids for dropping on a folder in the sidebar, plus file
        URLs, so photos can also be dragged out to a file manager or
        another app."""
        rows = [self._rows[i.row()] for i in sorted(indexes, key=lambda i: i.row()) if i.isValid()]
        data = QMimeData()
        data.setData(PHOTO_IDS_MIME, ",".join(str(row.id) for row in rows).encode())
        data.setUrls([QUrl.fromLocalFile(row.path) for row in rows])
        return data

    @Slot(int, int, QImage)
    def _on_loaded(self, photo_id: int, generation: int, image: QImage) -> None:
        self._pending.discard(photo_id)
        row = self._row_of.get(photo_id)
        if row is None:
            return
        key = (photo_id, self._rows[row].file_mtime)
        if image.isNull():
            self._failed.add(key)
        else:
            self._pixmaps[key] = QPixmap.fromImage(image)
            while len(self._pixmaps) > MEMORY_CACHE_SIZE:
                self._pixmaps.popitem(last=False)
        index = self.index(row)
        self.dataChanged.emit(index, index, [Qt.DecorationRole])


class PhotoDelegate(QStyledItemDelegate):
    PADDING = 8

    def __init__(self, parent=None):
        super().__init__(parent)
        self.thumb_size = 160

    def sizeHint(self, option, index) -> QSize:
        line = option.fontMetrics.height()
        return QSize(self.thumb_size + self.PADDING * 2, self.thumb_size + self.PADDING * 2 + line * 2 + 2)

    def paint(self, painter: QPainter, option, index) -> None:
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        rect = option.rect.adjusted(2, 2, -2, -2)
        palette = option.palette
        selected = option.state & QStyle.State_Selected
        if selected:
            highlight = QColor(palette.color(QPalette.Highlight))
            painter.setPen(Qt.NoPen)
            painter.setBrush(highlight)
            painter.drawRoundedRect(rect, 6, 6)
        elif option.state & QStyle.State_MouseOver:
            hover = QColor(palette.color(QPalette.Highlight))
            hover.setAlpha(40)
            painter.setPen(Qt.NoPen)
            painter.setBrush(hover)
            painter.drawRoundedRect(rect, 6, 6)

        size = self.thumb_size
        image_box = QRect(rect.x() + self.PADDING - 2, rect.y() + self.PADDING - 2, size, size)
        pixmap = index.data(Qt.DecorationRole)
        if isinstance(pixmap, QPixmap) and not pixmap.isNull():
            scaled = pixmap.size().scaled(size, size, Qt.KeepAspectRatio)
            target = QRect(0, 0, scaled.width(), scaled.height())
            target.moveCenter(image_box.center())
            painter.drawPixmap(target, pixmap)
        else:
            placeholder = QColor(palette.color(QPalette.Mid))
            placeholder.setAlpha(60)
            painter.setPen(Qt.NoPen)
            painter.setBrush(placeholder)
            painter.drawRoundedRect(image_box.adjusted(size // 8, size // 8, -size // 8, -size // 8), 4, 4)

        text_colour = palette.color(QPalette.HighlightedText if selected else QPalette.Text)
        metrics = option.fontMetrics
        line = metrics.height()
        text_rect = QRect(rect.x() + 4, image_box.bottom() + 4, rect.width() - 8, line)
        painter.setPen(text_colour)
        painter.drawText(text_rect, Qt.AlignHCenter | Qt.AlignVCenter, metrics.elidedText(index.data(), Qt.ElideMiddle, text_rect.width()))

        photo = index.data(PhotoRole)
        if photo is not None and photo.rating:
            painter.setPen(text_colour if selected else GOLD)
            painter.drawText(text_rect.translated(0, line), Qt.AlignHCenter | Qt.AlignVCenter, "★" * photo.rating)
        painter.restore()


class PhotoGrid(QListView):
    """Keys: Enter opens the viewer; 0–5 rate the selection."""

    activated_photo = Signal(int)  # row
    rate_requested = Signal(int)

    def __init__(self, model: PhotoModel, parent=None):
        super().__init__(parent)
        self.setModel(model)
        self._delegate = PhotoDelegate(self)
        self.setItemDelegate(self._delegate)
        self.setViewMode(QListView.IconMode)
        self.setResizeMode(QListView.Adjust)
        self.setMovement(QListView.Static)
        self.setUniformItemSizes(True)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragOnly)
        self.setDefaultDropAction(Qt.CopyAction)
        self.setMouseTracking(True)
        self.setSpacing(2)
        self.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.verticalScrollBar().setSingleStep(24)
        self.doubleClicked.connect(lambda index: self.activated_photo.emit(index.row()))
        self.empty_text = ""

    def set_thumb_size(self, size: int) -> None:
        self._delegate.thumb_size = size
        # Uniform item sizes are cached — make the view re-ask for them.
        self.setUniformItemSizes(False)
        self.setUniformItemSizes(True)
        self.scheduleDelayedItemsLayout()
        current = self.currentIndex()
        if current.isValid():
            self.scrollTo(current, QAbstractItemView.PositionAtCenter)

    def keyPressEvent(self, event) -> None:
        key = event.key()
        if key in (Qt.Key_Return, Qt.Key_Enter) and self.currentIndex().isValid():
            self.activated_photo.emit(self.currentIndex().row())
            return
        if Qt.Key_0 <= key <= Qt.Key_5 and not (event.modifiers() & (Qt.AltModifier | Qt.MetaModifier)):
            self.rate_requested.emit(key - Qt.Key_0)
            return
        super().keyPressEvent(event)

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if self.model().rowCount() == 0 and self.empty_text:
            painter = QPainter(self.viewport())
            painter.setPen(self.palette().color(QPalette.PlaceholderText))
            painter.drawText(self.viewport().rect().adjusted(40, 40, -40, -40), Qt.AlignCenter | Qt.TextWordWrap, self.empty_text)
