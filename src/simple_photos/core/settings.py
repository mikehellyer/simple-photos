"""Persisted user preferences: which folders make up the collection, and
how the browser was last laid out."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Optional

from platformdirs import user_config_dir, user_pictures_dir

from simple_photos.core.library import SORTS

THUMB_MIN, THUMB_MAX, THUMB_DEFAULT = 96, 320, 160


def _default_pictures_dir() -> str:
    try:
        return user_pictures_dir()
    except Exception:  # platformdirs can trip over odd XDG setups
        return str(Path.home() / "Pictures")


@dataclass
class Settings:
    collection_folders: list[str] = field(default_factory=lambda: [_default_pictures_dir()])
    thumb_size: int = THUMB_DEFAULT
    sort: str = "taken"
    sort_descending: bool = False
    show_info_panel: bool = True
    scan_on_startup: bool = True
    import_folder: str = ""  # where imports go by default; "" = the first collection folder
    import_verify: bool = False  # byte-for-byte check before skipping photos already imported
    import_period_unit: str = "all"  # import only photos taken in the last … "days" / "weeks" / "months"
    import_period_count: int = 7
    last_backups: dict[str, str] = field(default_factory=dict)  # backup drive's name → when (ISO), to show next time


class SettingsStore:
    def __init__(self, config_path: Optional[Path] = None):
        self._config_path = config_path or Path(user_config_dir("Simple-Photos")) / "settings.json"
        self.settings = Settings()
        self.load()

    def update(self, **changes) -> None:
        for name, value in changes.items():
            if not hasattr(self.settings, name):
                raise AttributeError(name)
            setattr(self.settings, name, value)
        self.save()

    def load(self) -> None:
        try:
            data = json.loads(self._config_path.read_text())
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(data, dict):
            return
        defaults = Settings()
        for f in fields(Settings):
            if f.name not in data:
                continue
            value, default = data[f.name], getattr(defaults, f.name)
            if f.name == "collection_folders":
                if isinstance(value, list):
                    self.settings.collection_folders = [v for v in value if isinstance(v, str) and v.strip()]
            elif f.name == "last_backups":
                if isinstance(value, dict):
                    self.settings.last_backups = {k: v for k, v in value.items() if isinstance(k, str) and isinstance(v, str)}
            elif type(value) is type(default):
                setattr(self.settings, f.name, value)
        self.settings.thumb_size = max(THUMB_MIN, min(THUMB_MAX, self.settings.thumb_size))
        if self.settings.sort not in SORTS:
            self.settings.sort = "taken"
        if self.settings.import_period_unit not in ("all", "days", "weeks", "months"):
            self.settings.import_period_unit = "all"
        self.settings.import_period_count = max(1, min(999, self.settings.import_period_count))

    def save(self) -> None:
        try:
            self._config_path.parent.mkdir(parents=True, exist_ok=True)
            self._config_path.write_text(json.dumps(asdict(self.settings), indent=2))
        except OSError:
            pass
