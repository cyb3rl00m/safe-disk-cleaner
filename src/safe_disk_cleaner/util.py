"""Small helpers: sizes, running commands, measuring folders, processes."""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
from dataclasses import dataclass

C_ENV = {"LC_ALL": "C", "LANG": "C", "LANGUAGE": "C"}


def human_size(n: int | float | None) -> str:
    """SI units, like GNOME Files and Disks (1 GB = 1,000,000,000 bytes)."""
    if n is None:
        return "?"
    n = float(n)
    if n < 1000:
        return f"{int(n)} bytes" if n != 1 else "1 byte"
    for unit in ("kB", "MB", "GB", "TB"):
        n /= 1000.0
        if n < 1000 or unit == "TB":
            return f"{n:.1f} {unit}"
    return f"{n:.1f} TB"


def which(name: str) -> str | None:
    return shutil.which(name, path="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin")


def run(argv: list[str], timeout: int = 60, stdin_text: str | None = None) -> subprocess.CompletedProcess | None:
    """Run a command with C locale. Returns None if the program is missing."""
    exe = which(argv[0]) if not argv[0].startswith("/") else argv[0]
    if not exe:
        return None
    env = dict(os.environ)
    env.update(C_ENV)
    try:
        return subprocess.run(
            [exe, *argv[1:]],
            input=stdin_text,
            stdin=None if stdin_text is not None else subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def disk_usage(path: str, one_filesystem: bool = True) -> int:
    """Bytes actually used on disk by a file or folder (like `du -s`).

    Never follows symlinks. Hard-linked files are counted once.
    """
    try:
        st = os.lstat(path)
    except OSError:
        return 0
    if not stat.S_ISDIR(st.st_mode):
        return st.st_blocks * 512
    total = st.st_blocks * 512
    dev = st.st_dev
    seen: set[tuple[int, int]] = set()
    stack = [path]
    while stack:
        current = stack.pop()
        try:
            it = os.scandir(current)
        except OSError:
            continue
        with it:
            for entry in it:
                try:
                    est = entry.stat(follow_symlinks=False)
                except OSError:
                    continue
                if one_filesystem and est.st_dev != dev:
                    continue
                if est.st_nlink > 1 and not stat.S_ISDIR(est.st_mode):
                    key = (est.st_dev, est.st_ino)
                    if key in seen:
                        continue
                    seen.add(key)
                total += est.st_blocks * 512
                if stat.S_ISDIR(est.st_mode):
                    stack.append(entry.path)
    return total


def count_files(path: str) -> int:
    n = 0
    for _root, _dirs, files in os.walk(path):
        n += len(files)
    return n


def running_process_names() -> set[str]:
    """Lower-case names of running programs (comm and argv[0] basename)."""
    names: set[str] = set()
    try:
        pids = [p for p in os.listdir("/proc") if p.isdigit()]
    except OSError:
        return names
    for pid in pids:
        try:
            with open(f"/proc/{pid}/comm", encoding="utf-8", errors="replace") as fh:
                names.add(fh.read().strip().lower())
            with open(f"/proc/{pid}/cmdline", "rb") as fh:
                argv0 = fh.read().split(b"\0", 1)[0].decode("utf-8", "replace")
            if argv0:
                names.add(os.path.basename(argv0).lower())
        except OSError:
            continue
    return names


_SNAP_CGROUP_RE = re.compile(r"snap\.([a-z0-9-]+(?:_[a-z0-9]+)?)\.")


def running_snaps() -> set[str]:
    """Names of snaps that currently have a running process."""
    snaps: set[str] = set()
    try:
        pids = [p for p in os.listdir("/proc") if p.isdigit()]
    except OSError:
        return snaps
    for pid in pids:
        try:
            with open(f"/proc/{pid}/cgroup", encoding="utf-8", errors="replace") as fh:
                for m in _SNAP_CGROUP_RE.finditer(fh.read()):
                    snaps.add(m.group(1))
        except OSError:
            continue
    return snaps


def running_flatpaks() -> set[str]:
    res = run(["flatpak", "ps", "--columns=application"], timeout=15)
    if not res or res.returncode != 0:
        return set()
    return {line.strip() for line in res.stdout.splitlines()
            if line.strip() and line.strip() != "Application"}


def is_systemd() -> bool:
    return os.path.isdir("/run/systemd/system")


def os_pretty_name() -> str:
    try:
        with open("/etc/os-release", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("PRETTY_NAME="):
                    return line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    return "Linux"


@dataclass
class FileSystem:
    mountpoint: str
    device: str
    fstype: str
    total: int
    used: int
    free: int

    @property
    def fraction(self) -> float:
        return self.used / self.total if self.total else 0.0


_SKIP_FS = {"squashfs", "tmpfs", "devtmpfs", "overlay", "proc", "sysfs", "fuse.snapfuse",
            "iso9660", "udf", "nsfs", "ramfs", "efivarfs"}


def filesystems() -> list[FileSystem]:
    """Real, mounted disk filesystems (/, /home, /boot …), one per device."""
    result: list[FileSystem] = []
    seen: set[int] = set()
    try:
        with open("/proc/self/mounts", encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except OSError:
        return result
    for line in lines:
        parts = line.split()
        if len(parts) < 3:
            continue
        device, mountpoint, fstype = parts[0], parts[1].replace("\\040", " "), parts[2]
        if fstype in _SKIP_FS or not device.startswith("/dev/") or device.startswith("/dev/loop"):
            continue
        if mountpoint.startswith(("/snap/", "/var/snap/", "/run/user/")):
            continue
        try:
            st = os.stat(mountpoint)
            usage = shutil.disk_usage(mountpoint)
        except OSError:
            continue
        if st.st_dev in seen:
            continue
        seen.add(st.st_dev)
        result.append(FileSystem(mountpoint, device, fstype, usage.total, usage.used, usage.free))
    result.sort(key=lambda f: (f.mountpoint != "/", f.mountpoint))
    return result


def total_free_bytes() -> int:
    return sum(fs.free for fs in filesystems())
