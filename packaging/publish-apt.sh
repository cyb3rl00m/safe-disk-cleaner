#!/usr/bin/env bash
# Adds the .deb files in dist/ to the APT repository on the gh-pages branch and pushes it.
# Used by .github/workflows/release.yml.   Usage:  bash packaging/publish-apt.sh <gpg-key-id>
set -euo pipefail

KEY_ID="${1:?usage: publish-apt.sh <gpg-key-id>}"
ROOT="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)"
SITE="$ROOT/.gh-pages"
cd "$ROOT"

shopt -s nullglob
DEBS=(dist/*.deb)
[[ ${#DEBS[@]} -gt 0 ]] || { echo "No .deb in dist/ — run packaging/build-deb.sh first"; exit 1; }

rm -rf "$SITE"; git worktree prune
if git ls-remote --exit-code --heads origin gh-pages >/dev/null 2>&1; then
    git fetch --depth=1 origin gh-pages
    git worktree add -B gh-pages "$SITE" FETCH_HEAD
else
    echo "gh-pages does not exist yet — creating it"
    git worktree add --detach "$SITE"
    (cd "$SITE" && git checkout --orphan gh-pages && git rm -rf --quiet . && git clean -fdx --quiet)
fi

python3 packaging/apt_repo.py "$SITE/apt" "${DEBS[@]}" --key "$KEY_ID"
cp packaging/apt-index.html "$SITE/index.html"
touch "$SITE/.nojekyll"

VERSION="$(sed -n 's/^VERSION = "\(.*\)"/\1/p' src/safe_disk_cleaner/__init__.py)"
cd "$SITE"
git add -A
if git diff --cached --quiet; then
    echo "APT repository already up to date"
else
    git commit --quiet -m "APT repository: safe-disk-cleaner $VERSION"
    git push origin gh-pages
    echo "Published safe-disk-cleaner $VERSION to the APT repository"
fi
cd "$ROOT"
git worktree remove --force "$SITE"
