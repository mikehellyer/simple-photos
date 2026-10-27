"""The left-hand sidebar: browse the collection by folder, by date or by
tag. Whichever tab was used last decides what the grid shows."""
from __future__ import annotations

import calendar
import os

from PySide6.QtCore import QItemSelectionModel, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QInputDialog,
    QMenu,
    QMessageBox,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from simple_photos.core.fileops import is_blank_folder
from simple_photos.core.library import Library
from simple_photos.gui.photo_grid import PHOTO_IDS_MIME

ScopeRole = Qt.UserRole + 1
ALL_PHOTOS = {}


def _tree() -> QTreeWidget:
    tree = QTreeWidget()
    tree.setHeaderHidden(True)
    tree.setColumnCount(2)
    tree.setRootIsDecorated(True)
    tree.setUniformRowHeights(True)
    return tree


def _fit_columns(tree: QTreeWidget) -> None:
    tree.resizeColumnToContents(1)
    header = tree.header()
    header.setStretchLastSection(False)
    header.setSectionResizeMode(0, header.ResizeMode.Stretch)
    header.setSectionResizeMode(1, header.ResizeMode.ResizeToContents)


def _item(parent, text: str, count: int, scope: dict) -> QTreeWidgetItem:
    item = QTreeWidgetItem(parent, [text, f"{count:,}"])
    item.setData(0, ScopeRole, scope)
    item.setTextAlignment(1, Qt.AlignRight | Qt.AlignVCenter)
    return item


def add_folder_items(tree: QTreeWidget, counts: dict[str, int], roots: list[str]) -> None:
    """One top-level item per collection folder, with its subfolders
    nested below, each showing how many photos it and its subfolders
    hold. counts: photos directly in each folder (Library.folder_counts)."""
    for root in roots:
        root = os.path.normpath(os.path.abspath(os.path.expanduser(root)))
        prefix = root.rstrip(os.sep) + os.sep
        # folder → photos in it and everything below it
        totals: dict[str, int] = {root: 0}
        for folder, count in counts.items():
            if folder != root and not folder.startswith(prefix):
                continue
            while True:
                totals[folder] = totals.get(folder, 0) + count
                if folder == root:
                    break
                folder = os.path.dirname(folder)
        label = os.path.basename(root) or root
        top = _item(tree, label, totals[root], {"folder": root})
        top.setToolTip(0, root if os.path.isdir(root) else f"{root}\n(not available right now)")
        nodes = {root: top}
        for folder in sorted(totals, key=lambda f: [part.lower() for part in f.split(os.sep)]):
            if folder == root:
                continue
            parent = nodes.get(os.path.dirname(folder), top)
            nodes[folder] = _item(parent, os.path.basename(folder), totals[folder], {"folder": folder})


def walk_items(tree: QTreeWidget):
    stack = [tree.topLevelItem(i) for i in range(tree.topLevelItemCount())]
    while stack:
        item = stack.pop()
        yield item
        stack.extend(item.child(i) for i in range(item.childCount()))


def select_folder_item(tree: QTreeWidget, folder: str) -> bool:
    for item in walk_items(tree):
        if (item.data(0, ScopeRole) or {}).get("folder") == folder:
            tree.setCurrentItem(item)
            parent = item.parent()
            while parent is not None:
                parent.setExpanded(True)
                parent = parent.parent()
            tree.scrollToItem(item)
            return True
    return False


class FolderTree(QTreeWidget):
    """The folder tree, which also takes photos dragged from the grid."""

    photos_dropped = Signal(list, str)  # photo ids, destination folder

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setHeaderHidden(True)
        self.setColumnCount(2)
        self.setUniformRowHeights(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QAbstractItemView.DropOnly)

    def _drop_folder(self, event) -> str | None:
        if not event.mimeData().hasFormat(PHOTO_IDS_MIME):
            return None
        item = self.itemAt(event.position().toPoint())
        folder = (item.data(0, ScopeRole) or {}).get("folder") if item else None
        return folder if folder and os.path.isdir(folder) else None

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasFormat(PHOTO_IDS_MIME):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:
        folder = self._drop_folder(event)
        if folder:
            self.setCurrentItem(self.itemAt(event.position().toPoint()), 0, QItemSelectionModel.NoUpdate)
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event) -> None:
        folder = self._drop_folder(event)
        if not folder:
            event.ignore()
            return
        ids = [int(i) for i in bytes(event.mimeData().data(PHOTO_IDS_MIME)).decode().split(",") if i]
        event.setDropAction(Qt.CopyAction)  # see PhotoModel.supportedDragActions
        event.accept()
        # Asked after the drop finishes, so the drag's cursor is gone.
        QTimer.singleShot(0, lambda: self.photos_dropped.emit(ids, folder))


