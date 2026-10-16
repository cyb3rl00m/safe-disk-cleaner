#!/usr/bin/env bash
# Removes Safe Disk Cleaner.  Usage:  sudo bash uninstall.sh
set -euo pipefail

APP_ID="io.github.cyb3rl00m.SafeDiskCleaner"

if [[ $EUID -ne 0 ]]; then
    echo "Please run with sudo:  sudo bash uninstall.sh"
    exit 1
fi

rm -rf /usr/lib/safe-disk-cleaner
rm -f /usr/libexec/safe-disk-cleaner-helper \
      /usr/bin/safe-disk-cleaner \
      "/usr/share/polkit-1/actions/$APP_ID.policy" \
      "/usr/share/applications/$APP_ID.desktop" \
      "/usr/share/metainfo/$APP_ID.metainfo.xml" \
      "/usr/share/icons/hicolor/scalable/apps/$APP_ID.svg"

gtk-update-icon-cache -q -t /usr/share/icons/hicolor 2>/dev/null || true
update-desktop-database -q /usr/share/applications 2>/dev/null || true

if [[ -f /etc/systemd/journald.conf.d/60-safe-disk-cleaner.conf ]]; then
    echo "Note: you set a journal size limit with this app. It stays active."
    echo "To go back to the default:  sudo rm /etc/systemd/journald.conf.d/60-safe-disk-cleaner.conf"
    echo "                            sudo systemctl restart systemd-journald"
fi
echo "Safe Disk Cleaner was removed."
