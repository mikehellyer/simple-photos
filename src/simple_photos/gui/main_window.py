from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from PySide6.QtCore import QFile, QItemSelection, QItemSelectionModel, Qt, QThread, QTimer, QUrl, Slot
from PySide6.QtGui import QAction, QCursor, QDesktopServices, QIcon, QKeySequence
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressDialog,
    QSlider,
    QSplitter,
    QStackedWidget,
    QStatusBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from simple_photos import __version__
from simple_photos.core.diagnostics import log_dir, log_event
from simple_photos.core import autorotate
from simple_photos.core.fileops import find_blank_folders, is_blank_folder, move_photos, rename_folder, reveal_in_file_manager
from simple_photos.core.library import Library, PhotoFilter, ScanResult
from simple_photos.core.self_update import download_asset, find_asset_for_this_platform, launch_installer
from simple_photos.core.settings import THUMB_MAX, THUMB_MIN, SettingsStore
from simple_photos.core.rotation import RotateError, can_rotate, rotate_photo
from simple_photos.core.thumbnails import ensure_thumbnail, move_thumbnail, thumbnail_path
from simple_photos.core.updater import API_TIMEOUT_SECONDS, check_for_update
from simple_photos.core import duplicates, importer
from simple_photos.gui.autorotate_dialog import AutoRotateDialog
from simple_photos.gui.duplicates_dialog import DuplicatesDialog, FindDuplicatesDialog
from simple_photos.gui.folder_dialogs import DeleteFoldersDialog, MoveToFolderDialog, ask_folder_rename, make_new_folder, short_path
from simple_photos.gui.import_widgets import DevicesPanel, DeviceWatcher, ImportDialog
from simple_photos.gui.info_panel import InfoPanel
from simple_photos.gui.photo_grid import PhotoGrid, PhotoModel
from simple_photos.gui.settings_dialog import SettingsDialog, is_inside, normalise
from simple_photos.gui.sidebar import Sidebar
from simple_photos.gui.update_banner import UpdateBanner
from simple_photos.gui.viewer import ViewerPage
from simple_photos.gui.workers import BackgroundRunner, ScanWorker

UPDATE_OWNER = "mikehellyer"
UPDATE_REPO = "simple-photos"
BACKGROUND_JOIN_TIMEOUT_MS = (API_TIMEOUT_SECONDS + 1) * 1000
ICON_PATH = Path(__file__).parent / "resources" / "icon.png"
SCAN_REFRESH_SECONDS = 2.5  # how often the views catch up during a long scan

