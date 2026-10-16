#!/usr/bin/env bash
# Builds dist/safe-disk-cleaner_<version>_all.deb  (Debian, Ubuntu, Mint, Pop!_OS …)
# Usage:  bash packaging/build-deb.sh          (no root needed)
set -euo pipefail

ROOT="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)"
APP_ID="io.github.cyb3rl00m.SafeDiskCleaner"
VERSION="$(sed -n 's/^VERSION = "\(.*\)"/\1/p' "$ROOT/src/safe_disk_cleaner/__init__.py")"
PKG="safe-disk-cleaner"
OUT="$ROOT/dist"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
chmod 755 "$STAGE"

echo "==> Building $PKG $VERSION"

# --- files (same layout as install.sh) -------------------------------------
install -d "$STAGE/usr/lib/safe-disk-cleaner/safe_disk_cleaner"
install -m 644 "$ROOT"/src/safe_disk_cleaner/*.py "$STAGE/usr/lib/safe-disk-cleaner/safe_disk_cleaner/"
install -D -m 755 "$ROOT/helper/safe-disk-cleaner-helper" "$STAGE/usr/libexec/safe-disk-cleaner-helper"
install -D -m 755 "$ROOT/safe-disk-cleaner"               "$STAGE/usr/bin/safe-disk-cleaner"
install -D -m 644 "$ROOT/data/$APP_ID.policy"             "$STAGE/usr/share/polkit-1/actions/$APP_ID.policy"
install -D -m 644 "$ROOT/data/$APP_ID.desktop"            "$STAGE/usr/share/applications/$APP_ID.desktop"
install -D -m 644 "$ROOT/data/$APP_ID.metainfo.xml"       "$STAGE/usr/share/metainfo/$APP_ID.metainfo.xml"
install -D -m 644 "$ROOT/data/icons/hicolor/scalable/apps/$APP_ID.svg" \
                  "$STAGE/usr/share/icons/hicolor/scalable/apps/$APP_ID.svg"
install -D -m 644 "$ROOT/README.md"    "$STAGE/usr/share/doc/$PKG/README.md"
install -D -m 644 "$ROOT/CHANGELOG.md" "$STAGE/usr/share/doc/$PKG/CHANGELOG.md"
install -D -m 644 "$ROOT/LICENSE"      "$STAGE/usr/share/doc/$PKG/copyright"

# --- control -----------------------------------------------------------------
SIZE_KB="$(du -sk "$STAGE" | cut -f1)"
install -d "$STAGE/DEBIAN"
cat > "$STAGE/DEBIAN/control" <<EOF
Package: $PKG
Version: $VERSION
Section: utils
Priority: optional
Architecture: all
Installed-Size: $SIZE_KB
Depends: python3 (>= 3.10), python3-gi, gir1.2-gtk-4.0, gir1.2-adw-1 (>= 1.4), pkexec | policykit-1
Suggests: flatpak, snapd
Maintainer: cyb3rl00m <https://github.com/cyb3rl00m>
Homepage: https://github.com/cyb3rl00m/safe-disk-cleaner
Description: see where disk space is wasted and what is safe to delete
 Safe Disk Cleaner finds wasted space on Ubuntu and other systemd-based
 distributions — old kernels, package caches, old Snap versions, unused
 Flatpak runtimes, an oversized journal, crash dumps, old logs, stale
 picture previews, app caches and the Trash — and explains each one in
 plain language: what it is, why it grows, whether it is safe to delete
 and exactly which command will run. System work goes through a small
 pkexec helper that re-validates every operation.
EOF

# Python caches are created when the root helper runs; remove them on uninstall
cat > "$STAGE/DEBIAN/prerm" <<'EOF'
#!/bin/sh
set -e
rm -rf /usr/lib/safe-disk-cleaner/safe_disk_cleaner/__pycache__
exit 0
EOF
chmod 755 "$STAGE/DEBIAN/prerm"

# --- build -------------------------------------------------------------------
mkdir -p "$OUT"
DEB="$OUT/${PKG}_${VERSION}_all.deb"
dpkg-deb --root-owner-group --build "$STAGE" "$DEB" >/dev/null
echo "==> $DEB"
