"""Dialogs for making a new folder and for choosing where to move photos."""
from __future__ import annotations

import os
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTreeWidget,
    QVBoxLayout,
)

from simple_photos.core.fileops import create_folder, folder_name_problem
from simple_photos.core.library import Library
from simple_photos.gui.sidebar import ScopeRole, add_folder_items, select_folder_item


def short_path(path: str) -> str:
    home = os.path.expanduser("~")
    return "~" + path[len(home):] if path.startswith(home + os.sep) else path


class NewFolderDialog(QDialog):
    def __init__(self, parent_folder: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("New Folder")
        self.setMinimumWidth(420)
        self._parent_folder = parent_folder
        layout = QVBoxLayout(self)
        where = QLabel(f"Inside <b>{short_path(parent_folder)}</b>")
        where.setTextFormat(Qt.RichText)
        layout.addWidget(where)
        self._name = QLineEdit()
        self._name.setPlaceholderText("Folder name")
        self._name.textChanged.connect(self._validate)
        layout.addWidget(self._name)
        self._problem = QLabel()
        self._problem.setStyleSheet("color: #c0392b;")
        layout.addWidget(self._problem)
        self._buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self._buttons.button(QDialogButtonBox.Ok).setText("Create")
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)
        layout.addWidget(self._buttons)
        self._validate()

    def name(self) -> str:
        return self._name.text()

    def _validate(self) -> None:
        name = self._name.text()
        problem = folder_name_problem(name) if name else None
        if not problem and name and os.path.lexists(os.path.join(self._parent_folder, name)):
            problem = f"There's already something called “{name}” here."
        self._problem.setText(problem or "")
        self._buttons.button(QDialogButtonBox.Ok).setEnabled(bool(name) and problem is None)


def make_new_folder(library: Library, parent_folder: str, widget) -> Optional[str]:
    """Ask for a name and make the folder. Its path, or None if cancelled
    or it couldn't be made (the user has already been told why)."""
    dialog = NewFolderDialog(parent_folder, widget)
    if not dialog.exec():
        return None
    try:
        path = create_folder(parent_folder, dialog.name())
    except (ValueError, OSError) as error:
        message = str(error) if isinstance(error, ValueError) else (error.strerror or str(error))
        QMessageBox.warning(widget, "New Folder", f"Couldn't make the folder: {message}")
        return None
    library.add_folder(path)
    return path


class MoveToFolderDialog(QDialog):
    last_destination: Optional[str] = None  # preselected next time

    def __init__(self, library: Library, roots: list[str], count: int, current_folder: Optional[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Move to Folder")
        self.resize(460, 520)
        self._library = library
        self._roots = roots
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"Move {'the photo' if count == 1 else f'{count:,} photos'} to:"))
        self._tree = QTreeWidget()
        self._tree.setHeaderHidden(True)
        self._tree.setColumnCount(2)
        self._tree.setUniformRowHeights(True)
        self._tree.itemSelectionChanged.connect(self._update_buttons)
        self._tree.itemDoubleClicked.connect(lambda *_: self._move_button.isEnabled() and self.accept())
        layout.addWidget(self._tree, 1)

        self._new_button = QPushButton("New Folder…")
        self._new_button.clicked.connect(self._new_folder)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.addButton(self._new_button, QDialogButtonBox.ResetRole)
        self._move_button = buttons.button(QDialogButtonBox.Ok)
        self._move_button.setText("Move")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._fill()
        preselect = self.last_destination if self.last_destination and os.path.isdir(self.last_destination) else current_folder
        if not (preselect and select_folder_item(self._tree, preselect)) and self._tree.topLevelItemCount():
            self._tree.setCurrentItem(self._tree.topLevelItem(0))
        self._update_buttons()

    def _fill(self) -> None:
        self._tree.clear()
        add_folder_items(self._tree, self._library.folder_counts(), self._roots)
        for i in range(self._tree.topLevelItemCount()):
            self._tree.topLevelItem(i).setExpanded(True)
        self._tree.resizeColumnToContents(1)
        header = self._tree.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, header.ResizeMode.Stretch)

    def destination(self) -> Optional[str]:
        items = self._tree.selectedItems()
        return (items[0].data(0, ScopeRole) or {}).get("folder") if items else None

    def _update_buttons(self) -> None:
        folder = self.destination()
        usable = bool(folder and os.path.isdir(folder))
        self._move_button.setEnabled(usable)
        self._new_button.setEnabled(usable)

    def _new_folder(self) -> None:
        parent_folder = self.destination()
        if not parent_folder:
            return
        path = make_new_folder(self._library, parent_folder, self)
        if path:
            self._fill()
            select_folder_item(self._tree, path)

    def accept(self) -> None:
        MoveToFolderDialog.last_destination = self.destination()
        super().accept()
