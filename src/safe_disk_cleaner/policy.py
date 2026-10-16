"""Rules shared by the app and the privileged helper.

Everything here is pure (no I/O) so it can be unit tested and so the app and
the root helper make exactly the same decisions: the app uses these rules to
decide what to *offer*, the helper re-checks them before it *deletes*.
"""

from __future__ import annotations

import re

# --------------------------------------------------------------------------
# Name / value validators
# --------------------------------------------------------------------------

# Debian/Ubuntu package names, optionally with an architecture qualifier.
APT_PKG_RE = re.compile(r"^[a-z0-9][a-z0-9+.-]+(?::[a-z0-9]+)?$")
# Fedora / Arch / openSUSE package names.
GENERIC_PKG_RE = re.compile(r"^[A-Za-z0-9_+@][A-Za-z0-9_+@.-]*$")

SNAP_NAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,38}[a-z0-9])?(?:_[a-z0-9]{1,10})?$")
SNAP_REV_RE = re.compile(r"^x?\d{1,10}$")

FLATPAK_REF_RE = re.compile(
    r"^runtime/[A-Za-z0-9_][A-Za-z0-9_.-]*/[A-Za-z0-9_]+/[A-Za-z0-9_.-]+$"
)

JOURNAL_VACUUM_SIZES = ("100M", "200M", "500M", "1G")
JOURNAL_LIMITS = ("200M", "500M", "1G", "2G", "default")

PACKAGE_MANAGERS = ("apt", "dnf", "pacman", "zypper")

# A rotated log is never written to again. Anything changed within this many
# seconds is treated as a live log and left alone.
LIVE_LOG_SECONDS = 3600

# Rotated (old) log files: syslog.1, kern.log.2.gz, messages-20261012, foo.old ...
ROTATED_LOG_RE = re.compile(
    r"^[^/]+(?:"
    r"\.\d{1,3}(?:\.(?:gz|xz|zst|bz2|lz4))?"
    r"|\.(?:gz|xz|zst|bz2|lz4)"
    r"|\.old"
    r"|-\d{8}(?:\.(?:gz|xz|zst|bz2|lz4))?"
    r")$"
)

APPORT_CRASH_RE = re.compile(r"^[^/]+\.(?:crash|upload|uploaded)$")
COREDUMP_RE = re.compile(r"^core\.[^/]+$")

# Files the helper may delete, grouped so each group has one fixed base dir.
DELETE_GROUPS = {
    # name: (base directory, filename regex, allow sub-directories)
    "crash": ("/var/crash", APPORT_CRASH_RE, False),
    "coredump": ("/var/lib/systemd/coredump", COREDUMP_RE, False),
    "rotated-logs": ("/var/log", ROTATED_LOG_RE, True),
}


def size_ok(value: str, allowed: tuple[str, ...]) -> bool:
    return value in allowed


# --------------------------------------------------------------------------
# Kernels (Debian / Ubuntu)
# --------------------------------------------------------------------------

KERNEL_PKG_RE = re.compile(
    r"^linux-"
    r"(?:(?:hwe|hwe-edge|oem|lowlatency|realtime)(?:-[\d.]+)?-)?"
    r"(?:image(?:-unsigned)?|modules(?:-extra)?|headers|tools|cloud-tools|buildinfo"
    r"|modules-ipu\d*(?:-[a-z]+)?"
    r"|modules-nvidia-\d+(?:-server)?(?:-open)?"
    r"|objects-nvidia-\d+(?:-server)?(?:-open)?"
    r"|signatures-nvidia)"
    r"-(\d+\.\d+\.\d+-\d+)(?:-[a-z0-9]+)?$"
)

RUNNING_KERNEL_RE = re.compile(r"^(\d+\.\d+\.\d+-\d+)")


def kernel_version_of_package(pkg: str) -> str | None:
    """'linux-image-6.17.0-5-generic' -> '6.17.0-5'; meta packages -> None."""
    m = KERNEL_PKG_RE.match(pkg.split(":", 1)[0])
    return m.group(1) if m else None


def running_kernel_version(uname_release: str) -> str | None:
    """'6.17.0-5-generic' -> '6.17.0-5'."""
    m = RUNNING_KERNEL_RE.match(uname_release.strip())
    return m.group(1) if m else None


def kernel_sort_key(version: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.split(r"[.-]", version) if x.isdigit())


def plan_kernels(installed: set[str], running: str | None,
                 held: set[str] | frozenset[str] = frozenset()) -> tuple[set[str], set[str]]:
    """Decide which kernel versions to keep.

    Always keeps: the running kernel, the newest installed kernel, any kernel
    with a package put on hold (`apt-mark hold`), and enough others that at
    least TWO bootable kernels remain (a fallback in case the newest one
    breaks). Returns (keep, removable).
    """
    ordered = sorted(installed, key=kernel_sort_key, reverse=True)
    keep: set[str] = set(held) & set(installed)
    if running and running in installed:
        keep.add(running)
    if ordered:
        keep.add(ordered[0])
    for version in ordered:
        if len(keep) >= 2:
            break
        keep.add(version)
    return keep, set(installed) - keep


# --------------------------------------------------------------------------
# Parsers for command output (LC_ALL=C)
# --------------------------------------------------------------------------

