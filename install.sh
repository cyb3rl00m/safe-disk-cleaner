#!/usr/bin/env bash
# Installs Safe Disk Cleaner system-wide.  Usage:  sudo bash install.sh
set -euo pipefail

APP_ID="io.github.cyb3rl00m.SafeDiskCleaner"
SRC="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"

if [[ $EUID -ne 0 ]]; then
    echo "Please run with sudo:  sudo bash install.sh"
    exit 1
fi

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

echo "==> Copying files"
rm -rf /usr/lib/safe-disk-cleaner
install -d -m 755 /usr/lib/safe-disk-cleaner/safe_disk_cleaner
install -m 644 "$SRC"/src/safe_disk_cleaner/*.py /usr/lib/safe-disk-cleaner/safe_disk_cleaner/
python3 -m compileall -q /usr/lib/safe-disk-cleaner >/dev/null || true

install -D -m 755 "$SRC/helper/safe-disk-cleaner-helper" /usr/libexec/safe-disk-cleaner-helper
install -D -m 755 "$SRC/safe-disk-cleaner"               /usr/bin/safe-disk-cleaner
install -D -m 644 "$SRC/data/$APP_ID.policy"             "/usr/share/polkit-1/actions/$APP_ID.policy"
install -D -m 644 "$SRC/data/$APP_ID.desktop"            "/usr/share/applications/$APP_ID.desktop"
install -D -m 644 "$SRC/data/$APP_ID.metainfo.xml"       "/usr/share/metainfo/$APP_ID.metainfo.xml"
install -D -m 644 "$SRC/data/icons/hicolor/scalable/apps/$APP_ID.svg" \
                  "/usr/share/icons/hicolor/scalable/apps/$APP_ID.svg"

gtk-update-icon-cache -q -t /usr/share/icons/hicolor 2>/dev/null || true
update-desktop-database -q /usr/share/applications 2>/dev/null || true

echo "==> Done. Open “Safe Disk Cleaner” from your app menu, or run: safe-disk-cleaner"
