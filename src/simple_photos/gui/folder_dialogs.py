"""Dialogs for making a new folder and for choosing where to move photos."""
from __future__ import annotations

import html
import os
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
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


class FolderNameDialog(QDialog):
    """Asks for a folder name, checking it as it's typed — for a new
    folder inside parent_folder, or (with renaming) a new name for one."""

    def __init__(self, parent_folder: str, parent=None, renaming: Optional[str] = None):
        super().__init__(parent)
        self.setWindowTitle("Rename Folder" if renaming else "New Folder")
        self.setMinimumWidth(420)
        self._parent_folder = parent_folder
        self._renaming = renaming
        layout = QVBoxLayout(self)
        where = QLabel(
            f"Rename <b>{html.escape(short_path(renaming))}</b> to:" if renaming
            else f"Inside <b>{html.escape(short_path(parent_folder))}</b>"
        )
        where.setTextFormat(Qt.RichText)
        where.setWordWrap(True)
        layout.addWidget(where)
        self._name = QLineEdit(os.path.basename(renaming) if renaming else "")
        self._name.setPlaceholderText("Folder name")
        self._name.selectAll()
        self._name.textChanged.connect(self._validate)
        layout.addWidget(self._name)
        self._problem = QLabel()
        self._problem.setStyleSheet("color: #c0392b;")
        layout.addWidget(self._problem)
        self._buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self._buttons.button(QDialogButtonBox.Ok).setText("Rename" if renaming else "Create")
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)
        layout.addWidget(self._buttons)
        self._validate()

    def name(self) -> str:
        return self._name.text()

    def _validate(self) -> None:
        name = self._name.text()
        problem = folder_name_problem(name) if name else None
        target = os.path.join(self._parent_folder, name)
        unchanged = bool(self._renaming) and name == os.path.basename(self._renaming)
        if not problem and name and not unchanged and os.path.lexists(target):
            same = bool(self._renaming) and os.path.exists(self._renaming) and os.path.samefile(target, self._renaming)
            if not same:
                problem = f"There's already something called “{name}” here."
        self._problem.setText(problem or "")
        self._buttons.button(QDialogButtonBox.Ok).setEnabled(bool(name) and problem is None and not unchanged)


def make_new_folder(library: Library, parent_folder: str, widget) -> Optional[str]:
    """Ask for a name and make the folder. Its path, or None if cancelled
    or it couldn't be made (the user has already been told why)."""
    dialog = FolderNameDialog(parent_folder, widget)
    if not dialog.exec():
        return None
    try:
        path = create_folder(parent_folder, dialog.name())
    except (ValueError, OSError) as error:
        QMessageBox.warning(widget, "New Folder", f"Couldn't make the folder: {_reason(error)}")
        return None
    library.add_folder(path)
    return path


def ask_folder_rename(folder: str, widget) -> Optional[str]:
    """The new name the user wants for folder, or None if cancelled."""
    dialog = FolderNameDialog(os.path.dirname(folder), widget, renaming=folder)
    return dialog.name() if dialog.exec() else None


def _reason(error: Exception) -> str:
    return str(error) if isinstance(error, ValueError) else (getattr(error, "strerror", None) or str(error))


class MoveToFolderDialog(QDialog):
    last_destination: Optional[str] = None  # preselected next time

    def __init__(
        self, library: Library, roots: list[str], count: int, current_folder: Optional[str], parent=None, copying: bool = False
    ):
        super().__init__(parent)
        verb = "Copy" if copying else "Move"
        self.setWindowTitle(f"{verb} to Folder")
        self.resize(460, 520)
        self._library = library
        self._roots = roots
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"{verb} {'the photo' if count == 1 else f'{count:,} photos'} to:"))
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
        self._move_button.setText(verb)
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


class DeleteFoldersDialog(QDialog):
    """Review the empty folders found before they go to the Trash."""

    def __init__(self, folders: list[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Delete Empty Folders")
        self.resize(560, 460)
        layout = QVBoxLayout(self)
        intro = QLabel(
            "These folders have nothing in them (or only files your computer leaves behind, like "
            "Thumbs.db or .DS_Store). They'll go to the Trash, so they can be put back. "
            "Folders with any other files in them, such as videos, are left alone."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self._list = QListWidget()
        self._list.itemChanged.connect(self._update_button)
        for folder in folders:
            item = QListWidgetItem(short_path(folder))
            item.setToolTip(folder)
            item.setData(Qt.UserRole, folder)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked)
            self._list.addItem(item)
        layout.addWidget(self._list, 1)
        self._buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)
        layout.addWidget(self._buttons)
        self._update_button()

    def chosen(self) -> list[str]:
        items = (self._list.item(i) for i in range(self._list.count()))
        return [item.data(Qt.UserRole) for item in items if item.checkState() == Qt.Checked]

    def _update_button(self) -> None:
        count = len(self.chosen())
        ok = self._buttons.button(QDialogButtonBox.Ok)
        ok.setText(f"Move {count:,} Folder{'s' if count != 1 else ''} to Trash")
        ok.setEnabled(count > 0)
