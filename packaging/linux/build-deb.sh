#!/usr/bin/env bash
# Packages dist/Simple-Photos/ (already built by PyInstaller, onedir mode)
# into a .deb that installs to /opt/simple-photos and adds a proper menu
# entry + icon, so it behaves like a native app rather than a dropped
# binary. Uses dpkg-deb directly, which ships with Debian-based distros
# (Pop!_OS included) — no extra tool to install in CI.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BUNDLE="$ROOT/dist/Simple-Photos"
VERSION="$(grep -o '__version__ = "[^"]*"' "$ROOT/src/simple_photos/__init__.py" | cut -d'"' -f2)"
ARCH="$(dpkg --print-architecture)"
PKG_ROOT="$ROOT/dist/deb-staging"
OUT="$ROOT/dist/simple-photos_${VERSION}_${ARCH}.deb"

if [ ! -d "$BUNDLE" ]; then
  echo "error: $BUNDLE not found — run PyInstaller first" >&2
  exit 1
fi

rm -rf "$PKG_ROOT" "$OUT"
mkdir -p "$PKG_ROOT/DEBIAN"
mkdir -p "$PKG_ROOT/opt/simple-photos"
mkdir -p "$PKG_ROOT/usr/bin"
mkdir -p "$PKG_ROOT/usr/share/applications"

cp -R "$BUNDLE"/. "$PKG_ROOT/opt/simple-photos/"
ln -s /opt/simple-photos/Simple-Photos "$PKG_ROOT/usr/bin/simple-photos"
cp "$ROOT/packaging/linux/simple-photos.desktop" "$PKG_ROOT/usr/share/applications/"

for size in 16 32 48 64 128 256 512; do
  dir="$PKG_ROOT/usr/share/icons/hicolor/${size}x${size}/apps"
  mkdir -p "$dir"
  cp "$ROOT/packaging/icons/icon_${size}.png" "$dir/simple-photos.png"
done

cat > "$PKG_ROOT/DEBIAN/control" <<EOF
Package: simple-photos
Version: $VERSION
Section: graphics
Priority: optional
Architecture: $ARCH
Maintainer: Mike Hellyer
Description: Simple photo manager
 Organises a photo collection by folder, date, tag and rating, with a
 fast thumbnail browser, EXIF details, a full-screen viewer and an
 in-app update checker.
EOF

dpkg-deb --build --root-owner-group "$PKG_ROOT" "$OUT"
rm -rf "$PKG_ROOT"

echo "wrote $OUT"
