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

**Importing from USB sticks, cards, drives and cameras.** Plug one in and
it appears in the **Devices** list under the sidebar, and the status bar
says so. Double-click it (or select it and click **Import Photos…**), or use
**File ▸ Import From ▸** for any connected device or **Other Folder…**.

Simple-Photos looks through it for photos, skipping system folders like
`$RECYCLE.BIN` and `.Trashes`, and shows how many there are, their size and
the dates they were taken. The photos are copied into one new folder named
with today's date and the device, such as **`2026-10-27 KINGSTON`**. You can
change the name and choose which collection folder it goes in.

- **Skip photos already in your library** is on by default, so putting the
  same card in again doesn't make copies. A photo counts as already
  imported if one with the same file size and date taken is already in the
  library.
- The copies keep the originals' dates. Files are never overwritten (a
  clash gets "(2)"), and a stopped import never leaves half a file.
- Nothing on the device is changed or deleted.

Which devices show up:

- **Linux**: anything the desktop mounts under `/media` or `/run/media`.
  Phones and cameras connected in photo-transfer (MTP/PTP) mode also show
  up, because GNOME makes them readable like a folder.
- **macOS**: external volumes under `/Volumes`.
- **Windows**: removable and external drives other than the Windows drive.

A camera that connects as a drive (or its card in a card reader) works
everywhere. RAW files aren't imported yet, only the formats Simple-Photos
shows.

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

**Viewer.** Double-click (or Enter) to open a photo. Move through photos with
the big ‹ › arrows at the sides, with ← → (or Space, Home and End), or with
your mouse's back and forward buttons. The keys keep working after you click
in the info panel, except while you're typing in a text box. The ✕ in the
photo's top-right corner, **◀ Grid** or Esc goes back to the grid. **Move**
and **Delete** on the viewer's toolbar move the photo to another folder or
send it to the Trash (after asking), then go on to the next photo. Scroll to zoom, drag to pan, and double-click to switch between
fit and 100%. F11 goes full screen and **S** starts a slideshow.

**Folders.** **File ▸ New Folder…** (Ctrl+Shift+N) makes a real folder on
disk inside the folder picked in the sidebar. You can also right-click any
folder in the sidebar and choose **New Folder…**. Names that wouldn't work on
Windows, macOS or Linux are refused as you type. Empty folders show in the tree
too, so a new folder is ready to fill.

**Renaming folders.** Right-click a folder in the sidebar and choose **Rename
Folder…**, or pick it and press **F2**. The photos inside keep their ratings,
tags and captions. Renaming one of your collection folders updates Settings
too.

**Deleting empty folders.** Right-click an empty folder in the sidebar and
choose **Delete Folder**, or use **File ▸ Delete Empty Folders…** to find
them all at once and untick any you want to keep. A folder counts as empty
only if it and its subfolders hold no files at all, apart from the clutter
computers leave behind (`.DS_Store`, `Thumbs.db`, `desktop.ini`). A folder
with anything else in it, even with no photos (a video, say), is never
offered. Your collection folders themselves are never removed. Folders go
to the Trash, so they can be put back.

**Rotating.** **Rotate Left** (Ctrl+L) and **Rotate Right** (Ctrl+R) work on
the selected photos, on the right-click menu and from the ⟲ ⟳ buttons in the
viewer. Rotating never costs any quality:

- For a JPEG, only the orientation flag in the file changes. This is the same
  flag a camera sets when you hold it sideways, and every modern viewer, phone
  and browser follows it.
- PNG, TIFF and BMP files are turned and saved again, which is also lossless.
- WebP and HEIC files are left alone, because re-saving them would lose
  quality.

The file keeps its date either way.

**Auto-Rotate…** (Photo menu) looks for photos that are on their side or
upside down, which usually means they lost their camera orientation (scans,
edited copies, photos sent through messaging apps). It checks the selected
photos, or everything shown if you've selected one or none.

It goes by faces. Each photo is tried at all four quarter turns, and if
faces appear only when it's turned, that's the way up it should be. A face
only counts when two detectors agree and eyes are found in it, so patterns
aren't mistaken for faces. You get a preview of each suggested photo, shown
the way it will look after rotating, and untick any you don't want before
anything changes. Photos without people give no clear answer and are left as
they are, so use Rotate Left / Right for those.

**Finding duplicates.** **Photo ▸ Find Duplicates…** looks through the whole
collection, or just the photos shown, at one of three strictness levels:

- **Exact copies only**: the same file saved twice. Only photos that share a
  file size are read and compared, so this is quick.
- **The same photo, even resized or re-saved** (the default): also catches
  smaller copies, re-saved JPEGs, copies sent through messaging apps, and
  rotated copies.
- **Very similar photos**: also near-identical shots, like a burst or a slight
  crop. Check these carefully.

Look-alikes are found by a "fingerprint" of each photo's thumbnail. Copies of
one picture give the same or nearly the same fingerprint; different pictures
are far apart. Near-blank photos (all black, all sky) are only ever matched as
exact copies.

The results show each group side by side, with the folder, size and rating of
each copy. The best copy of each group is kept (highest resolution, then
largest file, then the one you've rated or tagged) and the rest are ticked to
go to the Trash. Change any tick you like, but each group must keep at least
one photo. Ratings, tags and captions from the copies you remove are added to
the one you keep. Fingerprints are remembered, so later searches only look at
new or changed photos.

**Moving photos.** Select one or more photos and choose **Photo ▸ Move to
Folder…** (Ctrl+Shift+M, also on the right-click menu and in the viewer).
Then pick the destination, or make a new one there with **New Folder…**. You
can also drag photos from the grid onto a folder in the sidebar. Moved photos
keep their ratings, tags and captions. A photo never overwrites a file of the
same name; it's saved as `name (2).jpg` instead. Moving to another drive copies
the file, then deletes the original. Photos dragged out to a file manager or
another app are copied, never moved, so nothing leaves the library behind
its back.

**Files.** Right-click a photo for **Open in Default App**, **Show in File
Manager**, and **Move to Trash** (it goes to the system Trash, so it can be
restored).

**Formats**: JPEG, PNG, TIFF, WebP, GIF, BMP, and HEIC/HEIF/AVIF (iPhone
photos).

Face detection uses [OpenCV](https://opencv.org)'s bundled Haar cascades and
runs entirely on your computer. OpenCV adds about 55 MB to the installers.

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
  - `fileops.py`: making, renaming and moving folders and photos, and
    showing a file in the system's file manager
  - `rotation.py`: lossless rotation (the JPEG orientation tag, or re-saving
    lossless formats)
  - `autorotate.py`: spotting sideways photos by their faces (OpenCV)
  - `duplicates.py`: exact-copy and look-alike fingerprints, and grouping
  - `devices.py` and `importer.py`: spotting plugged-in devices, and importing from them
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
