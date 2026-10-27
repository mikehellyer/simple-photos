"""The full-size photo viewer that replaces the grid on double-click.

The photo opens fitted to the window. Scroll to zoom around the
pointer, drag to pan, double-click to swap between fitted and 100%.
While the full image decodes in the background, its thumbnail stands
in, so moving through photos never shows a blank screen.
"""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QImage, QKeySequence, QPainter, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QGraphicsView,
    QLabel,
    QToolBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from simple_photos.core.library import PhotoRow
from simple_photos.core import raw
from simple_photos.core.thumbnails import load_image
from simple_photos.gui.star_rating import StarRating
from simple_photos.gui.workers import BackgroundRunner

MAX_ZOOM = 16.0
MAX_DECODE_SIZE = 12000  # px, longest side — beyond this, panoramas are scaled
SLIDESHOW_INTERVAL_MS = 4000


def to_qimage(image) -> QImage:
    if image.mode != "RGBA":
        image = image.convert("RGBA")
    qimage = QImage(image.tobytes("raw", "RGBA"), image.width, image.height, image.width * 4, QImage.Format_RGBA8888)
    return qimage.copy()  # own the pixels; the bytes object above is temporary


def decode(path: str) -> QImage:
    """The photo for viewing — for a RAW, its embedded preview."""
    return to_qimage(load_image(path, MAX_DECODE_SIZE))


def develop_if_needed(path: str, shown_width: int, shown_height: int) -> QImage | None:
    """For a RAW whose preview is much smaller than the photo: the RAW
    developed in full (about a second). None if the preview will do."""
    if not raw.needs_developing(path, shown_width, shown_height):
        return None
    return to_qimage(raw.develop(path))


class ImageView(QGraphicsView):
    zoom_changed = Signal(float)
    double_clicked = Signal()
    step_requested = Signal(int)  # the mouse's back / forward buttons

    def __init__(self, parent=None):
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self._item = QGraphicsPixmapItem()
        self._item.setTransformationMode(Qt.SmoothTransformation)
        self._scene.addItem(self._item)
        self.setScene(self._scene)
        self.setFrameShape(QFrame.NoFrame)
        self.setBackgroundBrush(Qt.black)
        self.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorViewCenter)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._fitted = True
        self._upscale = False
        self._image_size = (0, 0)

    def set_pixmap(self, pixmap: QPixmap, stand_in: bool = False) -> None:
        """stand_in: a thumbnail shown while the real photo decodes, so
        let it be enlarged to fill the window as the photo will."""
        self._upscale = stand_in
        self._image_size = (pixmap.width(), pixmap.height())
        self._item.setPixmap(pixmap)
        self._scene.setSceneRect(QRectF(0, 0, *self._image_size))
        self.fit()

    def replace_pixmap(self, pixmap: QPixmap) -> None:
        """Swap in a sharper version of the same photo (a developed RAW)
        without the view jumping: same place, same apparent zoom."""
        if self._fitted or not self._image_size[0]:
            self.set_pixmap(pixmap)
            return
        old_width = self._image_size[0]
        centre = self.mapToScene(self.viewport().rect().center())
        relative = (centre.x() / old_width, centre.y() / self._image_size[1])
        zoom = self.current_zoom() * old_width / pixmap.width()
        self._upscale = False
        self._image_size = (pixmap.width(), pixmap.height())
        self._item.setPixmap(pixmap)
        self._scene.setSceneRect(QRectF(0, 0, *self._image_size))
        self.resetTransform()
        self.scale(zoom, zoom)
        self.centerOn(relative[0] * pixmap.width(), relative[1] * pixmap.height())
        self.zoom_changed.emit(zoom)

    def clear(self) -> None:
        self._item.setPixmap(QPixmap())

    def current_zoom(self) -> float:
        return self.transform().m11()

    def fit(self) -> None:
        self._fitted = True
        if not self._image_size[0]:
            return
        self.resetTransform()
        view = self.viewport().rect()
        scale = min(view.width() / self._image_size[0], view.height() / self._image_size[1])
        if not self._upscale:
            scale = min(scale, 1.0)
        self.scale(scale, scale)
        self.centerOn(self._image_size[0] / 2, self._image_size[1] / 2)
        self.zoom_changed.emit(scale)

    def set_zoom(self, zoom: float) -> None:
        zoom = max(0.02, min(MAX_ZOOM, zoom))
        self._fitted = False
        factor = zoom / self.current_zoom()
        self.scale(factor, factor)
        self.zoom_changed.emit(zoom)

    def zoom_by(self, factor: float) -> None:
        self.set_zoom(self.current_zoom() * factor)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._fitted:
            self.fit()

    def wheelEvent(self, event) -> None:
        steps = event.angleDelta().y() / 120
        if steps:
            self.zoom_by(1.25 ** steps)

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self.double_clicked.emit()

    def mousePressEvent(self, event) -> None:
        if event.button() in (Qt.BackButton, Qt.ForwardButton):
            self.step_requested.emit(-1 if event.button() == Qt.BackButton else 1)
            return
        super().mousePressEvent(event)


