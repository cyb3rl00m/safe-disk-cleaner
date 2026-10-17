#!/usr/bin/env bash
# Removes a Safe Disk Cleaner that was installed with install.sh.
#   sudo bash uninstall.sh
# (Installed the .deb instead? Use:  sudo apt remove safe-disk-cleaner)
set -euo pipefail

APP_ID="io.github.cyb3rl00m.SafeDiskCleaner"
POLKIT_FILE="/usr/share/polkit-1/actions/$APP_ID.policy"

if [[ $EUID -ne 0 ]]; then
    echo "Please run with sudo:  sudo bash uninstall.sh"
    exit 1
fi

DEB_INSTALLED=0
if command -v dpkg-query >/dev/null \
   && dpkg-query -W -f='${Status}' safe-disk-cleaner 2>/dev/null | grep -q "ok installed"; then
    DEB_INSTALLED=1
fi

# install.sh uses /usr/local (or $PREFIX); version 1.0.0 of it used /usr.
PREFIXES=(/usr/local /usr)
[[ -n "${PREFIX:-}" ]] && PREFIXES=("${PREFIX%/}" "${PREFIXES[@]}")

removed=0
for p in "${PREFIXES[@]}"; do
    # Files in /usr belong to the .deb if it is installed — leave them to apt.
    if [[ "$p" == "/usr" && $DEB_INSTALLED == 1 ]]; then
        continue
    fi
    if [[ -e "$p/lib/safe-disk-cleaner" || -e "$p/bin/safe-disk-cleaner" ]]; then
        rm -rf "$p/lib/safe-disk-cleaner"
        rm -f "$p/libexec/safe-disk-cleaner-helper" \
              "$p/bin/safe-disk-cleaner" \
              "$p/share/applications/$APP_ID.desktop" \
              "$p/share/metainfo/$APP_ID.metainfo.xml" \
              "$p/share/icons/hicolor/scalable/apps/$APP_ID.svg"
        [[ -f "$p/share/icons/hicolor/icon-theme.cache" ]] && gtk-update-icon-cache -q -t "$p/share/icons/hicolor" 2>/dev/null || true
        update-desktop-database -q "$p/share/applications" 2>/dev/null || true
        echo "Removed Safe Disk Cleaner from $p"
        removed=1
    fi
done
if [[ $DEB_INSTALLED == 0 ]]; then
    rm -f "$POLKIT_FILE"
fi

if [[ $DEB_INSTALLED == 1 ]]; then
    echo "The .deb package is still installed. Remove it with:  sudo apt remove safe-disk-cleaner"
elif [[ $removed == 0 ]]; then
    echo "Safe Disk Cleaner was not found — nothing to remove."
fi

if [[ -f /etc/systemd/journald.conf.d/60-safe-disk-cleaner.conf ]]; then
    echo "Note: you set a journal size limit with this app. It stays active."
    echo "To go back to the default:  sudo rm /etc/systemd/journald.conf.d/60-safe-disk-cleaner.conf"
    echo "                            sudo systemctl restart systemd-journald"
fi