def parse_apt_simulation(output: str) -> list[str]:
    """Package names that `apt-get -s ...` says it would remove or purge."""
    names = []
    for line in output.splitlines():
        if line.startswith(("Remv ", "Purg ")):
            parts = line.split()
            if len(parts) >= 2:
                names.append(parts[1])
    return names


def parse_snap_list_all(output: str) -> list[dict]:
    """Rows of `snap list --all` as dicts (name, version, rev, notes)."""
    rows: list[dict] = []
    lines = output.splitlines()
    if not lines or not lines[0].startswith("Name"):
        return rows
    for line in lines[1:]:
        parts = line.split()
        if len(parts) < 4:
            continue
        rows.append({
            "name": parts[0],
            "version": parts[1],
            "rev": parts[2],
            "notes": parts[-1] if len(parts) >= 6 else "-",
        })
    return rows


def parse_flatpak_unused(output: str) -> list[dict]:
    """Rows of `flatpak uninstall --unused` when not attached to a terminal.

    Flatpak then prints tab-separated cells: " 1.", "", ID, [Arch], Branch, Op.
    The Arch column is left out when every row has the machine's own arch.
    Each row: {"id", "arch" (or None), "branch"}.
    """
    rows: list[dict] = []
    for line in output.splitlines():
        cells = [c.strip() for c in line.split("\t")]
        cells = [c for c in cells if c]
        if len(cells) < 4 or not re.match(r"^\d+\.$", cells[0]):
            continue
        rest = cells[1:]
        if len(rest) >= 4:            # ID Arch Branch Op
            rows.append({"id": rest[0], "arch": rest[1], "branch": rest[2]})
        else:                         # ID Branch Op
            rows.append({"id": rest[0], "arch": None, "branch": rest[1]})
    return rows


def flatpak_ref_in(ref: str, unused_rows: list[dict]) -> bool:
    parts = ref.split("/")
    if len(parts) != 4:
        return False
    _, rid, arch, branch = parts
    for row in unused_rows:
        if row["id"] == rid and row["branch"] == branch and row["arch"] in (None, arch):
            return True
    return False


_SIZE_UNITS = {
    "b": 1, "byte": 1, "bytes": 1,
    "k": 1000, "kb": 1000, "m": 1000**2, "mb": 1000**2, "g": 1000**3, "gb": 1000**3,
    "t": 1000**4, "tb": 1000**4,
    "kib": 1024, "mib": 1024**2, "gib": 1024**3, "tib": 1024**4,
}


def parse_size(text: str) -> int | None:
    """'1.2 GB', '345.6 MB', '512M', '1.23 MiB', '12.0 kB' -> bytes."""
    m = re.match(r"^\s*([\d.,]+)\s*([A-Za-z]*)\s*$", text or "")
    if not m:
        return None
    try:
        number = float(m.group(1).replace(",", "."))
    except ValueError:
        return None
    unit = (m.group(2) or "b").lower()
    if unit not in _SIZE_UNITS:
        return None
    return int(number * _SIZE_UNITS[unit])


# --------------------------------------------------------------------------
# Human readable previews of what the helper will run
# --------------------------------------------------------------------------

def preview_op(op: dict) -> list[str]:
    """The commands an operation runs, as the user should see them."""
    kind = op.get("op")
    if kind == "pkg-cache-clean":
        mgr = op.get("manager")
        options: dict[str, list[str]] = {
            "apt": ["apt-get clean"],
            "dnf": ["dnf clean packages"],
            "pacman": ["paccache -rk2   (keeps the 2 newest versions of each package)",
                       "paccache -ruk0  (removes cached packages that are not installed)"],
            "zypper": ["zypper --non-interactive clean"],
        }
        return options.get(str(mgr), [])
    if kind == "remove-packages":
        pkgs = " ".join(op.get("packages", []))
        mgr = op.get("manager")
        if mgr == "apt":
            verb = "purge" if op.get("purpose") == "kernels" else "remove"
            return [f"apt-get {verb} -y {pkgs}"]
        if mgr == "dnf":
            return [f"dnf remove -y {pkgs}"]
        if mgr == "pacman":
            return [f"pacman -Rn --noconfirm {pkgs}"]
    if kind == "snap-remove-revision":
        return [f"snap remove {op.get('name')} --revision={op.get('revision')}"]
    if kind == "snap-set-retain":
        return [f"snap set system refresh.retain={op.get('value')}"]
    if kind == "journal-vacuum":
        return [f"journalctl --vacuum-size={op.get('size')}"]
    if kind == "journal-set-limit":
        size = op.get("size")
        if size == "default":
            return ["rm /etc/systemd/journald.conf.d/60-safe-disk-cleaner.conf",
                    "systemctl restart systemd-journald"]
        return [f"write SystemMaxUse={size} to /etc/systemd/journald.conf.d/60-safe-disk-cleaner.conf",
                "systemctl restart systemd-journald"]
    if kind == "flatpak-uninstall":
        return [f"flatpak uninstall --system --noninteractive {' '.join(op.get('refs', []))}"]
    if kind == "delete-files":
        paths = op.get("paths", [])
        if len(paths) <= 6:
            return [f"rm {p}" for p in paths]
        return [f"rm {p}" for p in paths[:5]] + [f"… and {len(paths) - 5} more files"]
    return [str(op)]
