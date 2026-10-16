"""Entry point: `safe-disk-cleaner` opens the window, `--report` prints a text report."""

from __future__ import annotations

import sys

from . import APP_NAME, VERSION

USAGE = f"""{APP_NAME} {VERSION}

Usage:
  safe-disk-cleaner            open the app
  safe-disk-cleaner --report   scan and print what could be cleaned (changes nothing)
  safe-disk-cleaner --version
"""


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    if "--help" in argv or "-h" in argv:
        print(USAGE)
        return 0
    if "--version" in argv:
        print(f"{APP_NAME} {VERSION}")
        return 0
    if "--report" in argv:
        from .report import print_report
        return print_report()
    try:
        from .window import run_app
    except (ImportError, ValueError) as exc:
        print(f"{APP_NAME} needs GTK 4 and libadwaita for Python ({exc}).\n"
              "On Ubuntu/Debian:  sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1\n"
              "On Fedora:         sudo dnf install python3-gobject gtk4 libadwaita\n"
              "On Arch:           sudo pacman -S python-gobject gtk4 libadwaita", file=sys.stderr)
        return 1
    return run_app([a for a in argv if a not in ("--report",)])


if __name__ == "__main__":
    sys.exit(main())
