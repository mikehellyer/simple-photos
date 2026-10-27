"""Importing from USB sticks, SD cards, drives and cameras: noticing
them being plugged in, the sidebar's Devices panel, and the dialog that
sets up an import."""
from __future__ import annotations

import datetime as _dt
import os

from PySide6.QtCore import QObject, QStorageInfo, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from simple_photos.core.devices import Device, Volume, pick_devices
from simple_photos.core.fileops import folder_name_problem, free_name
from simple_photos.core.importer import DeviceScan, default_folder_name
from simple_photos.core.metadata import format_size
from simple_photos.gui.folder_dialogs import short_path

POLL_MS = 2500

ICONS = {"drive": "💾", "camera": "📷"}


def current_devices() -> list[Device]:
    volumes = []
    for storage in QStorageInfo.mountedVolumes():
        if storage.isValid():
            volumes.append(Volume(root=storage.rootPath(), label=storage.name(), read_only=storage.isReadOnly()))
    return pick_devices(volumes)


class DeviceWatcher(QObject):
    """Checks every couple of seconds what's plugged in. changed(devices)
    when the list changes; arrived(device) for each newly plugged-in one."""

    changed = Signal(list)
    arrived = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.devices: list[Device] = current_devices()
        self._timer = QTimer(self)
        self._timer.setInterval(POLL_MS)
        self._timer.timeout.connect(self.check)
        self._timer.start()

    def check(self) -> None:
        try:
            devices = current_devices()
        except Exception:  # a flaky mount shouldn't upset anything
            return
        if devices == self.devices:
            return
        before = {d.path for d in self.devices}
        self.devices = devices
        self.changed.emit(devices)
        for device in devices:
            if device.path not in before:
                self.arrived.emit(device)


class DevicesPanel(QWidget):
    """The list of plugged-in devices under the sidebar; hidden when
    there are none."""

    import_requested = Signal(object)  # Device

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 6, 0, 0)
        title = QLabel("DEVICES")
        title.setStyleSheet("font-weight: 600; font-size: 11px; color: palette(mid); padding-left: 4px;")
        layout.addWidget(title)
        self._list = QListWidget()
        self._list.itemDoubleClicked.connect(lambda item: self.import_requested.emit(item.data(Qt.UserRole)))
        self._list.itemSelectionChanged.connect(self._update_button)
        layout.addWidget(self._list)
        self._button = QPushButton("Import Photos…")
        self._button.clicked.connect(self._import_selected)
        layout.addWidget(self._button)
        self.set_devices([])

    def set_devices(self, devices: list[Device]) -> None:
        selected = self._selected()
        self._list.clear()
        for device in devices:
            item = QListWidgetItem(f"{ICONS.get(device.kind, '💾')}  {device.description}")
            item.setToolTip(f"{device.path}\nDouble-click to import its photos")
            item.setData(Qt.UserRole, device)
            self._list.addItem(item)
            if selected and device.path == selected.path:
                item.setSelected(True)
        if devices and not self._list.selectedItems():
            self._list.item(0).setSelected(True)
        rows = max(1, min(4, len(devices)))  # just tall enough, up to four
        self._list.setFixedHeight(rows * self._list.sizeHintForRow(0) + 2 * self._list.frameWidth() + 4 if devices else 0)
        self.setVisible(bool(devices))
        self._update_button()

    def _selected(self) -> Device | None:
        items = self._list.selectedItems()
        return items[0].data(Qt.UserRole) if items else None

    def _update_button(self) -> None:
        self._button.setEnabled(self._selected() is not None)

    def _import_selected(self) -> None:
        device = self._selected()
        if device is not None:
            self.import_requested.emit(device)


def _nice_date(iso: str) -> str:
    try:
        day = _dt.date.fromisoformat(iso)
    except ValueError:
        return iso
    return f"{day.day} {day.strftime('%B %Y')}"


class ImportDialog(QDialog):
    """What was found on the device, and where it's going."""

    def __init__(self, scan: DeviceScan, source_name: str, parents: list[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Import from {source_name}")
        self.setMinimumWidth(560)
        self._scan = scan
        layout = QVBoxLayout(self)

        count = len(scan.photos)
        dates = scan.date_range()
        when = ""
        if dates:
            when = f", taken {_nice_date(dates[0])}" if dates[0] == dates[1] else f", taken {_nice_date(dates[0])} – {_nice_date(dates[1])}"
        found = QLabel(
            f"<b>{count:,} photo{'s' if count != 1 else ''}</b> on {source_name} ({format_size(scan.total_size)}){when}."
        )
        found.setWordWrap(True)
        layout.addWidget(found)

        already = count - len(scan.new_photos)
        self._some_already = already > 0
        self._skip = QCheckBox(f"Skip the {already:,} already in your library" if already != 1 else "Skip the 1 already in your library")
        self._skip.setChecked(True)
        self._skip.setVisible(already > 0)
        self._skip.toggled.connect(self._update)
        layout.addWidget(self._skip)

        form = QFormLayout()
        self._name = QLineEdit(default_folder_name(source_name))
        self._name.textChanged.connect(self._update)
        form.addRow("New folder:", self._name)
        self._parent = QComboBox()
        for folder in parents:
            self._parent.addItem(short_path(folder), folder)
        self._parent.currentIndexChanged.connect(self._update)
        form.addRow("Inside:", self._parent)
        layout.addLayout(form)

        self._where = QLabel()
        self._where.setWordWrap(True)
        self._where.setStyleSheet("color: palette(mid);")
        layout.addWidget(self._where)
        note = QLabel("The photos are copied — nothing on the device is changed or deleted.")
        note.setWordWrap(True)
        note.setStyleSheet("color: palette(mid);")
        layout.addWidget(note)

        self._buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)
        layout.addWidget(self._buttons)
        self._name.selectAll()
        self._update()

    def photos(self):
        # (Not isVisible(): once the dialog closes, nothing in it is visible.)
        return self._scan.new_photos if self._some_already and self._skip.isChecked() else self._scan.photos

    def parent_folder(self) -> str:
        return self._parent.currentData()

    def folder_name(self) -> str:
        return self._name.text().strip()

    def _update(self) -> None:
        ok = self._buttons.button(QDialogButtonBox.Ok)
        count = len(self.photos())
        name = self.folder_name()
        problem = folder_name_problem(name) if name else "Type a name for the new folder."
        if problem:
            self._where.setText(f"<span style='color:#c0392b'>{problem}</span>")
        elif count == 0:
            self._where.setText("They're all in your library already.")
        else:
            parent = self.parent_folder() or ""
            actual = free_name(parent, name) if parent and os.path.isdir(parent) else name
            self._where.setText(f"They'll be copied to {short_path(os.path.join(parent, actual))}")
        ok.setText(f"Import {count:,} Photo{'s' if count != 1 else ''}")
        ok.setEnabled(count > 0 and not problem and bool(self.parent_folder()))
