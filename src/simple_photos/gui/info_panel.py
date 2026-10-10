"""The right-hand panel: what's known about the selected photo(s), and
where ratings, tags and captions are edited. Edits apply to every
selected photo at once."""
from __future__ import annotations

import html
import os

from PySide6.QtCore import QStringListModel, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QCompleter,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from simple_photos.core.library import Library
from simple_photos.core.metadata import (
    format_aperture,
    format_camera,
    format_exposure,
    format_focal_length,
    format_size,
    megapixels,
)
from simple_photos.gui.star_rating import StarRating

CAPTION_SAVE_DELAY_MS = 700


def _heading(text: str) -> QLabel:
    label = QLabel(text.upper())
    label.setStyleSheet("font-weight: 600; font-size: 11px; color: palette(mid); padding-top: 10px;")
    return label


def _pretty_date(taken: str) -> str:
    from datetime import datetime

    try:
        moment = datetime.strptime(taken, "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return taken or ""
    return moment.strftime("%A %-d %B %Y, %H:%M" if os.name != "nt" else "%A %#d %B %Y, %H:%M")


class InfoPanel(QScrollArea):
    rating_changed = Signal(list, int)  # photo ids, rating
    tags_changed = Signal()

    def __init__(self, library: Library, parent=None):
        super().__init__(parent)
        self._library = library
        self._ids: list[int] = []
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setMinimumWidth(260)

        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(12, 8, 12, 12)
        self.setWidget(body)

        self._title = QLabel()
        self._title.setWordWrap(True)
        self._title.setStyleSheet("font-weight: 600; font-size: 14px;")
        self._title.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self._title)

        layout.addWidget(_heading("Rating"))
        self._stars = StarRating()
        self._stars.rating_changed.connect(lambda rating: self.rating_changed.emit(list(self._ids), rating))
        layout.addWidget(self._stars)

        layout.addWidget(_heading("Tags"))
        self._tag_list = QListWidget()
        self._tag_list.setFlow(QListWidget.LeftToRight)
        self._tag_list.setWrapping(True)
        self._tag_list.setResizeMode(QListWidget.Adjust)
        self._tag_list.setSpacing(3)
        self._tag_list.setMaximumHeight(90)
        self._tag_list.setToolTip("Select a tag and press Delete (or click Remove) to take it off these photos")
        self._tag_list.itemSelectionChanged.connect(self._update_remove_button)
        for key in (QKeySequence.Delete, QKeySequence(Qt.Key_Backspace)):
            QShortcut(key, self._tag_list, self._remove_selected_tags, context=Qt.WidgetShortcut)
        layout.addWidget(self._tag_list)
        tag_row = QHBoxLayout()
        self._tag_entry = QLineEdit()
        self._tag_entry.setPlaceholderText("Add a tag…")
        self._tag_entry.returnPressed.connect(self._add_tag)
        self._completions = QStringListModel(self)
        completer = QCompleter(self._completions, self)
        completer.setCaseSensitivity(Qt.CaseInsensitive)
        completer.setFilterMode(Qt.MatchContains)
        self._tag_entry.setCompleter(completer)
        tag_row.addWidget(self._tag_entry, 1)
        self._remove_tag_button = QPushButton("Remove")
        self._remove_tag_button.clicked.connect(self._remove_selected_tags)
        tag_row.addWidget(self._remove_tag_button)
        layout.addLayout(tag_row)

        layout.addWidget(_heading("Caption"))
        self._caption = QPlainTextEdit()
        self._caption.setPlaceholderText("Add a caption…")
        self._caption.setMaximumHeight(80)
        self._caption.textChanged.connect(self._caption_edited)
        self._caption_dirty = False
        self._caption_timer = QTimer(self)
        self._caption_timer.setSingleShot(True)
        self._caption_timer.setInterval(CAPTION_SAVE_DELAY_MS)
        self._caption_timer.timeout.connect(self.save_caption)
        layout.addWidget(self._caption)

        self._details_heading = _heading("Details")
        layout.addWidget(self._details_heading)
        self._details = QFormLayout()
        self._details.setLabelAlignment(Qt.AlignLeft)
        self._details.setHorizontalSpacing(10)
        layout.addLayout(self._details)
        layout.addStretch(1)

        self._edit_widgets = (self._stars, self._tag_list, self._tag_entry, self._caption)
        self.show_photos([])

    # --- showing --------------------------------------------------------

    def show_photos(self, photo_ids: list[int]) -> None:
        self.save_caption()
        self._ids = list(photo_ids)
        for widget in self._edit_widgets:
            widget.setEnabled(bool(self._ids))
        self._refresh_tags()
        self._clear_details()

        if not self._ids:
            self._title.setText("No photo selected")
            self._stars.set_rating(0)
            self._set_caption("")
            self._details_heading.hide()
            return

        photos = [p for p in (self._library.photo(i) for i in self._ids[:500]) if p is not None]
        if not photos:
            return
        ratings = {p.rating for p in photos}
        self._stars.set_rating(ratings.pop() if len(ratings) == 1 else 0)
        captions = {p.caption for p in photos}
        if len(captions) == 1:
            self._set_caption(captions.pop())
            self._caption.setPlaceholderText("Add a caption…")
        else:
            self._set_caption("")
            self._caption.setPlaceholderText("Several different captions — type to replace them all")

        if len(self._ids) > 1:
            self._title.setText(f"{len(self._ids):,} photos selected")
            self._details_heading.hide()
            return

        photo = photos[0]
        self._title.setText(photo.filename)
        self._details_heading.show()
        home = os.path.expanduser("~")
        shown_folder = "~" + photo.folder[len(home):] if photo.folder.startswith(home + os.sep) else photo.folder
        folder_link = f'<a href="{QUrl.fromLocalFile(photo.folder).toString()}">{html.escape(shown_folder)}</a>'
        rows = [
            ("Taken", _pretty_date(photo.taken)),
            ("Size", " · ".join(x for x in (f"{photo.width} × {photo.height}" if photo.width else "", megapixels(photo.width, photo.height), format_size(photo.file_size)) if x)),
            ("Camera", format_camera(photo.camera_make, photo.camera_model)),
            ("Lens", photo.lens),
            ("Exposure", " · ".join(x for x in (format_exposure(photo.exposure_time), format_aperture(photo.f_number), f"ISO {photo.iso}" if photo.iso else "", format_focal_length(photo.focal_length)) if x)),
        ]
        for label, value in rows:
            if value:
                self._add_detail(label, html.escape(value))
        if photo.gps_lat is not None and photo.gps_lon is not None:
            lat, lon = photo.gps_lat, photo.gps_lon
            url = f"https://www.openstreetmap.org/?mlat={lat}&mlon={lon}#map=15/{lat}/{lon}"
            self._add_detail("Location", f'<a href="{url}">{lat:.5f}, {lon:.5f}</a>')
        self._add_detail("Folder", folder_link)

    def _add_detail(self, label: str, rich_text: str) -> None:
        value = QLabel(rich_text)
        value.setWordWrap(True)
        value.setTextFormat(Qt.RichText)
        value.setTextInteractionFlags(Qt.TextBrowserInteraction)
        value.linkActivated.connect(lambda link: QDesktopServices.openUrl(QUrl(link)))
        name = QLabel(label)
        name.setStyleSheet("color: palette(mid);")
        self._details.addRow(name, value)

    def _clear_details(self) -> None:
        while self._details.rowCount():
            self._details.removeRow(0)

    def set_rating_display(self, rating: int) -> None:
        self._stars.set_rating(rating)

    # --- tags -----------------------------------------------------------

    def _refresh_tags(self) -> None:
        self._completions.setStringList(self._library.all_tag_names())
        self._tag_list.clear()
        if not self._ids:
            self._update_remove_button()
            return
        total = len(self._ids)
        for name, count in self._library.tags_for(self._ids).items():
            item = QListWidgetItem(name if count == total else f"{name} ({count}/{total})")
            item.setData(Qt.UserRole, name)
            if count != total:
                item.setToolTip(f"{count} of the {total} selected photos have this tag")
            self._tag_list.addItem(item)
        self._update_remove_button()

    def _update_remove_button(self) -> None:
        self._remove_tag_button.setEnabled(bool(self._tag_list.selectedItems()))

    def _add_tag(self) -> None:
        names = [n.strip() for n in self._tag_entry.text().split(",") if n.strip()]
        if not names or not self._ids:
            return
        for name in names:
            self._library.add_tag(self._ids, name)
        self._tag_entry.clear()
        self._refresh_tags()
        self.tags_changed.emit()

    def _remove_selected_tags(self) -> None:
        names = [item.data(Qt.UserRole) for item in self._tag_list.selectedItems()]
        for name in names:
            self._library.remove_tag(self._ids, name)
        if names:
            self._refresh_tags()
            self.tags_changed.emit()

    def focus_tag_entry(self) -> None:
        if self._ids:
            self._tag_entry.setFocus()

    # --- caption --------------------------------------------------------

    def _set_caption(self, text: str) -> None:
        self._caption.blockSignals(True)
        self._caption.setPlainText(text)
        self._caption.blockSignals(False)
        self._caption_dirty = False

    def _caption_edited(self) -> None:
        self._caption_dirty = True
        self._caption_timer.start()

    def save_caption(self) -> None:
        self._caption_timer.stop()
        if self._caption_dirty and self._ids:
            self._library.set_caption(self._ids, self._caption.toPlainText())
        self._caption_dirty = False
