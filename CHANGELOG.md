# Changelog

## 1.0.0 — 2026-10-16

First release.

- 12 categories: old kernels, package download cache, orphaned packages, old Snap revisions,
  unused Flatpak runtimes, system journal, crash reports & core dumps, rotated logs, picture
  previews, ~/.cache folders, Flatpak/Snap app caches, Trash.
- Plain-language explanation for every category and every known cache folder.
- Root helper through pkexec that re-validates every operation.
- Permanent journal size limit and Snap `refresh.retain` setting.
- `--report` mode for the terminal.
- Arch: `install.sh` also installs `pacman-contrib` (needed to clean the pacman cache safely).
- APT repository on GitHub Pages: add it once, then updates arrive with `apt upgrade`.
  Signed, published automatically by the release workflow.
- `.deb` package (`packaging/build-deb.sh`), built and attached to GitHub releases automatically.
- Overview: a checkbox for every category (with a “partly selected” state), live “Selected to clean”
  total, a 3-step how-to, and clearer “all selected / not selected” labels in the sidebar.
- Safety audit: kernels on hold are always kept; orphaned packages are removed (not purged) so
  edited configs survive; Flatpak "unused" re-check runs as the real user; logs written in the
  last hour are never touched; the root helper rejects malformed requests cleanly. Attack test
  suite added.
