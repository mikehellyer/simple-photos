"""Setting up a restore: what's missing from the computer, and where it
goes back to."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCheckBox, QDialog, QDialogButtonBox, QLabel, QListWidget, QListWidgetItem, QVBoxLayout

from simple_photos.core.backup import FileToCopy
from simple_photos.core.metadata import format_size
from simple_photos.core.restore import RestorePlan
from simple_photos.gui.folder_dialogs import short_path


class RestoreDialog(QDialog):
    def __init__(self, plans: list[RestorePlan], name: str, free: int | None, has_library: bool, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Restore from {name}")
        self.setMinimumWidth(640)
        self._free = free
        layout = QVBoxLayout(self)
        intro = QLabel(
            f"Photos in the backup on <b>{name}</b> that are missing from this computer are copied back. "
            "Nothing on this computer is overwritten or deleted: where a file is different here, "
            "this computer's version is kept."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self._list = QListWidget()
        for plan in plans:
            where = short_path(plan.target) + ("" if plan.matched else " (a new folder)")
            parts = [f"{len(plan.to_copy):,} missing ({format_size(plan.bytes_to_copy)})" if plan.to_copy else "nothing missing"]
            if plan.already_here:
                parts.append(f"{plan.already_here:,} already here")
            if plan.different:
                parts.append(f"{plan.different:,} different here (kept)")
            item = QListWidgetItem(f"{plan.name} → {where}: {', '.join(parts)}")
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked)
            item.setData(Qt.UserRole, plan)
            self._list.addItem(item)
        self._list.setMaximumHeight(max(60, 26 * len(plans) + 8))
        self._list.itemChanged.connect(self._update)
        layout.addWidget(self._list)

        self._library = QCheckBox(
            "Also bring back ratings, tags and captions (added to what's here — nothing is removed)"
        )
        self._library.setChecked(has_library)
        self._library.setEnabled(has_library)
        if not has_library:
            self._library.setText("This backup has no copy of your ratings, tags and captions")
        self._library.toggled.connect(self._update)
        layout.addWidget(self._library)

        self._summary = QLabel()
        self._summary.setWordWrap(True)
        layout.addWidget(self._summary)
        self._buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)
        layout.addWidget(self._buttons)
        self._update()

    def plans(self) -> list[RestorePlan]:
        items = (self._list.item(i) for i in range(self._list.count()))
        return [item.data(Qt.UserRole) for item in items if item.checkState() == Qt.Checked]

    def files(self) -> list[FileToCopy]:
        return [f for plan in self.plans() for f in plan.to_copy]

    def restore_library(self) -> bool:
        return self._library.isEnabled() and self._library.isChecked()

    def _update(self) -> None:
        files = self.files()
        needed = sum(f.size for f in files)
        ok = self._buttons.button(QDialogButtonBox.Ok)
        room = f" {format_size(self._free)} free on this computer." if self._free is not None else ""
        if files and self._free is not None and needed > self._free:
            self._summary.setText(
                f"<span style='color:#c0392b'>Not enough room: this needs {format_size(needed)}, and this computer "
                f"has {format_size(self._free)} free.</span>"
            )
            ok.setText("Restore")
            ok.setEnabled(False)
        elif files:
            self._summary.setText(f"{len(files):,} file{'s' if len(files) != 1 else ''} to copy back ({format_size(needed)}).{room}")
            ok.setText(f"Restore {len(files):,} File{'s' if len(files) != 1 else ''}")
            ok.setEnabled(True)
        else:
            self._summary.setText("Nothing ticked is missing from this computer.")
            ok.setText("Restore Ratings and Tags" if self.restore_library() else "Restore")
            ok.setEnabled(self.restore_library() and bool(self.plans()))
