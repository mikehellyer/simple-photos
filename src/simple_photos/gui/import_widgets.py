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
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from simple_photos.core.devices import Device, Volume, pick_devices
from simple_photos.core.fileops import folder_name_problem, free_name
from simple_photos.core.importer import DeviceScan, cutoff_date, default_folder_name, taken_since
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
    backup_requested = Signal(object)  # Device

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
        buttons = QHBoxLayout()
        buttons.setSpacing(4)
        self._button = QPushButton("Import Photos…")
        self._button.clicked.connect(self._import_selected)
        buttons.addWidget(self._button, 1)
        self._backup_button = QPushButton("Back Up…")
        self._backup_button.setToolTip("Back up your collection folders to this drive")
        self._backup_button.clicked.connect(self._backup_selected)
        buttons.addWidget(self._backup_button)
        layout.addLayout(buttons)
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
        device = self._selected()
        self._button.setEnabled(device is not None)
        # Only drives: a camera or phone isn't somewhere to keep a backup.
        self._backup_button.setEnabled(device is not None and device.kind == "drive")

    def _backup_selected(self) -> None:
        device = self._selected()
        if device is not None and device.kind == "drive":
            self.backup_requested.emit(device)

    def _import_selected(self) -> None:
        device = self._selected()
        if device is not None:
            self.import_requested.emit(device)


def _nice_date(iso: str, weekday: bool = False) -> str:
    try:
        day = _dt.date.fromisoformat(iso)
    except ValueError:
        return iso
    text = f"{day.day} {day.strftime('%B %Y')}"
    return f"{day.strftime('%A')} {text}" if weekday else text


class ImportDialog(QDialog):
    """What was found on the device, and where it's going."""

    def __init__(
        self,
        scan: DeviceScan,
        source_name: str,
        parents: list[str],
        verify: bool = False,
        period: tuple[str, int] = ("all", 7),
        parent=None,
    ):
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

        # Which photos, by when they were taken.
        unit, number = period
        period_row = QHBoxLayout()
        period_row.addWidget(QLabel("Import:"))
        self._scope = QComboBox()
        self._scope.addItems(["All photos", "Photos taken in the last"])
        self._scope.setCurrentIndex(0 if unit == "all" else 1)
        period_row.addWidget(self._scope)
        self._count = QSpinBox()
        self._count.setRange(1, 999)
        self._count.setValue(number)
        period_row.addWidget(self._count)
        self._unit = QComboBox()
        for key in ("days", "weeks", "months"):
            self._unit.addItem(key, key)
        self._unit.setCurrentIndex(max(0, self._unit.findData(unit)))
        period_row.addWidget(self._unit)
        period_row.addStretch(1)
        layout.addLayout(period_row)
        self._period_note = QLabel()
        self._period_note.setStyleSheet("color: palette(mid);")
        layout.addWidget(self._period_note)
        for widget in (self._scope, self._unit):
            widget.currentIndexChanged.connect(self._update)
        self._count.valueChanged.connect(self._update)

        already = count - len(scan.new_photos)
        self._some_already = already > 0
        self._skip = QCheckBox()
        self._skip.setChecked(True)
        self._skip.setVisible(already > 0)
        self._skip.toggled.connect(self._update)
        layout.addWidget(self._skip)
        self._verify = QCheckBox("Double-check them byte for byte first (slower)")
        self._verify.setToolTip(
            "They look already imported because a photo with the same file size and date taken is in the "
            "library. Tick this to compare the files too, and import any that turn out to be different."
        )
        self._verify.setChecked(verify)
        self._verify.setVisible(already > 0)
        self._verify.setStyleSheet("margin-left: 22px;")
        self._verify.toggled.connect(self._update)
        layout.addWidget(self._verify)

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
        self._delete_after = QCheckBox(f"Delete them from {source_name} after importing")
        self._delete_after.setToolTip(
            "Once the import has finished you'll be asked to confirm. Each photo is only deleted after its "
            "copy has been checked byte for byte; photos that weren't imported this time are left alone."
        )
        self._delete_after.toggled.connect(self._update)
        layout.addWidget(self._delete_after)
        self._note = QLabel()
        self._note.setWordWrap(True)
        self._note.setStyleSheet("color: palette(mid);")
        layout.addWidget(self._note)

        self._buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)
        layout.addWidget(self._buttons)
        self._name.selectAll()
        self._update()

    def period(self) -> tuple[str, int]:
        """("all" / "days" / "weeks" / "months", how many) — to remember."""
        unit = "all" if self._scope.currentIndex() == 0 else self._unit.currentData()
        return unit, self._count.value()

    def in_range(self):
        """The photos taken in the chosen period (all of them for "All")."""
        return taken_since(self._scan.photos, cutoff_date(self._count.value(), self.period()[0]))

    def photos(self):
        """The photos to copy (before any byte-for-byte check)."""
        # (Not isVisible(): once the dialog closes, nothing in it is visible.)
        skipping = self._skipping()
        return [p for p in self.in_range() if not (skipping and p.already_imported)]

    def _skipping(self) -> bool:
        return self._some_already and self._skip.isChecked()

    def photos_to_check(self):
        """The photos that look already imported, if they're to be checked
        byte for byte before being skipped."""
        if self._skipping() and self._verify.isChecked():
            return [p for p in self.in_range() if p.already_imported]
        return []

    def parent_folder(self) -> str:
        return self._parent.currentData()

    def delete_after(self) -> bool:
        return self._delete_after.isChecked()

    def folder_name(self) -> str:
        return self._name.text().strip()

    def _update(self) -> None:
        ok = self._buttons.button(QDialogButtonBox.Ok)
        unit, number = self.period()
        for widget in (self._count, self._unit):
            widget.setVisible(unit != "all")
        for i, key in enumerate(("days", "weeks", "months")):
            self._unit.setItemText(i, key[:-1] if number == 1 else key)  # "1 day", "2 days"
        in_range = self.in_range()
        cutoff = cutoff_date(number, unit)
        if cutoff is None:
            self._period_note.hide()
        else:
            total = len(self._scan.photos)
            self._period_note.setText(
                f"Taken on or after {_nice_date(cutoff.isoformat(), weekday=True)} — {len(in_range):,} of {total:,} photos"
            )
            self._period_note.show()
        already = sum(p.already_imported for p in in_range)
        self._skip.setText(f"Skip the {already:,} already in your library" if already != 1 else "Skip the 1 already in your library")
        count = len(self.photos())
        checking = len(self.photos_to_check())
        self._verify.setEnabled(self._skip.isChecked())
        name = self.folder_name()
        problem = folder_name_problem(name) if name else "Type a name for the new folder."
        if problem:
            self._where.setText(f"<span style='color:#c0392b'>{problem}</span>")
        elif not in_range:
            self._where.setText("None were taken in that time.")
        elif count == 0 and not checking:
            self._where.setText("They're all in your library already.")
        else:
            parent = self.parent_folder() or ""
            actual = free_name(parent, name) if parent and os.path.isdir(parent) else name
            self._where.setText(f"They'll be copied to {short_path(os.path.join(parent, actual))}")
        if checking:
            ok.setText(f"Check {checking:,} and Import")
        else:
            ok.setText(f"Import {count:,} Photo{'s' if count != 1 else ''}")
        ok.setEnabled((count > 0 or checking > 0) and not problem and bool(self.parent_folder()))
        self._note.setText(
            "You'll be asked to confirm before anything is deleted, once the photos are safely copied."
            if self._delete_after.isChecked()
            else "The photos are copied — nothing on the device is changed or deleted."
        )
