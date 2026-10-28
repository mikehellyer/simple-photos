"""Setting up a backup: what will be copied, and is there room."""
from __future__ import annotations

import datetime as _dt

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCheckBox, QDialog, QDialogButtonBox, QLabel, QListWidget, QListWidgetItem, QVBoxLayout

from simple_photos.core.backup import BACKUP_FOLDER, REMOVED_FOLDER, FileToCopy, FolderPlan, Move
from simple_photos.core.metadata import format_size
from simple_photos.gui.folder_dialogs import short_path


def nice_when(iso: str) -> str:
    try:
        when = _dt.datetime.fromisoformat(iso)
    except ValueError:
        return iso
    return f"{when.strftime('%A')} {when.day} {when.strftime('%B %Y, %H:%M')}"


class BackupDialog(QDialog):
    def __init__(self, plans: list[FolderPlan], name: str, free: int | None, last_backup: str | None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Back Up to {name}")
        self.setMinimumWidth(600)
        self._plans = plans
        self._free = free
        self._name = name
        layout = QVBoxLayout(self)

        intro = QLabel(
            f"Your collection folders are backed up to <b>{name}</b>, into “{BACKUP_FOLDER}”. "
            "Only new or changed files are copied, and moves and renames are followed. Nothing you've "
            "backed up is ever deleted from it."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)
        last = QLabel(f"Last backed up here: {nice_when(last_backup)}" if last_backup else "Not backed up here before.")
        last.setStyleSheet("color: palette(mid);")
        layout.addWidget(last)

        self._list = QListWidget()
        for plan in plans:
            parts = []
            if plan.to_copy:
                parts.append(f"{len(plan.to_copy):,} to copy ({format_size(plan.bytes_to_copy)})")
            if plan.moves:
                parts.append(f"{len(plan.moves):,} moved or renamed (rearranged in the backup, no copying)")
            parts.append(f"{plan.up_to_date:,} already backed up" if parts else f"up to date ({plan.up_to_date:,} files)")
            detail = ", ".join(parts)
            item = QListWidgetItem(f"{short_path(plan.root)} — {detail}")
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked)
            item.setData(Qt.UserRole, plan)
            self._list.addItem(item)
        self._list.setMaximumHeight(max(60, 26 * len(plans) + 8))
        self._list.itemChanged.connect(self._update)
        layout.addWidget(self._list)

        self._library = QCheckBox("Also save your ratings, tags and captions (a copy of Simple-Photos' library)")
        self._library.setChecked(True)
        self._library.toggled.connect(self._update)
        layout.addWidget(self._library)

        removed = sum(len(p.removed) for p in plans)
        leftovers = sum(len(p.leftovers) for p in plans)
        tidy_parts = []
        if removed:
            tidy_parts.append(
                f"put the {removed:,} file{'s' if removed != 1 else ''} you've deleted from this computer into "
                f"“{REMOVED_FOLDER}” (kept there, not deleted)"
            )
        if leftovers:
            tidy_parts.append(
                f"remove {leftovers:,} leftover cop{'ies' if leftovers != 1 else 'y'} from earlier backups "
                "(the same files are backed up at their current places)"
            )
        self._tidy = QCheckBox("Tidy up the backup")
        self._tidy.setVisible(bool(tidy_parts))
        self._tidy.setChecked(False)
        self._tidy.toggled.connect(self._update)
        layout.addWidget(self._tidy)
        sentence = " and ".join(tidy_parts)
        tidy_note = QLabel(sentence[:1].upper() + sentence[1:] + "." if tidy_parts else "")
        tidy_note.setWordWrap(True)
        tidy_note.setStyleSheet("color: palette(mid); margin-left: 22px;")
        tidy_note.setVisible(bool(tidy_parts))
        layout.addWidget(tidy_note)

        self._summary = QLabel()
        self._summary.setWordWrap(True)
        layout.addWidget(self._summary)

        self._buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)
        layout.addWidget(self._buttons)
        self._update()

    def files(self) -> list[FileToCopy]:
        items = (self._list.item(i) for i in range(self._list.count()))
        return [f for item in items if item.checkState() == Qt.Checked for f in item.data(Qt.UserRole).to_copy]

    def save_library(self) -> bool:
        return self._library.isChecked()

    def _chosen(self) -> list[FolderPlan]:
        items = (self._list.item(i) for i in range(self._list.count()))
        return [item.data(Qt.UserRole) for item in items if item.checkState() == Qt.Checked]

    def chosen_plans(self) -> list[FolderPlan]:
        return self._chosen()

    def moves(self) -> list[Move]:
        return [m for plan in self._chosen() for m in plan.moves]

    def tidy(self) -> bool:
        return self._tidy.isVisible() and self._tidy.isChecked()

    def _update(self) -> None:
        files = self.files()
        needed = sum(f.size for f in files)
        ok = self._buttons.button(QDialogButtonBox.Ok)
        room = f" {format_size(self._free)} free on {self._name}." if self._free is not None else ""
        if files and self._free is not None and needed > self._free:
            self._summary.setText(
                f"<span style='color:#c0392b'>Not enough room: this needs {format_size(needed)}, and {self._name} has "
                f"{format_size(self._free)} free. Untick a folder, or free some space on it.</span>"
            )
            ok.setText("Back Up")
            ok.setEnabled(False)
        elif files:
            self._summary.setText(f"{len(files):,} file{'s' if len(files) != 1 else ''} to copy ({format_size(needed)}).{room}")
            ok.setText(f"Back Up {len(files):,} File{'s' if len(files) != 1 else ''}")
            ok.setEnabled(True)
        elif self.moves() or self.tidy():
            self._summary.setText("Nothing needs copying — the backup just needs rearranging." + room)
            ok.setText("Update Backup")
            ok.setEnabled(True)
        else:
            self._summary.setText("Everything ticked is already backed up." + room)
            ok.setText("Save Ratings and Tags" if self._library.isChecked() else "Back Up")
            ok.setEnabled(self._library.isChecked())
