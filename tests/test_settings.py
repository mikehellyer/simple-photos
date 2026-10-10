import json

from simple_photos.core.settings import THUMB_MAX, SettingsStore


def test_defaults_to_the_pictures_folder(tmp_path):
    store = SettingsStore(tmp_path / "settings.json")
    assert len(store.settings.collection_folders) == 1
    assert store.settings.collection_folders[0].endswith("Pictures")


def test_round_trip(tmp_path):
    path = tmp_path / "settings.json"
    SettingsStore(path).update(collection_folders=["/a", "/b"], thumb_size=200, sort="name")
    loaded = SettingsStore(path).settings
    assert loaded.collection_folders == ["/a", "/b"]
    assert loaded.thumb_size == 200 and loaded.sort == "name"


def test_bad_values_fall_back(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"thumb_size": 99999, "sort": "chaos", "collection_folders": "nope", "scan_on_startup": "yes"}))
    s = SettingsStore(path).settings
    assert s.thumb_size == THUMB_MAX
    assert s.sort == "taken"
    assert s.collection_folders[0].endswith("Pictures")
    assert s.scan_on_startup is True
