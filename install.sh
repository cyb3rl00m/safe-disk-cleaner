#!/usr/bin/env bash
# Installs Safe Disk Cleaner from source into /usr/local (the place for software that does not
# come from your package manager).
#
#   sudo bash install.sh                  # installs into /usr/local
#   sudo PREFIX=/opt/sdc bash install.sh  # or somewhere else
#
# On Ubuntu/Debian you can use the .deb package from the releases page instead.
set -euo pipefail

APP_ID="io.github.cyb3rl00m.SafeDiskCleaner"
SRC="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
PREFIX="${PREFIX:-/usr/local}"
PREFIX="${PREFIX%/}"
# polkit only reads actions from this folder, whatever the prefix is
POLKIT_DIR="/usr/share/polkit-1/actions"

if [[ $EUID -ne 0 ]]; then
    echo "Please run with sudo:  sudo bash install.sh"
    exit 1
fi
if [[ "$PREFIX" != /* || "$PREFIX" == *[[:space:]#]* ]]; then
    echo "PREFIX must be an absolute path without spaces, e.g. /usr/local"
    exit 1
fi
if command -v dpkg-query >/dev/null \
   && dpkg-query -W -f='${Status}' safe-disk-cleaner 2>/dev/null | grep -q "ok installed"; then
    echo "Safe Disk Cleaner is already installed as a .deb package."
    echo "Use that (update it with a newer .deb), or remove it first:  sudo apt remove safe-disk-cleaner"
    exit 1
fi

remove_from() {   # remove an install.sh installation from prefix $1
    local p="$1"
    rm -rf "$p/lib/safe-disk-cleaner"
    rm -f "$p/libexec/safe-disk-cleaner-helper" \
          "$p/bin/safe-disk-cleaner" \
          "$p/share/applications/$APP_ID.desktop" \
          "$p/share/metainfo/$APP_ID.metainfo.xml" \
          "$p/share/icons/hicolor/scalable/apps/$APP_ID.svg"
}

echo "==> Installing dependencies (GTK 4, libadwaita, Python bindings, pkexec)"
if [[ "${SKIP_DEPS:-0}" == "1" ]]; then
    echo "    skipped (SKIP_DEPS=1)"
elif command -v apt-get >/dev/null; then
    apt-get install -y python3 python3-gi gir1.2-gtk-4.0 gir1.2-adw-1
    apt-get install -y pkexec 2>/dev/null || apt-get install -y policykit-1
elif command -v dnf >/dev/null; then
    dnf install -y python3 python3-gobject gtk4 libadwaita polkit
elif command -v pacman >/dev/null; then
    pacman -S --needed --noconfirm python python-gobject gtk4 libadwaita polkit pacman-contrib
elif command -v zypper >/dev/null; then
    zypper --non-interactive install python3-gobject typelib-1_0-Gtk-4_0 typelib-1_0-Adw-1 polkit
else
    echo "Unknown package manager: please install Python 3 + PyGObject, GTK 4, libadwaita and polkit yourself."
fi

# Version 1.0.0 of this script installed into /usr — move such an install out of the way.
if [[ "$PREFIX" != "/usr" && -e /usr/lib/safe-disk-cleaner ]]; then
    echo "==> Removing an older Safe Disk Cleaner install from /usr"
    remove_from /usr
fi

echo "==> Copying files to $PREFIX"
remove_from "$PREFIX"
install -d -m 755 "$PREFIX/lib/safe-disk-cleaner/safe_disk_cleaner"
install -m 644 "$SRC"/src/safe_disk_cleaner/*.py "$PREFIX/lib/safe-disk-cleaner/safe_disk_cleaner/"
python3 -m compileall -q "$PREFIX/lib/safe-disk-cleaner" >/dev/null || true

install -D -m 755 "$SRC/helper/safe-disk-cleaner-helper" "$PREFIX/libexec/safe-disk-cleaner-helper"
install -D -m 755 "$SRC/safe-disk-cleaner"               "$PREFIX/bin/safe-disk-cleaner"
install -D -m 644 "$SRC/data/$APP_ID.desktop"            "$PREFIX/share/applications/$APP_ID.desktop"
install -D -m 644 "$SRC/data/$APP_ID.metainfo.xml"       "$PREFIX/share/metainfo/$APP_ID.metainfo.xml"
install -D -m 644 "$SRC/data/icons/hicolor/scalable/apps/$APP_ID.svg" \
                  "$PREFIX/share/icons/hicolor/scalable/apps/$APP_ID.svg"

# The polkit policy names the exact helper path, so point it at this prefix.
install -d -m 755 "$POLKIT_DIR"
sed "s#/usr/libexec/safe-disk-cleaner-helper#$PREFIX/libexec/safe-disk-cleaner-helper#" \
    "$SRC/data/$APP_ID.policy" > "$POLKIT_DIR/$APP_ID.policy"
chmod 644 "$POLKIT_DIR/$APP_ID.policy"

for dir in "$PREFIX/share/icons/hicolor" /usr/share/icons/hicolor; do
    [[ -f "$dir/icon-theme.cache" ]] && gtk-update-icon-cache -q -t "$dir" 2>/dev/null || true
done
update-desktop-database -q "$PREFIX/share/applications" 2>/dev/null || true

if [[ ":$PATH:" != *":$PREFIX/bin:"* ]]; then
    echo "Note: $PREFIX/bin is not in your PATH; start it from the app menu or run $PREFIX/bin/safe-disk-cleaner"
fi
echo "==> Done. Open “Safe Disk Cleaner” from your app menu, or run: safe-disk-cleaner"
echo "    To remove it later:  sudo bash uninstall.sh"
