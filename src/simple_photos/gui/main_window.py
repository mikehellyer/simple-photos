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
from simple_photos.core.fileops import move_photos, reveal_in_file_manager
from simple_photos.core.library import Library, PhotoFilter, ScanResult
from simple_photos.core.self_update import download_asset, find_asset_for_this_platform, launch_installer
from simple_photos.core.settings import THUMB_MAX, THUMB_MIN, SettingsStore
from simple_photos.core.thumbnails import move_thumbnail
from simple_photos.core.updater import API_TIMEOUT_SECONDS, check_for_update
from simple_photos.gui.folder_dialogs import MoveToFolderDialog, make_new_folder, short_path
from simple_photos.gui.info_panel import InfoPanel
from simple_photos.gui.photo_grid import PhotoGrid, PhotoModel
from simple_photos.gui.settings_dialog import SettingsDialog, normalise
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
        self._moving = False

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
        self._sidebar.reveal_requested.connect(lambda folder: QDesktopServices.openUrl(QUrl.fromLocalFile(folder)))
        self._splitter.addWidget(self._sidebar)

        self._stack = QStackedWidget()
        self._model = PhotoModel(self)
        self._grid_page = self._build_grid_page()
        self._stack.addWidget(self._grid_page)
        self._viewer = ViewerPage(self._runner)
        self._viewer.set_thumbnail_source(self._model.cached_pixmap)
        self._viewer.close_requested.connect(self._viewer_escape)
        self._viewer.current_changed.connect(self._on_viewer_moved)
        self._viewer.rate_requested.connect(lambda rating: self._rate_photos(self._viewer_ids(), rating))
        self._viewer.fullscreen_toggled.connect(self._toggle_fullscreen)
        self._viewer.position_changed.connect(self._show_viewer_position)
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
        action(file_menu, "New Folder…", "Ctrl+Shift+N", lambda: self._new_folder(None))
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
        rate_menu = photo_menu.addMenu("Rate")
        for rating in range(6):
            action(rate_menu, "No rating" if rating == 0 else "★" * rating, f"Ctrl+{rating}", lambda r=rating: self._rate_photos(self._active_ids(), r))
        action(photo_menu, "Add Tag…", "Ctrl+T", self._focus_tags)
        photo_menu.addSeparator()
        trash_action = action(photo_menu, "Move to Trash", QKeySequence.Delete, self._trash, local=True)
        for act in (view_action, open_action, reveal_action, move_action):
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
        if self._moving:
            self._status.showMessage("Still moving the last photos — try again in a moment", 5000)
            return
        if self._scan_thread is not None:
            # A scan running alongside could see a photo vanish from its
            # old folder and drop its tags. Stop it (it then removes
            # nothing) and look again once the move is done.
            self._stop_scan()
            self._rescan_after = True
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
        if self._rescan_after and self._scan_thread is None:
            self._rescan_after = False
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
        self._model.loader.shutdown()
        self._runner.shutdown(BACKGROUND_JOIN_TIMEOUT_MS)
        self._library.close()
        super().closeEvent(event)
