"""Preferences: which folders make up the collection."""
from __future__ import annotations

import os

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPushButton,
    QVBoxLayout,
)

from simple_photos.core.settings import SettingsStore


def normalise(folder: str) -> str:
    return os.path.normpath(os.path.abspath(os.path.expanduser(folder)))


class SettingsDialog(QDialog):
    def __init__(self, settings: SettingsStore, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumWidth(560)
        self._store = settings
        s = settings.settings
        layout = QVBoxLayout(self)

        collection = QGroupBox("Collection folders")
        group_layout = QVBoxLayout(collection)
        explain = QLabel(
            "Simple-Photos shows every photo in these folders and all the folders inside them. "
            "Your files stay where they are. Ratings, tags and captions are kept in Simple-Photos' own library."
        )
        explain.setWordWrap(True)
        group_layout.addWidget(explain)
        self._folders = QListWidget()
        self._folders.addItems(s.collection_folders)
        self._folders.itemSelectionChanged.connect(self._update_buttons)
        group_layout.addWidget(self._folders)
        buttons_row = QHBoxLayout()
        add = QPushButton("Add Folder…")
        add.clicked.connect(self._add)
        self._remove = QPushButton("Remove")
        self._remove.clicked.connect(self._remove_selected)
        buttons_row.addWidget(add)
        buttons_row.addWidget(self._remove)
        buttons_row.addStretch(1)
        group_layout.addLayout(buttons_row)
        layout.addWidget(collection)

        self._scan_on_startup = QCheckBox("Look for new and changed photos every time Simple-Photos starts")
        self._scan_on_startup.setChecked(s.scan_on_startup)
        layout.addWidget(self._scan_on_startup)

        box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        layout.addWidget(box)
        self._update_buttons()

    def folders(self) -> list[str]:
        return [self._folders.item(i).text() for i in range(self._folders.count())]

    def _add(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Add a Folder to the Collection")
        if folder:
            folder = normalise(folder)
            if folder not in self.folders():
                self._folders.addItem(folder)

    def _remove_selected(self) -> None:
        for item in self._folders.selectedItems():
            self._folders.takeItem(self._folders.row(item))

    def _update_buttons(self) -> None:
        self._remove.setEnabled(bool(self._folders.selectedItems()))

    def accept(self) -> None:
        self._store.update(collection_folders=self.folders(), scan_on_startup=self._scan_on_startup.isChecked())
        super().accept()