RATING_FILTERS = [("Any rating", 0), ("★ or more", 1), ("★★ or more", 2), ("★★★ or more", 3), ("★★★★ or more", 4), ("★★★★★", 5)]
SORT_CHOICES = [("Date taken", "taken"), ("File name", "name"), ("Rating", "rating"), ("Date added", "added"), ("File size", "size")]


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"Simple-Photos v{__version__}")
        self.setWindowIcon(QIcon(str(ICON_PATH)))
        self.resize(1320, 820)

        self._settings = SettingsStore()
        self._library = Library()
        self._runner = BackgroundRunner(self)
        self._pending_update = None
        self._scan_thread: QThread | None = None
        self._scan_worker: ScanWorker | None = None
        self._last_scan_refresh = 0.0
        self._rescan_after = False
        self._moving = False  # moving or rotating files
        self._auto_thread: QThread | None = None
        self._auto_worker: ScanWorker | None = None
        self._auto_progress: QProgressDialog | None = None
        self._auto_on_done = None
        self._after_scan_message = ""
        self._auto_checked = 0

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self._update_banner = UpdateBanner()
        self._update_banner.update_clicked.connect(self._start_update)
        layout.addWidget(self._update_banner)

        self._splitter = QSplitter(Qt.Horizontal)
        self._sidebar = Sidebar(self._library)
        self._sidebar.scope_changed.connect(self._reload_photos)
        self._sidebar.tags_edited.connect(self._tags_changed)
        self._sidebar.photos_dropped.connect(self._photos_dropped)
        self._sidebar.new_folder_requested.connect(self._new_folder)
        self._sidebar.rename_folder_requested.connect(self._rename_folder)
        self._sidebar.delete_folder_requested.connect(self._delete_folder)
        self._sidebar.reveal_requested.connect(lambda folder: QDesktopServices.openUrl(QUrl.fromLocalFile(folder)))
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(0)
        left_layout.addWidget(self._sidebar, 1)
        self._devices_panel = DevicesPanel()
        self._devices_panel.import_requested.connect(lambda device: self._import_from(device.path, device.name))
        left_layout.addWidget(self._devices_panel)
        self._splitter.addWidget(left)
        self._devices = DeviceWatcher(self)
        self._devices.changed.connect(self._devices_panel.set_devices)
        self._devices.arrived.connect(
            lambda device: self._status.showMessage(f"{device.description} connected — Import Photos… is in the Devices list", 10000)
        )
        self._devices_panel.set_devices(self._devices.devices)

        self._stack = QStackedWidget()
        self._model = PhotoModel(self)
        self._grid_page = self._build_grid_page()
        self._stack.addWidget(self._grid_page)
        self._viewer = ViewerPage(self._runner)
        self._viewer.set_thumbnail_source(self._model.cached_pixmap)
        self._viewer.close_requested.connect(self._viewer_escape)
        self._viewer.back_requested.connect(self._close_viewer)
        self._viewer.move_requested.connect(self._move_to_folder)
        self._viewer.trash_requested.connect(self._trash)
        self._viewer.current_changed.connect(self._on_viewer_moved)
        self._viewer.rate_requested.connect(lambda rating: self._rate_photos(self._viewer_ids(), rating))
        self._viewer.fullscreen_toggled.connect(self._toggle_fullscreen)
        self._viewer.position_changed.connect(self._show_viewer_position)
        self._viewer.rotate_requested.connect(lambda turns: self._rotate_photos([(row, turns) for row in self._active_rows()]))
        self._stack.addWidget(self._viewer)
        self._splitter.addWidget(self._stack)

        self._info = InfoPanel(self._library)
        self._info.rating_changed.connect(self._rate_photos)
        self._info.tags_changed.connect(self._tags_changed)
        self._splitter.addWidget(self._info)
        self._splitter.setStretchFactor(1, 1)
        self._splitter.setSizes([250, 790, 280])
        self._splitter.setCollapsible(1, False)
        layout.addWidget(self._splitter, 1)
        self.setCentralWidget(central)

        self._status = QStatusBar()
        self._count_label = QLabel()
        self._scan_label = QLabel()
        self._status.addPermanentWidget(self._scan_label)
        self._status.addPermanentWidget(self._count_label)
        self.setStatusBar(self._status)

        self._selection_timer = QTimer(self)
        self._selection_timer.setSingleShot(True)
        self._selection_timer.setInterval(60)
        self._selection_timer.timeout.connect(self._selection_settled)

        self._build_menus()
        self._info.setVisible(self._settings.settings.show_info_panel)
        self._refresh_sidebar()
        self._reload_photos()
        if self._settings.settings.scan_on_startup or self._library.count() == 0:
            QTimer.singleShot(0, self.rescan)
        self._check_for_updates()

    # --- layout ---------------------------------------------------------

    def _build_grid_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        bar = QHBoxLayout()
        bar.setContentsMargins(8, 6, 8, 6)
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search names, captions, tags, cameras…")
        self._search.setClearButtonEnabled(True)
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(300)
        self._search_timer.timeout.connect(self._reload_photos)
        self._search.textChanged.connect(self._search_timer.start)
        bar.addWidget(self._search, 2)

        self._rating_filter = QComboBox()
        for text, value in RATING_FILTERS:
            self._rating_filter.addItem(text, value)
        self._rating_filter.currentIndexChanged.connect(self._reload_photos)
        bar.addWidget(self._rating_filter)

        s = self._settings.settings
        self._sort = QComboBox()
        for text, key in SORT_CHOICES:
            self._sort.addItem(text, key)
        self._sort.setCurrentIndex(max(0, [key for _, key in SORT_CHOICES].index(s.sort)))
        self._sort.currentIndexChanged.connect(self._sort_changed)
        bar.addWidget(self._sort)
        self._sort_direction = QToolButton()
        self._sort_direction.setCheckable(True)
        self._sort_direction.setChecked(s.sort_descending)
        self._sort_direction.toggled.connect(self._sort_changed)
        self._update_sort_arrow()
        bar.addWidget(self._sort_direction)

        bar.addSpacing(12)
        self._size_slider = QSlider(Qt.Horizontal)
        self._size_slider.setRange(THUMB_MIN, THUMB_MAX)
        self._size_slider.setValue(s.thumb_size)
        self._size_slider.setFixedWidth(120)
        self._size_slider.setToolTip("Thumbnail size")
        self._size_slider.valueChanged.connect(self._thumb_size_changed)
        bar.addWidget(QLabel("Size"))
        bar.addWidget(self._size_slider)
        layout.addLayout(bar)

        self._grid = PhotoGrid(self._model)
        self._grid.set_thumb_size(s.thumb_size)
        self._grid.activated_photo.connect(self._open_viewer)
        self._grid.rate_requested.connect(lambda rating: self._rate_photos(self._selected_ids(), rating))
        self._grid.selectionModel().selectionChanged.connect(lambda *_: self._selection_timer.start())
        self._grid.setContextMenuPolicy(Qt.ActionsContextMenu)
        layout.addWidget(self._grid, 1)
        return page

    def _build_menus(self) -> None:
        def action(menu, text, shortcut, slot, local=False):
            act = QAction(text, self)
            if shortcut:
                act.setShortcut(QKeySequence(shortcut))
            if local:
                # Only while the grid or viewer has focus, so e.g. Delete in
                # the tag list can't trash photos.
                act.setShortcutContext(Qt.WidgetWithChildrenShortcut)
                self._grid.addAction(act)
                self._viewer.addAction(act)
            act.triggered.connect(lambda _=False: slot())
            menu.addAction(act)
            return act

        file_menu = self.menuBar().addMenu("&File")
        self._import_menu = file_menu.addMenu("Import From")
        self._import_menu.aboutToShow.connect(self._fill_import_menu)
        file_menu.addSeparator()
        action(file_menu, "New Folder…", "Ctrl+Shift+N", lambda: self._new_folder(None))
        action(file_menu, "Rename Folder…", "F2", lambda: self._rename_folder(None))
        action(file_menu, "Delete Empty Folders…", None, self._delete_empty_folders)
        action(file_menu, "Add Folder to Collection…", "Ctrl+O", self._add_folder)
        action(file_menu, "Look for New Photos", "F5", self.rescan)
        file_menu.addSeparator()
        action(file_menu, "Settings…", QKeySequence.Preferences, self._open_settings)
        file_menu.addSeparator()
        action(file_menu, "Quit", QKeySequence.Quit, self.close)

        photo_menu = self.menuBar().addMenu("&Photo")
        view_action = action(photo_menu, "View", None, lambda: self._open_viewer(self._grid.currentIndex().row()))
        open_action = action(photo_menu, "Open in Default App", "Ctrl+Shift+O", self._open_externally)
        reveal_action = action(photo_menu, "Show in File Manager", "Ctrl+Shift+R", self._reveal)
        move_action = action(photo_menu, "Move to Folder…", "Ctrl+Shift+M", self._move_to_folder)
        photo_menu.addSeparator()
        left_action = action(photo_menu, "Rotate Left", "Ctrl+L", lambda: self._rotate_photos([(row, -1) for row in self._active_rows()]))
        right_action = action(photo_menu, "Rotate Right", "Ctrl+R", lambda: self._rotate_photos([(row, 1) for row in self._active_rows()]))
        action(photo_menu, "Auto-Rotate…", None, self._auto_rotate)
        action(photo_menu, "Find Duplicates…", None, self._find_duplicates)
        photo_menu.addSeparator()
        rate_menu = photo_menu.addMenu("Rate")
        for rating in range(6):
            action(rate_menu, "No rating" if rating == 0 else "★" * rating, f"Ctrl+{rating}", lambda r=rating: self._rate_photos(self._active_ids(), r))
        action(photo_menu, "Add Tag…", "Ctrl+T", self._focus_tags)
        photo_menu.addSeparator()
        trash_action = action(photo_menu, "Move to Trash", QKeySequence.Delete, self._trash, local=True)
        for act in (view_action, open_action, reveal_action, move_action, left_action, right_action):
            self._grid.addAction(act)
        separator = QAction(self)
        separator.setSeparator(True)
        self._grid.addAction(separator)
        self._grid.addAction(trash_action)

        view_menu = self.menuBar().addMenu("&View")
        action(view_menu, "Find", QKeySequence.Find, self._focus_search)
        self._info_action = action(view_menu, "Show Info Panel", "Ctrl+I", self._toggle_info)
        self._info_action.setCheckable(True)
        self._info_action.setChecked(self._settings.settings.show_info_panel)
        view_menu.addSeparator()
        action(view_menu, "Slideshow", None, self._start_slideshow)
        action(view_menu, "Full Screen", None, self._toggle_fullscreen)

        help_menu = self.menuBar().addMenu("&Help")
        help_menu.addAction("Open Log Folder", lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(log_dir()))))
        help_menu.addAction("About Simple-Photos", self._about)

    # --- what's shown ---------------------------------------------------

    def _current_filter(self) -> PhotoFilter:
        return PhotoFilter(
            **self._sidebar.scope(),
            min_rating=self._rating_filter.currentData() or 0,
            text=self._search.text().strip(),
            sort=self._sort.currentData(),
            descending=self._sort_direction.isChecked(),
        )

    def _reload_photos(self) -> None:
        selected = self._selected_ids()
        current = self._model.photo_at(self._grid.currentIndex().row())
        rows = self._library.query(self._current_filter())
        self._model.set_photos(rows)

        selection = QItemSelection()
        for photo_id in selected:
            row = self._model.row_of(photo_id)
            if row is not None:
                index = self._model.index(row)
                selection.select(index, index)
        selection_model = self._grid.selectionModel()
        if current is not None and self._model.row_of(current.id) is not None:
            selection_model.setCurrentIndex(self._model.index(self._model.row_of(current.id)), QItemSelectionModel.NoUpdate)
        selection_model.select(selection, QItemSelectionModel.ClearAndSelect)
        if self._grid.currentIndex().isValid():
            self._grid.scrollTo(self._grid.currentIndex())

        total = self._update_count()
        if total == 0:
            folders = self._settings.settings.collection_folders
            if self._scan_thread is not None:
                self._grid.empty_text = "Looking for photos…"
            elif not folders:
                self._grid.empty_text = "No collection folders yet.\nUse File ▸ Add Folder to Collection… to choose where your photos are."
            else:
                self._grid.empty_text = "No photos found in " + ", ".join(folders) + ".\nUse File ▸ Add Folder to Collection… to add another folder."
        else:
            self._grid.empty_text = "No photos match."
        self._grid.viewport().update()
        self._selection_settled()

    def _update_count(self) -> int:
        total = self._library.count()
        shown = self._model.rowCount()
        self._count_label.setText(f"{shown:,} photos" if shown == total else f"{shown:,} of {total:,} photos")
        return total

    def _refresh_sidebar(self) -> None:
        self._sidebar.refresh(self._settings.settings.collection_folders)

    def _selected_ids(self) -> list[int]:
        rows = sorted(index.row() for index in self._grid.selectionModel().selectedIndexes())
        return [self._model.photo_at(row).id for row in rows]

    def _viewer_ids(self) -> list[int]:
        photo = self._viewer.current_photo()
        return [photo.id] if photo else []

    def _active_ids(self) -> list[int]:
        return self._viewer_ids() if self._stack.currentWidget() is self._viewer else self._selected_ids()

    def _selection_settled(self) -> None:
        if self._stack.currentWidget() is self._grid_page:
            self._info.show_photos(self._selected_ids())
            count = len(self._selected_ids())
            if count > 1:
                self._status.showMessage(f"{count:,} selected")

    def _sort_changed(self) -> None:
        self._update_sort_arrow()
        self._settings.update(sort=self._sort.currentData(), sort_descending=self._sort_direction.isChecked())
        self._reload_photos()

    def _update_sort_arrow(self) -> None:
        descending = self._sort_direction.isChecked()
        self._sort_direction.setText("↓" if descending else "↑")
        self._sort_direction.setToolTip("Descending — click for ascending" if descending else "Ascending — click for descending")

    def _thumb_size_changed(self, size: int) -> None:
        self._grid.set_thumb_size(size)
        self._settings.settings.thumb_size = size
        self._save_settings_later()

    def _save_settings_later(self) -> None:
        if not hasattr(self, "_settings_timer"):
            self._settings_timer = QTimer(self)
            self._settings_timer.setSingleShot(True)
            self._settings_timer.setInterval(800)
            self._settings_timer.timeout.connect(self._settings.save)
        self._settings_timer.start()

    # --- editing --------------------------------------------------------

    def _rate_photos(self, photo_ids: list[int], rating: int) -> None:
        if not photo_ids:
            return
        self._library.set_rating(photo_ids, rating)
        self._model.set_rating(photo_ids, rating)
        self._info.set_rating_display(rating)
        self._viewer.refresh_rating()
        stars = "★" * rating if rating else "no rating"
        self._status.showMessage(f"Rated {len(photo_ids):,} photo{'s' if len(photo_ids) != 1 else ''} {stars}", 4000)

    def _tags_changed(self) -> None:
        self._refresh_sidebar()
        if self._stack.currentWidget() is self._grid_page:
            scope = self._sidebar.scope()
            if "tag_id" in scope or "untagged" in scope or self._search.text().strip():
                self._reload_photos()
                return
        self._info.show_photos(self._active_ids())

    def _focus_tags(self) -> None:
        if not self._info.isVisible():
            self._toggle_info()
        self._info.focus_tag_entry()

    def _focus_search(self) -> None:
        if self._stack.currentWidget() is not self._grid_page:
            self._close_viewer()
        self._search.setFocus()
        self._search.selectAll()

    def _toggle_info(self) -> None:
        visible = not self._info.isVisible()
        self._info.setVisible(visible)
        self._info_action.setChecked(visible)
        self._settings.update(show_info_panel=visible)

    # --- viewer ---------------------------------------------------------

    def _open_viewer(self, row: int) -> None:
        if self._model.photo_at(row) is None:
            return
        self._info.save_caption()
        self._stack.setCurrentWidget(self._viewer)
        self._viewer.show_photos(list(self._model.photos()), row)
        self._viewer.view.setFocus()
        self._on_viewer_moved(row)

    def _show_viewer_position(self, text: str) -> None:
        if self._stack.currentWidget() is self._viewer:
            self._count_label.setText(text)

    def _on_viewer_moved(self, row: int) -> None:
        self._info.show_photos(self._viewer_ids())

    def _viewer_escape(self) -> None:
        if self.isFullScreen():
            self._toggle_fullscreen()
        else:
            self._close_viewer()

    def _close_viewer(self) -> None:
        self._viewer.stop_slideshow()
        if self.isFullScreen():
            self._toggle_fullscreen()
        photo = self._viewer.current_photo()
        self._stack.setCurrentWidget(self._grid_page)
        row = self._model.row_of(photo.id) if photo else None
        if row is not None:
            index = self._model.index(row)
            self._grid.selectionModel().setCurrentIndex(index, QItemSelectionModel.ClearAndSelect)
            self._grid.scrollTo(index)
        self._grid.setFocus()
        self._update_count()
        self._selection_settled()

    def _start_slideshow(self) -> None:
        if self._stack.currentWidget() is not self._viewer:
            row = self._grid.currentIndex().row()
            self._open_viewer(row if row >= 0 else 0)
        if self._stack.currentWidget() is self._viewer:
            self._viewer.toggle_slideshow()

    def _toggle_fullscreen(self) -> None:
        if self._stack.currentWidget() is not self._viewer:
            row = self._grid.currentIndex().row()
            self._open_viewer(row if row >= 0 else 0)
            if self._stack.currentWidget() is not self._viewer:
                return
        full = not self.isFullScreen()
        for widget in (self._sidebar, self.menuBar(), self.statusBar()):
            widget.setVisible(not full)
        self._info.setVisible(not full and self._settings.settings.show_info_panel)
        self._viewer.set_toolbar_visible(not full)
        if full:
            self._was_maximized = self.isMaximized()
            self.showFullScreen()
        elif getattr(self, "_was_maximized", False):
            self.showMaximized()
        else:
            self.showNormal()
        self._viewer.view.setFocus()

    # --- files ----------------------------------------------------------

    def _active_rows(self) -> list:
        """The photo in the viewer, or the photos selected in the grid."""
        if self._stack.currentWidget() is self._viewer:
            photo = self._viewer.current_photo()
            return [photo] if photo else []
        ids = set(self._selected_ids())
        return [row for row in self._model.photos() if row.id in ids]

    def _active_paths(self) -> list[str]:
        return [row.path for row in self._active_rows()]

    def _open_externally(self) -> None:
        for path in self._active_paths()[:10]:
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def _reveal(self) -> None:
        paths = self._active_paths()
        if paths and not reveal_in_file_manager(paths[0]):
            QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(paths[0])))

    def _trash(self) -> None:
        rows = self._active_rows()
        if not rows:
            return
        what = f"“{rows[0].filename}”" if len(rows) == 1 else f"these {len(rows):,} photos"
        answer = QMessageBox.question(self, "Move to Trash", f"Move {what} to the Trash?")
        if answer != QMessageBox.Yes:
            return
        trashed, failed = [], []
        for row in rows:
            (trashed if QFile.moveToTrash(row.path) else failed).append(row)
        self._library.forget([row.id for row in trashed])
        self._photos_changed_on_disk()
        if failed:
            QMessageBox.warning(self, "Move to Trash", f"Couldn't move {len(failed):,} photo(s) to the Trash, e.g. {failed[0].path}")
        else:
            self._status.showMessage(f"Moved {len(trashed):,} photo{'s' if len(trashed) != 1 else ''} to the Trash", 5000)

    def _photos_changed_on_disk(self) -> None:
        """Photos were trashed or moved: refresh everything. In the viewer,
        stay at the same place in the (possibly shorter) list."""
        in_viewer = self._stack.currentWidget() is self._viewer
        viewer_photo = self._viewer.current_photo()
        viewer_row = self._viewer.current_row()
        self._refresh_sidebar()
        self._reload_photos()
        if in_viewer:
            row = self._model.row_of(viewer_photo.id) if viewer_photo else None
            if row is None:
                row = min(viewer_row, self._model.rowCount() - 1)
            if row >= 0:
                self._open_viewer(row)
            else:
                self._close_viewer()

    # --- folders --------------------------------------------------------

    def _new_folder(self, parent_folder: str | None) -> None:
        """Make a folder inside parent_folder — by default the folder picked
        in the sidebar, or else the first collection folder."""
        if parent_folder is None:
            selected = self._sidebar.selected_folder()
            candidates = [selected] if selected else []
            candidates += [normalise(f) for f in self._settings.settings.collection_folders]
            parent_folder = next((f for f in candidates if os.path.isdir(f)), None)
        if parent_folder is None:
            QMessageBox.information(self, "New Folder", "Add a folder to your collection first (File ▸ Add Folder to Collection…).")
            return
        path = make_new_folder(self._library, parent_folder, self)
        if path:
            self._refresh_sidebar()
            self._sidebar.select_folder(path)
            self._status.showMessage(f"Made {short_path(path)}", 5000)

    def _move_to_folder(self) -> None:
        rows = self._active_rows()
        if not rows:
            return
        folders = {os.path.dirname(row.path) for row in rows}
        dialog = MoveToFolderDialog(
            self._library,
            self._settings.settings.collection_folders,
            len(rows),
            folders.pop() if len(folders) == 1 else self._sidebar.selected_folder(),
            self,
        )
        if dialog.exec() and dialog.destination():
            self._refresh_sidebar()  # the dialog may have made folders
            self._move_photos(rows, dialog.destination())

    def _photos_dropped(self, photo_ids: list[int], folder: str) -> None:
        wanted = set(photo_ids)
        rows = [row for row in self._model.photos() if row.id in wanted]
        if not rows:
            return
        menu = QMenu(self)
        what = f"“{rows[0].filename}”" if len(rows) == 1 else f"{len(rows):,} photos"
        move = menu.addAction(f"Move {what} to {os.path.basename(folder) or folder}")
        menu.addSeparator()
        menu.addAction("Cancel")
        if menu.exec(QCursor.pos()) is move:
            self._move_photos(rows, folder)

    def _move_photos(self, rows, destination: str) -> None:
        if self._busy_with_files():
            return
        self._pause_scan()
        self._info.save_caption()
        self._moving = True
        self._status.showMessage(f"Moving {len(rows):,} photo{'s' if len(rows) != 1 else ''}…")
        mtimes = {row.path: row.file_mtime for row in rows}
        paths = [row.path for row in rows]
        cache_dir = self._model.loader.cache_dir

        def job():
            result = move_photos(paths, destination)
            for old, new in result.moved:
                move_thumbnail(old, new, mtimes[old], cache_dir)
            return result

        self._runner.run(job, lambda result: self._on_moved(result, destination), lambda error: self._on_moved(error, destination))

    def _on_moved(self, result, destination: str) -> None:
        self._moving = False
        if isinstance(result, Exception):
            QMessageBox.warning(self, "Move to Folder", f"Couldn't move the photos: {result}")
            result = None
        else:
            self._library.relocate(result.moved)
            self._photos_changed_on_disk()
            moved = len(result.moved)
            where = short_path(destination)
            if result.failed:
                path, reason = result.failed[0]
                QMessageBox.warning(
                    self,
                    "Move to Folder",
                    f"Moved {moved:,} to {where}, but {len(result.failed):,} couldn't be moved.\n\n"
                    f"{os.path.basename(path)}: {reason}",
                )
            elif moved:
                self._status.showMessage(f"Moved {moved:,} photo{'s' if moved != 1 else ''} to {where}", 6000)
            else:
                self._status.showMessage(f"Already in {where}", 5000)
        self._rescan_if_pending()

    def _busy_with_files(self) -> bool:
        if self._moving:
            self._status.showMessage("Still busy with the last photos — try again in a moment", 5000)
        return self._moving

    def _pause_scan(self) -> None:
        """Before moving, renaming or rotating files: a scan running
        alongside could see a photo vanish from its old place and drop its
        tags. Stop it (a stopped scan removes nothing) and look again
        afterwards."""
        if self._scan_thread is not None:
            self._stop_scan()
            self._rescan_after = True

    def _rescan_if_pending(self) -> None:
        if self._rescan_after and self._scan_thread is None and not self._moving:
            self._rescan_after = False
            self.rescan()

    def _delete_folder(self, folder: str) -> None:
        """The sidebar's Delete Folder (only offered for empty folders)."""
        if self._busy_with_files():
            return
        answer = QMessageBox.question(self, "Delete Folder", f"Move the empty folder “{os.path.basename(folder)}” to the Trash?")
        if answer == QMessageBox.Yes:
            self._trash_folders([folder])

    def _delete_empty_folders(self) -> None:
        if self._busy_with_files():
            return
        roots = list(self._settings.settings.collection_folders)
        self._status.showMessage("Looking for empty folders…")
        self._runner.run(lambda: find_blank_folders(roots), self._on_blank_folders_found, lambda error: self._on_blank_folders_found([]))

    def _on_blank_folders_found(self, folders: list[str]) -> None:
        self._status.clearMessage()
        if not folders:
            QMessageBox.information(self, "Delete Empty Folders", "There are no empty folders in your collection.")
            return
        dialog = DeleteFoldersDialog(folders, self)
        if dialog.exec():
            self._trash_folders(dialog.chosen())

    def _trash_folders(self, folders: list[str]) -> None:
        self._pause_scan()
        trashed, kept = [], []
        for folder in folders:
            # Checked again now: something may have been saved there since.
            if is_blank_folder(folder) and QFile.moveToTrash(folder):
                self._library.forget_folder(folder)
                trashed.append(folder)
            else:
                kept.append(folder)
        selected = self._sidebar.selected_folder()
        self._refresh_sidebar()
        gone = next((f for f in trashed if selected and (selected == f or selected.startswith(f + os.sep))), None)
        if gone:
            self._sidebar.select_folder(os.path.dirname(gone))  # it went: show the folder it was in
        self._reload_photos()
        if kept:
            QMessageBox.warning(
                self,
                "Delete Folder",
                f"Moved {len(trashed):,} to the Trash, but {len(kept):,} couldn't be — "
                f"e.g. {short_path(kept[0])} (it may have files in it now).",
            )
        else:
            self._status.showMessage(f"Moved {len(trashed):,} empty folder{'s' if len(trashed) != 1 else ''} to the Trash", 6000)
        self._rescan_if_pending()

    def _rename_folder(self, folder: str | None) -> None:
        folder = folder or self._sidebar.selected_folder()
        if not folder or not os.path.isdir(folder):
            QMessageBox.information(self, "Rename Folder", "Pick a folder in the Folders list first.")
            return
        if self._busy_with_files():
            return
        name = ask_folder_rename(folder, self)
        if not name:
            return
        self._pause_scan()
        self._info.save_caption()
        try:
            new = rename_folder(folder, name)
        except (ValueError, OSError) as error:
            reason = str(error) if isinstance(error, ValueError) else (error.strerror or str(error))
            QMessageBox.warning(self, "Rename Folder", f"Couldn't rename the folder: {reason}")
            self._rescan_if_pending()
            return
        changed = self._library.rename_folder(folder, new)
        roots = self._settings.settings.collection_folders
        if any(normalise(root) == folder for root in roots):
            self._settings.update(collection_folders=[new if normalise(root) == folder else root for root in roots])
        cache_dir = self._model.loader.cache_dir
        self._runner.run(lambda: [move_thumbnail(old, moved, mtime, cache_dir) for old, moved, mtime in changed], lambda _: None, lambda _: None)
        self._photos_changed_on_disk()
        self._sidebar.select_folder(new)
        self._status.showMessage(f"Renamed to {short_path(new)}", 5000)
        self._rescan_if_pending()

    # --- rotating -------------------------------------------------------

    def _rotate_photos(self, items) -> None:
        """items: (photo row, quarter turns clockwise) pairs."""
        if not items or self._busy_with_files():
            return
        rotatable = [(row, turns) for row, turns in items if can_rotate(row.path)]
        if not rotatable:
            ext = os.path.splitext(items[0][0].path)[1] or "these"
            self._status.showMessage(f"Rotating {ext} files isn't supported (it would lose quality)", 6000)
            return
        self._pause_scan()
        self._moving = True
        self._status.showMessage(f"Rotating {len(rotatable):,} photo{'s' if len(rotatable) != 1 else ''}…")
        cache_dir = self._model.loader.cache_dir

        def job():
            done, failed = [], []
            for row, turns in rotatable:
                try:
                    rotate_photo(row.path, turns)
                except (RotateError, OSError) as error:
                    failed.append((row, str(error)))
                    continue
                # The file keeps its modified time, so drop the old thumbnail by hand.
                thumbnail_path(row.path, row.file_mtime, cache_dir).unlink(missing_ok=True)
                done.append(row)
            return done, failed

        skipped = len(items) - len(rotatable)
        self._runner.run(job, lambda result: self._on_rotated(result, skipped), lambda error: self._on_rotated(([], [(rotatable[0][0], str(error))]), skipped))

    def _on_rotated(self, result, skipped: int) -> None:
        done, failed = result
        self._moving = False
        self._library.refresh_files([row.path for row in done])
        self._model.drop_thumbnails([row.id for row in done])
        current = self._viewer.current_photo()
        if self._stack.currentWidget() is self._viewer and current and any(row.id == current.id for row in done):
            self._viewer.reload_current()
        self._info.show_photos(self._active_ids())
        if failed:
            row, reason = failed[0]
            QMessageBox.warning(self, "Rotate", f"Couldn't rotate {len(failed):,} photo(s).\n\n{row.filename}: {reason}")
        elif done:
            extra = f" ({skipped:,} skipped: format not supported)" if skipped else ""
            self._status.showMessage(f"Rotated {len(done):,} photo{'s' if len(done) != 1 else ''}{extra}", 5000)
        self._rescan_if_pending()

    # --- duplicates -----------------------------------------------------

    def _find_duplicates(self) -> None:
        if self._auto_thread is not None or self._busy_with_files():
            return
        total = self._library.count()
        if total < 2:
            QMessageBox.information(self, "Find Duplicates", "There aren't enough photos in the library to compare yet.")
            return
        options = FindDuplicatesDialog(self._model.rowCount(), total, self)
        if not options.exec():
            return
        mode = options.mode()
        only = [row.id for row in self._model.photos()] if options.only_shown() else None
        db_path = self._library.db_path
        cache_dir = self._model.loader.cache_dir

        def job(worker: ScanWorker, cancel):
            library = Library(db_path)
            try:
                return duplicates.search(
                    library,
                    library.duplicate_candidates(only),
                    mode,
                    progress=lambda done, total, stage: worker.progress.emit(done, f"{total}\t{stage}…"),
                    should_stop=cancel.is_set,
                    cache_dir=cache_dir,
                    workers=autorotate.worker_count(),
                )
            finally:
                library.close()

        self._start_background_check("Find Duplicates", "Looking for duplicates…", job, self._on_duplicates_found)

    def _start_background_check(self, title: str, label: str, job, on_done) -> None:
        """Run job(worker, cancel) on its own thread behind a progress
        window with a Stop button (Auto-Rotate, Find Duplicates)."""
        progress = QProgressDialog(label, "Stop", 0, 0, self)
        progress.setWindowTitle(title)
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        thread = QThread()
        worker = ScanWorker(job)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._on_auto_progress, Qt.QueuedConnection)
        worker.done.connect(self._on_background_check_done, Qt.QueuedConnection)
        progress.canceled.connect(worker.cancel_event.set)
        self._auto_thread, self._auto_worker, self._auto_progress = thread, worker, progress
        self._auto_on_done = on_done
        thread.start()
        progress.show()

    @Slot(object)
    def _on_background_check_done(self, result) -> None:
        on_done = self._auto_on_done
        self._auto_on_done = None
        self._on_auto_done(result, on_done)

    def _on_duplicates_found(self, result, stopped: bool) -> None:
        if isinstance(result, Exception):
            log_event(f"find duplicates failed: {result!r}")
            QMessageBox.warning(self, "Find Duplicates", f"The search stopped: {result}")
            return
        if result.stopped:
            self._status.showMessage("Stopped looking for duplicates — what was checked is remembered for next time", 6000)
            return
        if not result.groups:
            QMessageBox.information(self, "Find Duplicates", f"No duplicates found among {result.checked:,} photos.")
            return
        dialog = DuplicatesDialog(result, self)
        if dialog.exec():
            self._remove_duplicates(dialog.chosen())

    def _remove_duplicates(self, choices: list[tuple[list[int], list[int]]]) -> None:
        if not choices or self._busy_with_files():
            return
        self._pause_scan()
        self._info.save_caption()
        trashed, failed = [], []
        for keep, remove in choices:
            removed_here = []
            for photo_id in remove:
                photo = self._library.photo(photo_id)
                if photo is None:
                    continue
                (removed_here if QFile.moveToTrash(photo.path) else failed).append(photo)
            if removed_here:
                self._library.merge_into(keep[0], [p.id for p in removed_here])
                self._library.forget([p.id for p in removed_here])
                trashed += removed_here
        self._photos_changed_on_disk()
        if failed:
            QMessageBox.warning(
                self, "Find Duplicates",
                f"Moved {len(trashed):,} to the Trash, but {len(failed):,} couldn't be moved, e.g. {failed[0].path}",
            )
        else:
            self._status.showMessage(f"Moved {len(trashed):,} duplicate{'s' if len(trashed) != 1 else ''} to the Trash", 6000)
        self._rescan_if_pending()

    def _auto_rotate(self) -> None:
        if not autorotate.available():
            QMessageBox.warning(self, "Auto-Rotate", "The face detector Auto-Rotate needs isn't available in this installation.")
            return
        if self._auto_thread is not None:
            return
        selected = self._active_rows()
        rows = selected if len(selected) > 1 else list(self._model.photos())
        if not rows:
            return
        cache_dir = self._model.loader.cache_dir

        def job(worker: ScanWorker, cancel):
            from concurrent.futures import ThreadPoolExecutor, as_completed

            found = []
            with ThreadPoolExecutor(autorotate.worker_count()) as pool:
                futures = {pool.submit(autorotate.suggested_turns, row.path): row for row in rows}
                for checked, future in enumerate(as_completed(futures), start=1):
                    if cancel.is_set():
                        pool.shutdown(wait=True, cancel_futures=True)
                        break
                    try:
                        turns = future.result()
                    except Exception:  # unreadable photo: nothing to suggest
                        turns = None
                    if turns:
                        found.append((futures[future], turns))
                    worker.progress.emit(checked, "")
            order = {row.id: i for i, row in enumerate(rows)}
            found.sort(key=lambda pair: order[pair[0].id])
            return [(row, turns, str(ensure_thumbnail(row.path, row.file_mtime, cache_dir) or "") or None) for row, turns in found]

        self._auto_checked = len(rows)
        label = f"Looking for sideways photos among {len(rows):,}…"
        self._start_background_check("Auto-Rotate", label, job, self._on_sideways_found)

    @Slot(int, str)
    def _on_auto_progress(self, done: int, detail: str) -> None:
        """detail: "total\tstage" (Find Duplicates' stages vary in length),
        or empty for Auto-Rotate, which counts through its photos."""
        progress = self._auto_progress
        if progress is None:
            return
        if detail:
            total, stage = detail.split("\t", 1)
            progress.setLabelText(stage)
            progress.setMaximum(int(total))
        else:
            progress.setMaximum(self._auto_checked)
        progress.setValue(done)

    def _on_auto_done(self, result, on_done) -> None:
        thread, progress = self._auto_thread, self._auto_progress
        stopped = self._auto_worker is not None and self._auto_worker.cancel_event.is_set()
        self._auto_thread = self._auto_worker = self._auto_progress = None
        if thread is not None:
            # done arrives before the thread's quit (queued behind it): ask now.
            thread.quit()
            thread.wait()
            thread.deleteLater()
        if progress is not None:
            progress.close()
            progress.deleteLater()
        on_done(result, stopped)

    def _on_sideways_found(self, result, stopped: bool) -> None:
        if isinstance(result, Exception):
            log_event(f"auto-rotate failed: {result!r}")
            QMessageBox.warning(self, "Auto-Rotate", f"Auto-Rotate stopped: {result}")
            return
        rotatable = [s for s in result if can_rotate(s[0].path)]
        skipped = len(result) - len(rotatable)
        if not rotatable:
            if stopped and not result:
                return
            QMessageBox.information(
                self,
                "Auto-Rotate",
                ("Stopped early. " if stopped else "")
                + "No sideways photos found"
                + (f" ({skipped:,} found in formats that can't be rotated)" if skipped else "")
                + ".\n\nAuto-Rotate goes by faces, so photos without people in them are left as they are. "
                "Use Rotate Left / Right (Ctrl+L / Ctrl+R) for those.",
            )
            return
        dialog = AutoRotateDialog(rotatable, skipped, self)
        if dialog.exec():
            self._rotate_photos(dialog.chosen())

    # --- importing ------------------------------------------------------

    def _fill_import_menu(self) -> None:
        self._devices.check()
        menu = self._import_menu
        menu.clear()
        for device in self._devices.devices:
            menu.addAction(device.description, lambda device=device: self._import_from(device.path, device.name))
        if not self._devices.devices:
            none = menu.addAction("No USB sticks, cards or cameras plugged in")
            none.setEnabled(False)
        menu.addSeparator()
        menu.addAction("Other Folder…", self._import_from_folder)

    def _import_from_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Import Photos From")
        if folder:
            self._import_from(folder, os.path.basename(os.path.normpath(folder)) or folder)

    def _import_parents(self) -> list[str]:
        """Where an import's new folder can go: the import folder from
        Settings (made again if it's gone), each collection folder, then
        the folder picked in the sidebar. The first is the default."""
        options = []
        chosen = self._settings.settings.import_folder
        roots = self._settings.settings.collection_folders
        if chosen and is_inside(chosen, roots):
            if not os.path.isdir(chosen):
                try:
                    os.makedirs(chosen)
                    self._library.add_folder(chosen)
                except OSError:
                    pass
            if os.path.isdir(chosen):
                options.append(normalise(chosen))
        for root in roots:
            root = normalise(root)
            if os.path.isdir(root) and root not in options:
                options.append(root)
        selected = self._sidebar.selected_folder()
        if options and selected and os.path.isdir(selected) and selected not in options:
            options.append(selected)
        return options

    def _import_from(self, source: str, name: str) -> None:
        if self._auto_thread is not None or self._busy_with_files():
            return
        if not self._import_parents():
            QMessageBox.information(self, "Import", "Add a folder to your collection first (File ▸ Add Folder to Collection…).")
            return
        known = self._library.import_signatures()

        def job(worker: ScanWorker, cancel):
            return importer.find_photos(
                source,
                known,
                progress=lambda found: worker.progress.emit(found, f"0\tLooking for photos on {name}… {found:,} found"),
                should_stop=cancel.is_set,
            )

        self._start_background_check(
            "Import", f"Looking for photos on {name}…", job, lambda result, stopped: self._on_import_scanned(result, stopped, name)
        )

    def _on_import_scanned(self, scan, stopped: bool, name: str) -> None:
        if isinstance(scan, Exception):
            QMessageBox.warning(self, "Import", f"Couldn't read {name}: {scan}")
            return
        if stopped:
            return
        if not scan.photos:
            QMessageBox.information(self, "Import", f"No photos found on {name}.")
            return
        dialog = ImportDialog(scan, name, self._import_parents(), verify=self._settings.settings.import_verify, parent=self)
        if not dialog.exec():
            return
        photos, parent, folder_name = dialog.photos(), dialog.parent_folder(), dialog.folder_name()
        to_check = dialog.photos_to_check()
        matches = self._library.import_matches() if to_check else {}
        skipped_before = len(scan.photos) - len(photos)

        def job(worker: ScanWorker, cancel):
            wanted = list(photos)
            if to_check:
                different = importer.not_really_imported(
                    to_check,
                    matches,
                    progress=lambda done, total: worker.progress.emit(done, f"{total}\tDouble-checking {done:,} of {total:,}…"),
                    should_stop=cancel.is_set,
                )
                if cancel.is_set():
                    return None
                order = {id(p): i for i, p in enumerate(scan.photos)}
                wanted = sorted(wanted + different, key=lambda p: order[id(p)])
            skipped = len(scan.photos) - len(wanted) if skipped_before else 0
            if not wanted:
                return importer.ImportResult(folder=""), skipped
            result = importer.import_photos(
                wanted,
                parent,
                folder_name,
                progress=lambda done, total: worker.progress.emit(done, f"{total}\tImporting {done:,} of {total:,}…"),
                should_stop=cancel.is_set,
            )
            return result, skipped

        label = f"Double-checking {len(to_check):,} photos…" if to_check else f"Importing {len(photos):,} photos…"
        self._start_background_check("Import", label, job, self._on_imported)

    def _on_imported(self, outcome, stopped: bool) -> None:
        if isinstance(outcome, Exception):
            QMessageBox.warning(self, "Import", f"The import didn't work: {outcome}")
            return
        if outcome is None:  # stopped while double-checking: nothing copied
            return
        result, skipped = outcome
        if not result.folder:
            QMessageBox.information(self, "Import", f"All {skipped:,} photos are already in your library — nothing to import.")
            return
        self._library.add_folder(result.folder)
        self._refresh_sidebar()
        self._sidebar.select_folder(result.folder)
        copied = len(result.copied)
        where = short_path(result.folder)
        if result.failed:
            path, reason = result.failed[0]
            QMessageBox.warning(
                self, "Import",
                f"Imported {copied:,} into {where}, but {len(result.failed):,} couldn't be copied.\n\n{os.path.basename(path)}: {reason}",
            )
        elif result.stopped:
            self._after_scan_message = f"Import stopped — {copied:,} photo{'s' if copied != 1 else ''} copied into {where}"
        else:
            also = f" ({skipped:,} already in the library skipped)" if skipped else ""
            self._after_scan_message = f"Imported {copied:,} photo{'s' if copied != 1 else ''} into {where}{also}"
        self.rescan()

    def _add_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Add a Folder to the Collection")
        if not folder:
            return
        folder = normalise(folder)
        folders = self._settings.settings.collection_folders
        if folder not in folders:
            self._settings.update(collection_folders=[*folders, folder])
        self._refresh_sidebar()
        self._sidebar.select_folder(folder)
        self.rescan()

    def _open_settings(self) -> None:
        before = list(self._settings.settings.collection_folders)
        if SettingsDialog(self._settings, self).exec() and self._settings.settings.collection_folders != before:
            self._refresh_sidebar()
            self.rescan()

    # --- scanning -------------------------------------------------------

    def rescan(self) -> None:
        if self._scan_thread is not None or self._moving:
            self._rescan_after = True  # folders changed mid-scan: go again after
            return
        roots = list(self._settings.settings.collection_folders)
        db_path = self._library.db_path

        def job(worker: ScanWorker, cancel) -> ScanResult:
            library = Library(db_path)
            try:
                return library.scan(roots, progress=worker.progress.emit, should_stop=cancel.is_set)
            finally:
                library.close()

        thread = QThread()
        worker = ScanWorker(job)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._on_scan_progress, Qt.QueuedConnection)
        worker.done.connect(self._on_scan_done, Qt.QueuedConnection)
        worker.done.connect(thread.quit)
        self._scan_thread, self._scan_worker = thread, worker
        self._last_scan_refresh = time.monotonic()
        self._scan_label.setText("Looking for photos…")
        thread.start()
        if self._library.count() == 0:
            self._reload_photos()

    @Slot(int, str)
    def _on_scan_progress(self, count: int, folder: str) -> None:
        self._scan_label.setText(f"Reading photos… {count:,}")
        if folder:
            self._status.showMessage(folder, 3000)
        if time.monotonic() - self._last_scan_refresh >= SCAN_REFRESH_SECONDS:
            self._last_scan_refresh = time.monotonic()
            self._refresh_sidebar()
            if self._stack.currentWidget() is self._grid_page:
                self._reload_photos()

    @Slot(object)
    def _on_scan_done(self, result) -> None:
        thread = self._scan_thread
        stopped = self._scan_worker is not None and self._scan_worker.cancel_event.is_set()
        self._scan_thread = self._scan_worker = None
        if thread is not None:
            # done is delivered before the thread has been told to stop
            # (its quit is queued behind this), so ask it now — waiting
            # without that just timed out, and dropping a still-running
            # QThread aborts the app.
            thread.quit()
            thread.wait()
            thread.deleteLater()
        self._scan_label.setText("")
        if isinstance(result, Exception):
            log_event(f"scan failed: {result!r}")
            self._status.showMessage(f"Couldn't finish looking for photos: {result}", 10000)
        elif self._after_scan_message:
            # Said instead of "Collection up to date" — it's what the scan was for.
            self._status.showMessage(self._after_scan_message, 10000)
            self._after_scan_message = ""
        elif not stopped:
            parts = [f"{n:,} {label}" for n, label in ((result.added, "new"), (result.updated, "changed"), (result.removed, "removed")) if n]
            self._status.showMessage("Collection up to date" + (f": {', '.join(parts)}" if parts else ""), 8000)
        self._refresh_sidebar()
        if self._stack.currentWidget() is self._grid_page:
            self._reload_photos()
        if self._rescan_after:
            self._rescan_after = False
            self.rescan()

    def _stop_scan(self) -> None:
        if self._scan_worker is not None:
            self._scan_worker.cancel_event.set()
        if self._scan_thread is not None:
            self._scan_thread.quit()
            self._scan_thread.wait(10000)

    # --- about & updates ------------------------------------------------

    def _about(self) -> None:
        QMessageBox.about(
            self,
            "About Simple-Photos",
            f"<b>Simple-Photos</b> v{__version__}<br><br>"
            "Browse, rate and tag your photo collection, in the spirit of digiKam.<br><br>"
            "By Mike Hellyer, built with Claude Code.",
        )

    def _check_for_updates(self) -> None:
        self._runner.run(
            lambda: check_for_update(__version__, UPDATE_OWNER, UPDATE_REPO),
            self._on_update_checked,
            lambda error: None,
        )

    def _on_update_checked(self, info) -> None:
        if info:
            self._pending_update = info
            self._update_banner.announce(info.version)

    def _start_update(self) -> None:
        if self._pending_update is None:
            return
        asset_url = find_asset_for_this_platform(self._pending_update.assets)
        if asset_url is None:
            self._update_banner.set_status(f"No installer for this platform — see {self._pending_update.url}")
            return
        self._update_banner.set_busy(True, "Downloading…")
        self._runner.run(lambda: download_asset(asset_url), self._on_update_downloaded, lambda error: self._on_update_downloaded(None))

    def _on_update_downloaded(self, path) -> None:
        if path is None:
            self._update_banner.set_status("Couldn't download the update — try again")
            self._update_banner.set_busy(False)
            return
        try:
            process = launch_installer(path)
        except OSError:
            self._update_banner.set_status(f"Downloaded to {path} — open it manually")
            self._update_banner.set_busy(False)
            return

        self._update_banner.set_status("Installer launched — closing to finish…")
        if sys.platform not in ("win32", "darwin") and process is not None:
            # Linux: wait for pkexec/apt to finish before quitting — quitting
            # immediately can kill its password prompt before it's answered.
            self._runner.run(lambda: (path, self._wait_for_installer(process)), self._on_installer_finished)
        else:
            QTimer.singleShot(1500, self.close)

    @staticmethod
    def _wait_for_installer(process) -> tuple[int, str]:
        returncode = process.wait()
        stderr_text = process.stderr.read().strip() if process.stderr is not None else ""
        return returncode, stderr_text

    def _on_installer_finished(self, result) -> None:
        path, (returncode, stderr_text) = result
        if returncode == 0:
            self.close()
            return
        detail = f": {stderr_text}" if stderr_text else f" (exit code {returncode})"
        self._update_banner.set_status(f"Update didn't install{detail} — installer is at {path}")
        self._update_banner.set_busy(False)

    def closeEvent(self, event) -> None:
        self._info.save_caption()
        self._settings.save()
        self._stop_scan()
        if self._auto_worker is not None:
            self._auto_worker.cancel_event.set()
        if self._auto_thread is not None:
            self._auto_thread.quit()
            self._auto_thread.wait()
        self._model.loader.shutdown()
        self._runner.shutdown(BACKGROUND_JOIN_TIMEOUT_MS)
        self._library.close()
        super().closeEvent(event)