class Sidebar(QTabWidget):
    scope_changed = Signal()
    tags_edited = Signal()
    photos_dropped = Signal(list, str)  # photo ids, destination folder
    new_folder_requested = Signal(str)  # parent folder
    rename_folder_requested = Signal(str)  # folder
    delete_folder_requested = Signal(str)  # an empty folder, to the Trash
    reveal_requested = Signal(str)  # folder

    def __init__(self, library: Library, parent=None):
        super().__init__(parent)
        self._library = library
        self._active = 0
        self._roots: list[str] = []

        folders_page = QWidget()
        folders_layout = QVBoxLayout(folders_page)
        folders_layout.setContentsMargins(0, 0, 0, 0)
        self._folders = FolderTree()
        self._folders.photos_dropped.connect(self.photos_dropped)
        self._folders.setContextMenuPolicy(Qt.CustomContextMenu)
        self._folders.customContextMenuRequested.connect(self._folder_menu)
        folders_layout.addWidget(self._folders)
        self._subfolders = QCheckBox("Include subfolders")
        self._subfolders.setChecked(True)
        self._subfolders.toggled.connect(lambda: self._activate(0))
        folders_layout.addWidget(self._subfolders)
        self._dates = _tree()
        self._tags = _tree()
        self._tags.setRootIsDecorated(False)
        self._tags.setContextMenuPolicy(Qt.CustomContextMenu)
        self._tags.customContextMenuRequested.connect(self._tag_menu)

        self.addTab(folders_page, "Folders")
        self.addTab(self._dates, "Dates")
        self.addTab(self._tags, "Tags")
        for page, tree in enumerate((self._folders, self._dates, self._tags)):
            tree.itemSelectionChanged.connect(lambda page=page: self._activate(page))
        self.currentChanged.connect(self._activate)

    # --- what the grid should show --------------------------------------

    def scope(self) -> dict:
        tree = (self._folders, self._dates, self._tags)[self._active]
        items = tree.selectedItems()
        if not items:
            return ALL_PHOTOS
        scope = dict(items[0].data(0, ScopeRole) or {})
        if "folder" in scope:
            scope["include_subfolders"] = self._subfolders.isChecked()
        return scope

    def _activate(self, page: int) -> None:
        self._active = page
        self.scope_changed.emit()

    def select_folder(self, folder: str) -> None:
        self.setCurrentIndex(0)
        select_folder_item(self._folders, os.path.normpath(folder))

    def selected_folder(self) -> str | None:
        """The folder picked in the Folders tab, if any."""
        items = self._folders.selectedItems()
        return (items[0].data(0, ScopeRole) or {}).get("folder") if items else None

    def _folder_menu(self, position) -> None:
        item = self._folders.itemAt(position)
        folder = (item.data(0, ScopeRole) or {}).get("folder") if item else None
        if not folder:
            return
        menu = QMenu(self)
        new = menu.addAction("New Folder…", lambda: self.new_folder_requested.emit(folder))
        rename = menu.addAction("Rename Folder…", lambda: self.rename_folder_requested.emit(folder))
        is_root = folder in {os.path.normpath(os.path.abspath(os.path.expanduser(r))) for r in self._roots}
        deletable = not is_root and is_blank_folder(folder)
        delete = menu.addAction(
            "Delete Folder" if deletable else "Delete Folder (only when empty)",
            lambda: self.delete_folder_requested.emit(folder),
        )
        delete.setEnabled(deletable)
        menu.addSeparator()
        reveal = menu.addAction("Show in File Manager", lambda: self.reveal_requested.emit(folder))
        if not os.path.isdir(folder):
            for entry in (new, rename, reveal):
                entry.setEnabled(False)
        menu.exec(self._folders.viewport().mapToGlobal(position))

    # --- filling the trees ----------------------------------------------

    def refresh(self, roots: list[str]) -> None:
        """Rebuild all three trees from the library, keeping what was
        selected and expanded. Doesn't emit scope_changed."""
        self._roots = list(roots)
        total = self._library.count()
        for tree, fill in (
            (self._folders, lambda: self._fill_folders(roots, total)),
            (self._dates, lambda: self._fill_dates(total)),
            (self._tags, lambda: self._fill_tags(total)),
        ):
            selected = [item.data(0, ScopeRole) for item in tree.selectedItems()]
            expanded = self._expanded_scopes(tree)
            first_fill = tree.topLevelItemCount() == 0
            tree.blockSignals(True)
            tree.clear()
            fill()
            self._restore_expanded(tree, expanded, first_fill)
            if selected:
                self._select_by_scope(tree, selected[0])
            tree.blockSignals(False)
            _fit_columns(tree)

    def _fill_folders(self, roots: list[str], total: int) -> None:
        _item(self._folders, "All Photos", total, ALL_PHOTOS)
        add_folder_items(self._folders, self._library.folder_counts(), roots)

    def _fill_dates(self, total: int) -> None:
        _item(self._dates, "All Dates", total, ALL_PHOTOS)
        years: dict[int, QTreeWidgetItem] = {}
        year_totals: dict[int, int] = {}
        months = self._library.date_counts()
        for year, _month, count in months:
            year_totals[year] = year_totals.get(year, 0) + count
        for year, month, count in reversed(months):
            if year not in years:
                years[year] = _item(self._dates, str(year), year_totals[year], {"year": year})
            name = calendar.month_name[month] if 1 <= month <= 12 else "Unknown"
            _item(years[year], name, count, {"year": year, "month": month})

    def _fill_tags(self, total: int) -> None:
        _item(self._tags, "All Photos", total, ALL_PHOTOS)
        _item(self._tags, "Untagged", self._library.untagged_count(), {"untagged": True})
        for tag_id, name, count in self._library.tag_counts():
            item = _item(self._tags, name, count, {"tag_id": tag_id})
            item.setData(1, ScopeRole, name)


    def _expanded_scopes(self, tree: QTreeWidget) -> list[dict]:
        return [item.data(0, ScopeRole) for item in walk_items(tree) if item.isExpanded()]

    def _restore_expanded(self, tree: QTreeWidget, expanded: list[dict], first_fill: bool) -> None:
        for item in walk_items(tree):
            scope = item.data(0, ScopeRole)
            if scope in expanded or (first_fill and item.parent() is None and "folder" in (scope or {})):
                item.setExpanded(True)

    def _select_by_scope(self, tree: QTreeWidget, scope: dict) -> None:
        for item in walk_items(tree):
            if item.data(0, ScopeRole) == scope:
                tree.setCurrentItem(item)
                parent = item.parent()
                while parent is not None:
                    parent.setExpanded(True)
                    parent = parent.parent()
                tree.scrollToItem(item)
                return

    # --- tag management -------------------------------------------------

    def _tag_menu(self, position) -> None:
        item = self._tags.itemAt(position)
        if item is None or "tag_id" not in (item.data(0, ScopeRole) or {}):
            return
        tag_id = item.data(0, ScopeRole)["tag_id"]
        name = item.data(1, ScopeRole)
        menu = QMenu(self)
        menu.addAction("Rename Tag…", lambda: self._rename_tag(tag_id, name))
        menu.addAction("Delete Tag", lambda: self._delete_tag(tag_id, name))
        menu.exec(self._tags.viewport().mapToGlobal(position))

    def _rename_tag(self, tag_id: int, name: str) -> None:
        new_name, ok = QInputDialog.getText(self, "Rename Tag", "New name:", text=name)
        if ok and new_name.strip() and new_name.strip() != name:
            self._library.rename_tag(tag_id, new_name)
            self.tags_edited.emit()

    def _delete_tag(self, tag_id: int, name: str) -> None:
        answer = QMessageBox.question(
            self, "Delete Tag", f"Remove the tag “{name}” from every photo? The photos themselves aren't touched."
        )
        if answer == QMessageBox.Yes:
            self._library.delete_tag(tag_id)
            self.tags_edited.emit()
