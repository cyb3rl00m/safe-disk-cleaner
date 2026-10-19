# Changelog

## 1.1.1 — 2026-10-19

Thanks to @dmaglio for the precise report and analysis ([#3](https://github.com/cyb3rl00m/safe-disk-cleaner/issues/3)).

- **Fixed:** titles containing `&` — "Crash reports & core dumps", "Flatpak & Snap app caches" — were
  rejected as broken markup and not shown, with a GTK warning in the terminal. The same could happen
  to items with `&` or `<` in their name, such as a file in the Trash called "Tom & Jerry.mp4", and to
  error messages in notifications. All rows and notifications now show their text literally.
- A test makes sure every row and notification is created the safe way from now on.

## 1.1.0 — 2026-10-18

- **AppImage:** a single ~1 MB file that runs on any distribution with GTK 4 and libadwaita —
  no installation needed. AppImage managers (Gear Lever, AppImageUpdate) can update it automatically.
- If GTK 4 or libadwaita are missing, the AppImage shows a window with the exact install command
  for your distribution instead of failing silently.
- The root helper no longer writes `.pyc` files when run from the source folder.

## 1.0.1 — 2026-10-17

Thanks to @fastrizwaan for reporting both of these ([#1](https://github.com/cyb3rl00m/safe-disk-cleaner/issues/1)).

- **Back button:** a "←" button in the header returns from a category to the Overview.
  Alt+← and the mouse's back button work too.
- **`install.sh` now installs into `/usr/local`** instead of `/usr`, which belongs to the package
  manager. An existing install in `/usr` from version 1.0.0 is moved automatically. Choose another
  place with `sudo PREFIX=… bash install.sh`. `uninstall.sh` cleans up both locations.
- `install.sh` refuses to run when the `.deb` is installed, so the two can't get mixed up.
- The `.deb` package still installs into `/usr`, as packages should.

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
- `.deb` package (`packaging/build-deb.sh`), built and attached to GitHub releases automatically.
- Overview: a checkbox for every category (with a “partly selected” state), live “Selected to clean”
  total, a 3-step how-to, and clearer “all selected / not selected” labels in the sidebar.
- Safety audit: kernels on hold are always kept; orphaned packages are removed (not purged) so
  edited configs survive; Flatpak "unused" re-check runs as the real user; logs written in the
  last hour are never touched; the root helper rejects malformed requests cleanly. Attack test
  suite added.
