"""Preferences: which folders make up the collection, and how imports work."""
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
    QLineEdit,
    QListWidget,
    QPushButton,
    QVBoxLayout,
)

from simple_photos.core.settings import SettingsStore


def normalise(folder: str) -> str:
    return os.path.normpath(os.path.abspath(os.path.expanduser(folder)))


def is_inside(folder: str, roots: list[str]) -> bool:
    folder = os.path.normcase(normalise(folder))
    for root in roots:
        root = os.path.normcase(normalise(root))
        if folder == root or folder.startswith(root.rstrip(os.sep) + os.sep):
            return True
    return False


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

        imports = QGroupBox("Importing")
        import_layout = QVBoxLayout(imports)
        import_layout.addWidget(QLabel("Put each import's new folder inside:"))
        folder_row = QHBoxLayout()
        self._import_folder = QLineEdit(s.import_folder)
        self._import_folder.textChanged.connect(self._update_buttons)
        folder_row.addWidget(self._import_folder, 1)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse_import_folder)
        folder_row.addWidget(browse)
        clear = QPushButton("Use Default")
        clear.clicked.connect(self._import_folder.clear)
        folder_row.addWidget(clear)
        import_layout.addLayout(folder_row)
        self._import_problem = QLabel()
        self._import_problem.setWordWrap(True)
        import_layout.addWidget(self._import_problem)
        self._import_verify = QCheckBox(
            "When skipping photos already in the library, check each one byte for byte first (slower)"
        )
        self._import_verify.setToolTip(
            "Without this, a photo counts as already imported when one with the same file size and date taken "
            "is in the library. With it, the two files are also compared, and only true copies are skipped."
        )
        self._import_verify.setChecked(s.import_verify)
        import_layout.addWidget(self._import_verify)
        layout.addWidget(imports)

        self._box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self._box.accepted.connect(self.accept)
        self._box.rejected.connect(self.reject)
        layout.addWidget(self._box)
        self._update_buttons()

    def folders(self) -> list[str]:
        return [self._folders.item(i).text() for i in range(self._folders.count())]

    def _add(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Add a Folder to the Collection")
        if folder:
            folder = normalise(folder)
            if folder not in self.folders():
                self._folders.addItem(folder)
        self._update_buttons()

    def _remove_selected(self) -> None:
        for item in self._folders.selectedItems():
            self._folders.takeItem(self._folders.row(item))
        self._update_buttons()

    def _browse_import_folder(self) -> None:
        start = self._import_folder.text() or (self.folders()[0] if self.folders() else "")
        folder = QFileDialog.getExistingDirectory(self, "Where Imports Go", start)
        if folder:
            self._import_folder.setText(normalise(folder))

    def _update_buttons(self) -> None:
        self._remove.setEnabled(bool(self._folders.selectedItems()))
        folder = self._import_folder.text().strip()
        folders = self.folders()
        if not folder:
            first = folders[0] if folders else "your first collection folder"
            self._import_folder.setPlaceholderText(f"Default: {first}")
            problem = ""
        elif not is_inside(folder, folders):
            # Outside the collection, imported photos wouldn't show up.
            problem = "Choose a folder inside one of your collection folders, so imported photos show up."
        else:
            problem = ""
        self._import_problem.setText(f"<span style='color:#c0392b'>{problem}</span>" if problem else "")
        self._import_problem.setVisible(bool(problem))
        self._box.button(QDialogButtonBox.Ok).setEnabled(not problem)

    def accept(self) -> None:
        folder = self._import_folder.text().strip()
        self._store.update(
            collection_folders=self.folders(),
            scan_on_startup=self._scan_on_startup.isChecked(),
            import_folder=normalise(folder) if folder else "",
            import_verify=self._import_verify.isChecked(),
        )
        super().accept()
