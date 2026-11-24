#!/usr/bin/env bash
# Regenerates po/safe-disk-cleaner.pot from the source, merges it into every
# po/<lang>.po and compiles each catalogue into the package, where the app reads it:
#
#   src/safe_disk_cleaner/locale/<lang>/LC_MESSAGES/safe-disk-cleaner.mo
#
# Usage:
#   bash po/update.sh          # every language
#   bash po/update.sh es       # only the given languages
#
# Needs gettext (xgettext, msgmerge, msgfmt):  sudo apt install gettext
set -euo pipefail

ROOT="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)"
SRC="$ROOT/src/safe_disk_cleaner"
POT="$ROOT/po/safe-disk-cleaner.pot"

# Relative paths keep the source references in the .pot short and stable.
cd "$ROOT"
xgettext --language=Python \
         --keyword=_ --keyword=N_ --keyword=ngettext:1,2 \
         --from-code=UTF-8 --add-comments=TRANSLATORS: \
         --package-name="Safe Disk Cleaner" \
         --msgid-bugs-address="https://github.com/cyb3rl00m/safe-disk-cleaner/issues" \
         -o "$POT" src/safe_disk_cleaner/*.py

shopt -s nullglob
for po in "$ROOT"/po/*.po; do
    lang="$(basename "$po" .po)"
    if [[ $# -gt 0 && " $* " != *" $lang "* ]]; then
        continue
    fi
    msgmerge --quiet --update --backup=none "$po" "$POT"
    install -d "$SRC/locale/$lang/LC_MESSAGES"
    msgfmt --check -o "$SRC/locale/$lang/LC_MESSAGES/safe-disk-cleaner.mo" "$po"
    echo "==> $lang: $po"
done
