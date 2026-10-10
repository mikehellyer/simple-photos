"""Reviewing Auto-Rotate's suggestions before anything is changed."""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon, QPixmap, QTransform
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QListView,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
)

from simple_photos.core.library import PhotoRow

PREVIEW = 160
TURN_NAMES = {1: "turn right", 2: "turn upside down", 3: "turn left"}


class AutoRotateDialog(QDialog):
    """suggestions: (photo, quarter turns clockwise, thumbnail path or None)."""

    def __init__(self, suggestions: list[tuple[PhotoRow, int, str | None]], skipped: int, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Auto-Rotate")
        self.resize(760, 560)
        layout = QVBoxLayout(self)
        count = len(suggestions)
        intro = QLabel(
            f"These {count:,} photos look like they're on their side or upside down. They're shown here the way "
            "they'll look after rotating. Untick any that are already right." if count != 1 else
            "This photo looks like it's on its side or upside down. It's shown here the way it'll look after "
            "rotating. Untick it if it's already right."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)
        if skipped:
            note = QLabel(f"{skipped:,} more {'was' if skipped == 1 else 'were'} found in formats that can't be rotated without losing quality (WebP, HEIC).")
            note.setWordWrap(True)
            note.setStyleSheet("color: palette(mid);")
            layout.addWidget(note)

        self._list = QListWidget()
        self._list.setViewMode(QListView.IconMode)
        self._list.setIconSize(QSize(PREVIEW, PREVIEW))
        self._list.setGridSize(QSize(PREVIEW + 24, PREVIEW + 44))
        self._list.setResizeMode(QListView.Adjust)
        self._list.setMovement(QListView.Static)
        self._list.setWordWrap(True)
        self._list.itemChanged.connect(self._update_button)
        self._list.itemDoubleClicked.connect(
            lambda item: item.setCheckState(Qt.Unchecked if item.checkState() == Qt.Checked else Qt.Checked)
        )
        for photo, turns, thumb in suggestions:
            item = QListWidgetItem(f"{photo.filename}\n{TURN_NAMES[turns]}")
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked)
            item.setData(Qt.UserRole, (photo, turns))
            pixmap = QPixmap(thumb) if thumb else QPixmap()
            if not pixmap.isNull():
                item.setIcon(QIcon(pixmap.transformed(QTransform().rotate(90 * turns), Qt.SmoothTransformation)))
            self._list.addItem(item)
        layout.addWidget(self._list, 1)

        row = QHBoxLayout()
        for text, state in (("Tick All", Qt.Checked), ("Untick All", Qt.Unchecked)):
            button = QPushButton(text)
            button.clicked.connect(lambda _=False, state=state: self._set_all(state))
            row.addWidget(button)
        row.addStretch(1)
        self._buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)
        row.addWidget(self._buttons)
        layout.addLayout(row)
        self._update_button()

    def _set_all(self, state) -> None:
        for i in range(self._list.count()):
            self._list.item(i).setCheckState(state)

    def chosen(self) -> list[tuple[PhotoRow, int]]:
        items = (self._list.item(i) for i in range(self._list.count()))
        return [item.data(Qt.UserRole) for item in items if item.checkState() == Qt.Checked]

    def _update_button(self) -> None:
        count = len(self.chosen())
        ok = self._buttons.button(QDialogButtonBox.Ok)
        ok.setText(f"Rotate {count:,} Photo{'s' if count != 1 else ''}")
        ok.setEnabled(count > 0)
