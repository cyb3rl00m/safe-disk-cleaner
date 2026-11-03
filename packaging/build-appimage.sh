#!/usr/bin/env bash
# Builds dist/Safe_Disk_Cleaner-<version>-<arch>.AppImage (+ .zsync for updates). No root needed.
#   bash packaging/build-appimage.sh                 # 64-bit PC (x86_64)
#   ARCH=aarch64 bash packaging/build-appimage.sh    # 64-bit ARM (Raspberry Pi 4/5, ARM laptops)
# The app itself is pure Python, so one x86_64 build machine can build both: only the small
# AppImage starter ("runtime") differs per architecture.
# Downloads appimagetool and the runtime on first use; set APPIMAGETOOL=/path/to/appimagetool
# to use your own.
set -euo pipefail

ROOT="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)"
APP_ID="io.github.cyb3rl00m.SafeDiskCleaner"
VERSION="$(sed -n 's/^VERSION = "\(.*\)"/\1/p' "$ROOT/src/safe_disk_cleaner/__init__.py")"
ARCH="${ARCH:-x86_64}"
case "$ARCH" in
    x86_64|aarch64) ;;
    *) echo "ARCH must be x86_64 or aarch64, not '$ARCH'" >&2; exit 1 ;;
esac
OUT="$ROOT/dist"
NAME="Safe_Disk_Cleaner-$VERSION-$ARCH.AppImage"
# Lets AppImage managers (Gear Lever, AppImageUpdate, AppImageLauncher) find new versions.
UPDATE_INFO="gh-releases-zsync|cyb3rl00m|safe-disk-cleaner|latest|Safe_Disk_Cleaner-*-$ARCH.AppImage.zsync"

CACHE="$ROOT/packaging/.cache"
# appimagetool runs on the build machine, so it is the build machine's architecture.
TOOL="${APPIMAGETOOL:-$CACHE/appimagetool-$(uname -m).AppImage}"
if [[ ! -x "$TOOL" ]]; then
    echo "==> Downloading appimagetool"
    mkdir -p "$CACHE"
    curl -fsSL -o "$TOOL" \
        "https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-$(uname -m).AppImage"
    chmod +x "$TOOL"
fi
# The runtime is the part of the AppImage that starts on the user's computer: target architecture.
RUNTIME="$CACHE/runtime-$ARCH"
if [[ ! -s "$RUNTIME" ]]; then
    echo "==> Downloading the $ARCH AppImage runtime"
    mkdir -p "$CACHE"
    curl -fsSL -o "$RUNTIME" \
        "https://github.com/AppImage/type2-runtime/releases/download/continuous/runtime-$ARCH"
fi

APPDIR="$(mktemp -d)/SafeDiskCleaner.AppDir"
trap 'rm -rf "$(dirname "$APPDIR")"' EXIT
echo "==> Building $NAME"

install -d "$APPDIR/usr/lib/safe-disk-cleaner/safe_disk_cleaner"
install -m 644 "$ROOT"/src/safe_disk_cleaner/*.py "$APPDIR/usr/lib/safe-disk-cleaner/safe_disk_cleaner/"
install -D -m 755 "$ROOT/safe-disk-cleaner"               "$APPDIR/usr/bin/safe-disk-cleaner"
install -D -m 755 "$ROOT/helper/safe-disk-cleaner-helper" "$APPDIR/usr/libexec/safe-disk-cleaner-helper"
install -D -m 644 "$ROOT/data/$APP_ID.metainfo.xml"       "$APPDIR/usr/share/metainfo/$APP_ID.appdata.xml"
install -D -m 644 "$ROOT/data/icons/hicolor/scalable/apps/$APP_ID.svg" \
                  "$APPDIR/usr/share/icons/hicolor/scalable/apps/$APP_ID.svg"
install -m 755 "$ROOT/packaging/appimage/AppRun" "$APPDIR/AppRun"
install -m 644 "$ROOT/data/$APP_ID.desktop" "$APPDIR/$APP_ID.desktop"
install -D -m 644 "$ROOT/data/$APP_ID.desktop" "$APPDIR/usr/share/applications/$APP_ID.desktop"
install -m 644 "$ROOT/data/icons/hicolor/scalable/apps/$APP_ID.svg" "$APPDIR/$APP_ID.svg"
ln -s "$APP_ID.svg" "$APPDIR/.DirIcon"

mkdir -p "$OUT"
rm -f "$OUT/$NAME" "$OUT/$NAME.zsync"
# --appimage-extract-and-run: works without FUSE (e.g. on build servers)
(cd "$OUT" && ARCH="$ARCH" VERSION="$VERSION" "$TOOL" --appimage-extract-and-run --no-appstream \
     --runtime-file "$RUNTIME" \
     -u "$UPDATE_INFO" "$APPDIR" "$OUT/$NAME" >/dev/null)
chmod +x "$OUT/$NAME"
echo "==> $OUT/$NAME"
[[ -f "$OUT/$NAME.zsync" ]] && echo "==> $OUT/$NAME.zsync"
