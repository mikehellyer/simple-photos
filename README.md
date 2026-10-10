# Simple-Photos

A simple photo manager for Linux, macOS, and Windows, in the spirit of
digiKam. It's a sister app to Simple-Streamer, Simple-Jukebox and
Simple-Ripper.

Created by [Mike Hellyer](https://github.com/mikehellyer), built with
[Claude Code](https://claude.com/claude-code).

## Status

**Your collection.** Simple-Photos shows every photo in your collection
folders and all the folders inside them. The first folder is your Pictures
folder, and you can add more with **File ▸ Add Folder to Collection…** or in
**Settings**. Your files stay where they are. Simple-Photos keeps a catalogue of
them in its own library, along with your ratings, tags and captions. On each
start it looks for new and changed photos, and **F5** does the same any time.
It only re-reads files that have changed, so a big collection stays quick.
If a folder is on a drive that's unplugged, its photos and their tags are
kept until it's back.

**Browsing.** The sidebar has three ways in:

- **Folders**: your folder tree, with photo counts. Tick or untick *Include
  subfolders* to choose whether a folder's subfolders are shown too.
- **Dates**: by year and month, using when the photo was taken (the
  camera's EXIF date, or the file's date if there isn't one).
- **Tags**: everything you've tagged, plus *Untagged*. Right-click a tag
  to rename it (renaming onto an existing tag merges the two) or delete it.

The grid above the photos has a search box (it matches file names, captions,
tags and camera models), a minimum-rating filter, sorting (date taken, name,
rating, date added, size) and a thumbnail-size slider. Thumbnails are made in
the background and cached, so they appear instantly the next time.

**Rating, tagging, captions.** Select one or many photos, then:

- press **0–5** to rate them (or click the stars in the info panel),
- type a tag in the info panel and press Enter. Commas add several at once,
  and **Ctrl+T** jumps there,
- write a caption.

All of these apply to every selected photo. The info panel also shows each
photo's date, size, camera, lens, exposure, and GPS location, which links to
OpenStreetMap.

**Viewer.** Double-click (or Enter) to open a photo. Use ← → or Space to move
through photos and Esc to go back. Scroll to zoom, drag to pan, and
double-click to switch between fit and 100%. F11 goes full screen and **S**
starts a slideshow.

**Files.** Right-click a photo for **Open in Default App**, **Show in File
Manager**, and **Move to Trash** (it goes to the system Trash, so it can be
restored).

**Formats**: JPEG, PNG, TIFF, WebP, GIF, BMP, and HEIC/HEIF/AVIF (iPhone
photos).

### Not yet

Possible later additions:

- importing from a camera or SD card into dated folders,
- RAW files,
- videos,
- a map view,
- finding duplicates,
- simple edits (rotate, crop),
- writing ratings and tags into the files themselves (XMP), so other apps
  can see them.

## Development setup

```bash
python3 -m venv venv
source venv/bin/activate   # on Windows: venv\Scripts\activate
pip install -r requirements-dev.txt
pip install -e .
```

Run the app:

```bash
python -m simple_photos.app
```

Run the tests (core logic only, no display needed):

```bash
pytest
```

## Project layout

- `src/simple_photos/core/`: plain Python with no Qt dependency, covered by
  `tests/`. It contains:
  - `metadata.py`: EXIF reading and display formatting
  - `library.py`: the SQLite catalogue, scanning, queries, ratings and tags
  - `thumbnails.py`: decoding photos the right way up, and the thumbnail cache
  - `fileops.py`: showing a file in the system's file manager
  - settings, diagnostics and the update checker
- `src/simple_photos/gui/`: the PySide6 window, including the sidebar, grid,
  info panel and viewer.
- `src/simple_photos/app.py`: the entry point.
- `packaging/`: the icon generator, PyInstaller spec, and per-OS installer
  scripts (see Releasing, below).

## Where things are kept

- Library (ratings, tags, captions): `~/.local/share/Simple-Photos/library.db` on
  Linux, `~/Library/Application Support/Simple-Photos/` on macOS,
  `%LOCALAPPDATA%\Simple-Photos\` on Windows.
- Thumbnail cache: the OS's user cache folder, under `Simple-Photos/thumbnails`.
  It's safe to delete; thumbnails are remade as needed.
- Logs: **Help ▸ Open Log Folder**.

## Updates

On startup the app checks the repo's latest GitHub Release in the background.
If a newer version is published, it shows a banner with an **Update** button.
Nothing is downloaded or installed until you click it. If GitHub can't be
reached there's simply no banner, and the app carries on normally.

## Releasing

1. Bump `__version__` in `src/simple_photos/__init__.py` (and `pyproject.toml`).
2. Commit, then tag and push: `git tag vX.Y.Z && git push origin vX.Y.Z`.
3. The [release workflow](.github/workflows/release.yml) builds all three
   platforms and attaches the installers to a GitHub Release for that tag:
   - **Windows**: PyInstaller → Inno Setup → `Simple-Photos-X.Y.Z-Windows-Setup.exe`
   - **macOS**: PyInstaller → `.app` → `Simple-Photos-X.Y.Z-macOS.dmg`
   - **Linux**: PyInstaller → `simple-photos_X.Y.Z_amd64.deb`

To build one platform's installer locally (it must run on that OS):

```bash
pip install -r requirements-dev.txt
pyinstaller --noconfirm packaging/pyinstaller/simple-photos.spec
# then, per platform:
bash packaging/macos/build-dmg.sh      # macOS
bash packaging/linux/build-deb.sh      # Linux
iscc packaging/windows/installer.iss   # Windows (Inno Setup installed)
```