class _NavButton(QToolButton):
    """A round button floating over the photo: the big ‹ and › at its
    edges, and the smaller ✕ in its top-right corner."""

    SIZE = 56

    def __init__(self, text: str, tip: str, parent, size: int = SIZE, font_px: int = 34):
        super().__init__(parent)
        self.setText(text)
        self.setToolTip(tip)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self.setFixedSize(size, size)
        self.setStyleSheet(
            # Mid-grey so it shows over both dark and light photos.
            f"QToolButton {{ background: rgba(90, 90, 90, 140); color: white; border: none;"
            f" border-radius: {size // 2}px; font-size: {font_px}px; padding-bottom: {font_px // 9}px; }}"
            "QToolButton:hover { background: rgba(60, 60, 60, 210); }"
            "QToolButton:pressed { background: rgba(140, 140, 140, 210); }"
        )


class ViewerPage(QWidget):
    """Signals the window: close_requested (back to the grid),
    current_changed(row) when moving to another photo,
    rate_requested(rating), fullscreen_toggled."""

    close_requested = Signal()  # Esc: leaves full screen first, if it's on
    back_requested = Signal()  # the ✕: straight back to the grid
    move_requested = Signal()
    copy_requested = Signal()
    trash_requested = Signal()
    current_changed = Signal(int)
    position_changed = Signal(str)
    rate_requested = Signal(int)
    rotate_requested = Signal(int)  # quarter turns clockwise (-1 = anticlockwise)
    fullscreen_toggled = Signal()

    def __init__(self, runner: BackgroundRunner, parent=None):
        super().__init__(parent)
        self._runner = runner
        self._photos: list[PhotoRow] = []
        self._row = -1
        self._request = 0
        self._thumbnail_for = None  # callable(PhotoRow) -> QPixmap | None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self._toolbar = QToolBar()
        layout.addWidget(self._toolbar)
        self.view = ImageView()
        self.view.double_clicked.connect(self._toggle_fit)
        self.view.zoom_changed.connect(lambda z: self._zoom_label.setText(f"{z * 100:.0f}%"))
        self.view.step_requested.connect(self.step)
        layout.addWidget(self.view, 1)
        self._previous_button = _NavButton("‹", "Previous photo (←)", self)
        self._previous_button.clicked.connect(lambda: self.step(-1))
        self._next_button = _NavButton("›", "Next photo (→)", self)
        self._next_button.clicked.connect(lambda: self.step(1))
        self._close_button = _NavButton("✕", "Back to the grid (Esc)", self, size=44, font_px=22)
        self._close_button.clicked.connect(self.back_requested)

        def action(text, shortcuts, slot, tip=""):
            act = QAction(text, self)
            act.setShortcuts([QKeySequence(s) for s in shortcuts])
            # Anywhere in the window while the viewer is showing — not just
            # when the photo has focus, so ← → keep working after clicking
            # the info panel. (Text boxes still get their own arrow keys,
            # and a hidden viewer's shortcuts never fire.)
            act.setShortcutContext(Qt.WindowShortcut)
            act.setToolTip(f"{tip or text} ({QKeySequence(shortcuts[0]).toString(QKeySequence.NativeText)})" if shortcuts else text)
            act.triggered.connect(slot)
            self.addAction(act)
            return act

        self._toolbar.addAction(action("◀ Grid", ["Esc"], self.close_requested.emit, "Back to the grid"))
        self._toolbar.addSeparator()
        action("Previous", ["Left", "PgUp", "Backspace"], lambda: self.step(-1))
        action("Next", ["Right", "PgDown", "Space"], lambda: self.step(1))
        action("First", ["Home"], lambda: self.step(-len(self._photos)))
        action("Last", ["End"], lambda: self.step(len(self._photos)))
        self._toolbar.addAction(action("Fit", ["F"], self.view.fit, "Fit to window"))
        self._toolbar.addAction(action("100%", ["Z"], lambda: self.view.set_zoom(1.0), "Actual size"))
        self._toolbar.addAction(action("−", ["-"], lambda: self.view.zoom_by(0.8), "Zoom out"))
        self._toolbar.addAction(action("+", ["+", "="], lambda: self.view.zoom_by(1.25), "Zoom in"))
        self._zoom_label = QLabel()
        self._zoom_label.setMinimumWidth(48)
        self._zoom_label.setAlignment(Qt.AlignCenter)
        self._toolbar.addWidget(self._zoom_label)
        self._toolbar.addSeparator()
        self._slideshow_action = action("▶ Slideshow", ["S"], self.toggle_slideshow)
        self._slideshow_action.setCheckable(True)
        self._toolbar.addAction(self._slideshow_action)
        self._toolbar.addAction(action("Full Screen", ["F11"], self.fullscreen_toggled.emit))
        self._toolbar.addSeparator()
        for text, turns, tip in (("⟲", -1, "Rotate left (Ctrl+L)"), ("⟳", 1, "Rotate right (Ctrl+R)")):
            rotate = QAction(text, self)
            rotate.setToolTip(tip)
            rotate.triggered.connect(lambda _=False, turns=turns: self.rotate_requested.emit(turns))
            self._toolbar.addAction(rotate)
        self._toolbar.addSeparator()
        # Their shortcuts (Ctrl+Shift+M/C, Delete) belong to the window's Photo menu.
        for text, signal, tip in (
            ("Move…", self.move_requested, "Move this photo to another folder (Ctrl+Shift+M)"),
            ("Copy…", self.copy_requested, "Copy this photo to another folder (Ctrl+Shift+C)"),
            ("Delete", self.trash_requested, "Move this photo to the Trash (Delete)"),
        ):
            button = QAction(text, self)
            button.setToolTip(tip)
            button.triggered.connect(signal)
            self._toolbar.addAction(button)
        self._toolbar.addSeparator()
        self._stars = StarRating()
        self._stars.rating_changed.connect(self.rate_requested)
        self._toolbar.addWidget(self._stars)
        for rating in range(6):
            action(str(rating), [str(rating)], lambda _=False, r=rating: self.rate_requested.emit(r))

        self._slideshow = QTimer(self)
        self._slideshow.setInterval(SLIDESHOW_INTERVAL_MS)
        self._slideshow.timeout.connect(self._slideshow_step)

    def set_thumbnail_source(self, source) -> None:
        self._thumbnail_for = source

    def set_toolbar_visible(self, visible: bool) -> None:
        self._toolbar.setVisible(visible)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._place_nav_buttons()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._place_nav_buttons()

    def _place_nav_buttons(self) -> None:
        area = self.view.geometry()
        y = area.center().y() - _NavButton.SIZE // 2
        self._previous_button.move(area.left() + 16, y)
        self._next_button.move(area.right() - 16 - _NavButton.SIZE, y)
        self._close_button.move(area.right() - 16 - self._close_button.width(), area.top() + 16)
        for button in (self._previous_button, self._next_button, self._close_button):
            button.raise_()

    def _update_nav_buttons(self) -> None:
        self._previous_button.setVisible(self._row > 0)
        self._next_button.setVisible(0 <= self._row < len(self._photos) - 1)

    # --- which photo ----------------------------------------------------

    def show_photos(self, photos: list[PhotoRow], row: int) -> None:
        self._photos = photos
        self._show(row)

    def reload_current(self) -> None:
        """Decode the current photo again (it was rotated)."""
        if self.current_photo() is not None:
            self._show(self._row)

    def current_row(self) -> int:
        return self._row

    def current_photo(self) -> PhotoRow | None:
        return self._photos[self._row] if 0 <= self._row < len(self._photos) else None

    def step(self, delta: int) -> None:
        if self._photos:
            row = max(0, min(len(self._photos) - 1, self._row + delta))
            if row != self._row:
                self._show(row)
                self.current_changed.emit(row)

    def refresh_rating(self) -> None:
        photo = self.current_photo()
        self._stars.set_rating(photo.rating if photo else 0)

    def _show(self, row: int) -> None:
        self._row = row
        self._update_nav_buttons()
        photo = self.current_photo()
        if photo is None:
            self.view.clear()
            return
        self.position_changed.emit(f"{photo.filename} · {row + 1:,} of {len(self._photos):,}")
        self.refresh_rating()
        self._request += 1
        request = self._request
        stand_in = self._thumbnail_for(photo) if self._thumbnail_for else None
        if stand_in is not None and not stand_in.isNull():
            self.view.set_pixmap(stand_in, stand_in=True)
        else:
            self.view.clear()
        self._runner.run(lambda path=photo.path: decode(path), lambda image: self._on_decoded(request, image), lambda error: self._on_failed(request, error))

    def _on_decoded(self, request: int, image: QImage) -> None:
        if request != self._request:
            return
        self.view.set_pixmap(QPixmap.fromImage(image))
        photo = self.current_photo()
        if photo is not None and raw.is_raw(photo.path):
            # Shown already from its preview; develop it if that's small.
            self._runner.run(
                lambda path=photo.path, w=image.width(), h=image.height(): develop_if_needed(path, w, h),
                lambda developed: self._on_developed(request, developed),
                lambda error: None,  # the preview stays: still a fine view
            )

    def _on_developed(self, request: int, image: QImage | None) -> None:
        if request == self._request and image is not None:
            self.view.replace_pixmap(QPixmap.fromImage(image))

    def _on_failed(self, request: int, error: Exception) -> None:
        if request == self._request:
            photo = self.current_photo()
            self.position_changed.emit(f"Couldn't open {photo.filename if photo else 'this photo'}: {error}")

    # --- slideshow ------------------------------------------------------

    def toggle_slideshow(self) -> None:
        running = not self._slideshow.isActive()
        self._slideshow.start() if running else self._slideshow.stop()
        self._slideshow_action.setChecked(running)
        self._slideshow_action.setText("■ Stop Slideshow" if running else "▶ Slideshow")

    def stop_slideshow(self) -> None:
        if self._slideshow.isActive():
            self.toggle_slideshow()

    def _slideshow_step(self) -> None:
        if self._row >= len(self._photos) - 1:
            self.stop_slideshow()
        else:
            self.step(1)

    def _toggle_fit(self) -> None:
        if self.view._fitted:
            self.view.set_zoom(1.0)
        else:
            self.view.fit()
