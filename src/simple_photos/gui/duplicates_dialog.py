"""Find Duplicates: choosing what to look for, then reviewing what was
found before anything goes to the Trash."""
from __future__ import annotations

import os

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QRadioButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from simple_photos.core.duplicates import DEFAULT_MODE, Candidate, SearchResult
from simple_photos.core.metadata import format_size
from simple_photos.gui.folder_dialogs import short_path

MAX_GROUPS_SHOWN = 400
CARD_THUMB = 150

MODE_CHOICES = [
    ("exact", "Exact copies only", "The same file saved twice — byte for byte identical."),
    ("same", "The same photo, even resized or re-saved", "Also finds smaller copies, re-saved JPEGs, and photos sent through messaging apps."),
    ("similar", "Very similar photos", "Also near-identical shots, like a burst or a slight crop. Check these carefully."),
]


class FindDuplicatesDialog(QDialog):
    def __init__(self, shown_count: int, total_count: int, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Find Duplicates")
        self.setMinimumWidth(520)
        layout = QVBoxLayout(self)

        match = QGroupBox("Look for")
        match_layout = QVBoxLayout(match)
        self._modes = QButtonGroup(self)
        for key, title, detail in MODE_CHOICES:
            button = QRadioButton(title)
            button.setProperty("mode", key)
            button.setChecked(key == DEFAULT_MODE)
            self._modes.addButton(button)
            match_layout.addWidget(button)
            note = QLabel(detail)
            note.setWordWrap(True)
            note.setStyleSheet("color: palette(mid); margin-left: 24px;")
            match_layout.addWidget(note)
        layout.addWidget(match)

        scope = QGroupBox("Look in")
        scope_layout = QVBoxLayout(scope)
        self._everything = QRadioButton(f"The whole collection ({total_count:,} photos)")
        self._shown = QRadioButton(f"Just the photos shown now ({shown_count:,})")
        # Just the photos shown is the default — unless they're all of them
        # (or none), when the two choices are the same thing.
        narrower = 0 < shown_count < total_count
        self._shown.setEnabled(narrower)
        (self._shown if narrower else self._everything).setChecked(True)
        scope_layout.addWidget(self._everything)
        scope_layout.addWidget(self._shown)
        layout.addWidget(scope)

        note = QLabel("The first search reads every photo, which can take a while; later ones only look at new or changed photos.")
        note.setWordWrap(True)
        note.setStyleSheet("color: palette(mid);")
        layout.addWidget(note)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Search")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def mode(self) -> str:
        return self._modes.checkedButton().property("mode")

    def only_shown(self) -> bool:
        return self._shown.isChecked()


class _Card(QFrame):
    """One photo in a group: its thumbnail, details and a Trash tick."""

    def __init__(self, photo: Candidate, thumbnail: str | None, keep: bool, on_change):
        super().__init__()
        self.photo = photo
        self.setFrameShape(QFrame.StyledPanel)
        self.setFixedWidth(CARD_THUMB + 40)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        image = QLabel()
        image.setAlignment(Qt.AlignCenter)
        image.setFixedSize(CARD_THUMB, CARD_THUMB)
        pixmap = QPixmap(thumbnail) if thumbnail else QPixmap()
        if not pixmap.isNull():
            image.setPixmap(pixmap.scaled(CARD_THUMB, CARD_THUMB, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        image.setToolTip(f"{photo.path}\n\nDouble-click to open it")
        image.setCursor(Qt.PointingHandCursor)
        image.mouseDoubleClickEvent = lambda event: QDesktopServices.openUrl(QUrl.fromLocalFile(photo.path))
        layout.addWidget(image, alignment=Qt.AlignCenter)

        name = QLabel(f"<b>{os.path.basename(photo.path)}</b>")
        name.setWordWrap(True)
        layout.addWidget(name)
        details = QLabel(
            f"{short_path(os.path.dirname(photo.path))}<br>"
            f"{photo.width} × {photo.height} · {format_size(photo.file_size)}"
            + (f"<br>{'★' * photo.rating}" if photo.rating else "")
        )
        details.setWordWrap(True)
        details.setStyleSheet("color: palette(mid);")
        layout.addWidget(details)

        self.trash = QCheckBox("Move to Trash")
        self.trash.setChecked(not keep)
        self.trash.toggled.connect(lambda _: (self._update_look(), on_change()))
        layout.addWidget(self.trash)
        self._update_look()

    def _update_look(self) -> None:
        self.setStyleSheet(
            "_Card { background: rgba(192, 57, 43, 0.10); }" if self.trash.isChecked()
            else "_Card { background: rgba(39, 174, 96, 0.12); }"
        )


class DuplicatesDialog(QDialog):
    """Groups of duplicates, the best copy of each unticked. chosen() gives
    (keep ids, trash ids) per group."""

    def __init__(self, result: SearchResult, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Duplicates")
        self.resize(980, 700)
        layout = QVBoxLayout(self)
        groups = result.groups[:MAX_GROUPS_SHOWN]
        copies = sum(len(g) - 1 for g in result.groups)
        intro = QLabel(
            f"Found {len(result.groups):,} group{'s' if len(result.groups) != 1 else ''} of duplicates among "
            f"{result.checked:,} photos — {copies:,} extra cop{'ies' if copies != 1 else 'y'}. In each group the best copy "
            "(highest resolution, then largest file, then the one you've rated or tagged) is kept, and the rest are "
            "ticked to go to the Trash. Change any tick you like. Ratings, tags and captions from the photos you remove "
            "are added to the one you keep."
            + (f"<br><br>Showing the first {MAX_GROUPS_SHOWN:,} groups; search again afterwards for the rest." if len(result.groups) > MAX_GROUPS_SHOWN else "")
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        body = QWidget()
        body_layout = QVBoxLayout(body)
        self._groups: list[list[_Card]] = []
        for number, group in enumerate(groups, start=1):
            box = QGroupBox(f"{number}. {os.path.basename(group[0].path)} — {len(group)} copies")
            row = QHBoxLayout(box)
            cards = [_Card(photo, result.thumbnails.get(photo.id), keep=(i == 0), on_change=self._update) for i, photo in enumerate(group)]
            for card in cards:
                row.addWidget(card)
            row.addStretch(1)
            body_layout.addWidget(box)
            self._groups.append(cards)
        body_layout.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(body)
        layout.addWidget(scroll, 1)

        self._summary = QLabel()
        self._summary.setWordWrap(True)
        layout.addWidget(self._summary)
        self._buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)
        layout.addWidget(self._buttons)
        self._update()

    def chosen(self) -> list[tuple[list[int], list[int]]]:
        """(keep ids, trash ids) for each group with something to trash."""
        out = []
        for cards in self._groups:
            trash = [c.photo.id for c in cards if c.trash.isChecked()]
            keep = [c.photo.id for c in cards if not c.trash.isChecked()]
            if trash and keep:
                out.append((keep, trash))
        return out

    def _update(self) -> None:
        all_ticked = [i + 1 for i, cards in enumerate(self._groups) if all(c.trash.isChecked() for c in cards)]
        chosen = [card for cards in self._groups for card in cards if card.trash.isChecked()]
        ok = self._buttons.button(QDialogButtonBox.Ok)
        ok.setText(f"Move {len(chosen):,} to Trash")
        if all_ticked:
            groups = ", ".join(map(str, all_ticked[:5])) + ("…" if len(all_ticked) > 5 else "")
            self._summary.setText(f"<span style='color:#c0392b'>Keep at least one photo in each group (group {groups}).</span>")
            ok.setEnabled(False)
            return
        freed = sum(card.photo.file_size for card in chosen)
        self._summary.setText(f"{len(chosen):,} photo{'s' if len(chosen) != 1 else ''} will go to the Trash, freeing {format_size(freed)}." if chosen else "Nothing ticked.")
        ok.setEnabled(bool(chosen))
