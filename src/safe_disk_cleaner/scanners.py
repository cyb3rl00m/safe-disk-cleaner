"""Scanners: each one finds reclaimable space in one area and explains it.

Scanners only READ. Nothing here deletes anything.
"""

from __future__ import annotations

import os
import stat
import time
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import unquote, urlparse

from . import policy
from .cache_info import KNOWN_CACHES, SKIP_IN_USER_CACHE, UNKNOWN_DESCRIPTION
from .model import Category, Choice, Explanation, Item, Risk, Setting
from .util import (disk_usage, filesystems, human_size, is_systemd, run, running_flatpaks,
                   running_process_names, running_snaps, which)

MIN_LISTED = 1_000_000   # entries smaller than 1 MB are not listed individually


@dataclass
class ScanContext:
    home: str
    uid: int
    procs: set[str] = field(default_factory=set)
    snaps_running: set[str] = field(default_factory=set)
    flatpaks_running: set[str] = field(default_factory=set)

    @classmethod
    def create(cls) -> "ScanContext":
        return cls(
            home=os.path.expanduser("~"),
            uid=os.getuid(),
            procs=running_process_names(),
            snaps_running=running_snaps(),
            flatpaks_running=running_flatpaks() if which("flatpak") else set(),
        )


def _date(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%d %b %Y")


def _age(ts: float) -> str:
    days = int((time.time() - ts) // 86400)
    if days <= 0:
        return "today"
    if days == 1:
        return "yesterday"
    if days < 60:
        return f"{days} days ago"
    return _date(ts)


def _root_op(op: dict, merge: str | None = None) -> dict:
    return {"kind": "root", "op": op, "merge": merge}


def _user_delete(base: str, paths: list[str]) -> dict:
    return {"kind": "user-delete", "base": base, "paths": paths}


# ==========================================================================
# 1. Old kernels
# ==========================================================================

KERNELS_EXPLANATION = Explanation(
    what=("The kernel is the core of Linux. A kernel update does not replace the old kernel — it installs "
          "a complete new one next to it, so you can still start the previous version from the boot menu "
          "if the new one causes problems."),
    why=("Every kernel update adds roughly 300–500 MB (the kernel, its drivers and headers). Old ones are only "
         "removed when someone runs “apt autoremove”, so they pile up. On computers with a separate /boot "
         "partition they can even fill it up and make updates fail."),
    safe=("Yes, because of two rules this app always follows: it never touches the kernel you are running "
          "right now, and it always keeps at least two kernels — the newest one plus one fallback. Only "
          "versions older than those are offered."),
    how=("Removes all packages of the selected kernel versions with “apt-get purge”. First it asks APT for a "
         "dry run, and if APT would remove anything else than what you see here, it stops."),
    tip="After a kernel update, restart your computer before cleaning so the new kernel is the one in use.",
)


def scan_kernels(ctx: ScanContext) -> Category:
    cat = Category("kernels", "Old kernels", "computer-symbolic", Risk.SAFE, KERNELS_EXPLANATION,
                   needs_admin=True)
    if not which("dpkg-query") or not which("apt-get"):
        cat.available = False
        cat.unavailable_reason = ("Only needed on Debian/Ubuntu-based systems. Fedora keeps just the 3 newest "
                                  "kernels automatically, and Arch keeps only one.")
        return cat
    res = run(["dpkg-query", "-W", "-f=${db:Status-Abbrev}\t${Package}\t${Installed-Size}\n"])
    if not res or res.returncode != 0:
        cat.error = "Could not read the list of installed packages (dpkg-query failed)."
        return cat
    by_version: dict[str, list[tuple[str, int]]] = {}
    held: set[str] = set()
    for line in res.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) != 3 or len(parts[0]) < 2 or parts[0][1] != "i":
            continue                      # only installed packages
        version = policy.kernel_version_of_package(parts[1])
        if version:
            if parts[0][0] == "h":
                held.add(version)         # `apt-mark hold` — the user wants it kept
            size_kib = int(parts[2]) if parts[2].isdigit() else 0
            by_version.setdefault(version, []).append((parts[1], size_kib * 1024))

    running = policy.running_kernel_version(os.uname().release)
    keep, removable = policy.plan_kernels(set(by_version), running, held)
    newest = max(by_version, key=policy.kernel_sort_key) if by_version else None

    kept_desc = []
    for v in sorted(keep, key=policy.kernel_sort_key, reverse=True):
        why = []
        if v == running:
            why.append("running now")
        if v == newest:
            why.append("newest")
        if v in held:
            why.append("on hold")
        if not why:
            why.append("fallback")
        kept_desc.append(f"{v} ({', '.join(why)})")
    if kept_desc:
        cat.info.append("Always kept: " + "; ".join(kept_desc) + ". If you deliberately keep an older "
                        "kernel as a backup, untick it below — or protect it for good with "
                        "“sudo apt-mark hold linux-image-<version>-generic”.")
    if running and newest and running != newest:
        cat.warnings.append(
            f"A newer kernel ({newest}) is installed, but you are still running {running}. Restart the "
            "computer, then scan again — you will probably be able to remove one more kernel.")

    for version in sorted(removable, key=policy.kernel_sort_key, reverse=True):
        pkgs = sorted(by_version[version])
        names = [p for p, _ in pkgs]
        cat.items.append(Item(
            id=f"kernel-{version}",
            title=f"Linux kernel {version}",
            subtitle=f"{len(names)} packages: " + ", ".join(names[:3]) + ("…" if len(names) > 3 else ""),
            size=sum(s for _, s in pkgs),
            risk=Risk.SAFE,
            selected_by_default=True,
            action=_root_op({"op": "remove-packages", "manager": "apt", "purpose": "kernels",
                             "packages": names}, merge="packages"),
        ))

    for fs in filesystems():
        if fs.mountpoint == "/boot" and fs.fraction > 0.8:
            cat.warnings.append(
                f"Your /boot partition is {int(fs.fraction * 100)}% full. Old kernels are the usual cause, and a "
                "full /boot makes kernel updates fail.")
    return cat


# ==========================================================================
# 2. Package manager download cache
# ==========================================================================

def scan_package_cache(ctx: ScanContext) -> Category:
    if which("apt-get") and os.path.isdir("/var/cache/apt"):
        mgr, name, path, cmd = "apt", "APT", "/var/cache/apt/archives", "apt-get clean"
    elif which("dnf") or which("dnf5"):
        mgr, name, path, cmd = "dnf", "DNF", "/var/cache/dnf", "dnf clean packages"
    elif which("pacman"):
        mgr, name, path, cmd = "pacman", "pacman", "/var/cache/pacman/pkg", "paccache -rk2 && paccache -ruk0"
    elif which("zypper"):
        mgr, name, path, cmd = "zypper", "zypper", "/var/cache/zypp/packages", "zypper clean"
    else:
        mgr = name = path = cmd = ""

    explanation = Explanation(
        what=(f"Before {name or 'your package manager'} installs or updates software, it downloads the package "
              f"files and keeps a copy in {path or 'its cache folder'}."),
        why=("Once the software is installed these copies are not needed anymore, but some update tools keep "
             "them until somebody cleans up. After months of updates this can reach hundreds of MB or more."),
        safe=("Yes. Your installed programs never use these files. The only downside: if you reinstall the exact "
              "same version of something, it is downloaded again."),
        how=f"Runs “{cmd}”." if cmd else "",
    )
    cat = Category("package-cache", "Downloaded update files", "folder-download-symbolic", Risk.SAFE,
                   explanation, needs_admin=True)
    if not mgr:
        cat.available = False
        cat.unavailable_reason = "No supported package manager (APT, DNF, pacman, zypper) was found."
        return cat

    if mgr == "apt":
        debs = [e for e in _scandir_files("/var/cache/apt/archives") if e.name.endswith(".deb")]
        partial = disk_usage("/var/cache/apt/archives/partial")
        bins = sum(_blocks(e) for e in _scandir_files("/var/cache/apt") if e.name.endswith(".bin"))
        size = sum(_blocks(e) for e in debs) + partial + bins
        sub = f"{len(debs)} downloaded package file{'s' if len(debs) != 1 else ''} (.deb)"
        if bins:
            sub += " + APT's quick-lookup files (rebuilt automatically)"
        if not debs and size < MIN_LISTED:
            size = 0
    elif mgr == "dnf":
        rpms = []
        for base in ("/var/cache/dnf", "/var/cache/libdnf5"):
            for root, _dirs, files in os.walk(base):
                rpms += [os.path.join(root, f) for f in files if f.endswith(".rpm")]
        size = sum(_blocks_path(p) for p in rpms)
        sub = f"{len(rpms)} downloaded package files (.rpm)"
    elif mgr == "pacman":
        if not which("paccache"):
            size = disk_usage(path)
            cat.items.append(Item(
                id="pkg-cache", title="Downloaded package files", size=size,
                subtitle=path, blocked=True,
                note="Install “pacman-contrib” (it provides paccache) so old packages can be removed safely "
                     "while keeping the newest versions for downgrades."))
            return cat
        size = 0
        for args in (["paccache", "-dk2"], ["paccache", "-duk0"]):
            res = run(args)
            if res:
                for line in (res.stdout + res.stderr).splitlines():
                    if "disk space saved:" in line:
                        size += policy.parse_size(line.split("disk space saved:")[1].strip(" )")) or 0
        sub = "Older versions of packages (the 2 newest of each are kept for downgrades)"
    else:
        size = disk_usage(path)
        sub = path

    if size > 0:
        cat.items.append(Item(
            id="pkg-cache", title="Downloaded package files", size=size, subtitle=sub,
            risk=Risk.SAFE, selected_by_default=True,
            action=_root_op({"op": "pkg-cache-clean", "manager": mgr})))
    else:
        cat.info.append("The download cache is already empty.")
    return cat


def _scandir_files(path: str):
    try:
        with os.scandir(path) as it:
            return [e for e in it if e.is_file(follow_symlinks=False)]
    except OSError:
        return []


def _blocks(entry: os.DirEntry) -> int:
    try:
        return entry.stat(follow_symlinks=False).st_blocks * 512
    except OSError:
        return 0


def _blocks_path(path: str) -> int:
    try:
        return os.lstat(path).st_blocks * 512
    except OSError:
        return 0


# ==========================================================================
# 3. Packages that are no longer needed
# ==========================================================================

UNUSED_EXPLANATION = Explanation(
    what=("Packages that were installed automatically because another program needed them — libraries and "
          "helpers. The program that needed them has since been removed or doesn't need them anymore."),
    why=("When you uninstall a program, the extra packages it pulled in stay behind until someone removes them "
         "(“apt autoremove”)."),
    safe=("Usually. Nothing you installed yourself is in this list — only things the package manager installed "
          "as dependencies and now considers orphaned. Rarely, you use one of them directly (for example a "
          "command-line tool); if you recognise something you use, leave it unchecked."),
    how=("Removes exactly the selected packages. On APT systems it first does a dry run and stops if APT would "
         "remove anything that is not in your selection."),
    tip=("To keep a package that shows up here for good, mark it as installed by you: "
         "sudo apt-mark manual <package>"),
)

CRITICAL_PACKAGES = {
    "gnome-shell", "gdm3", "mutter", "nautilus", "ubuntu-desktop", "ubuntu-desktop-minimal", "ubuntu-session",
    "xserver-xorg-core", "xwayland", "network-manager", "networkmanager", "sudo", "systemd", "grub-pc",
    "grub-efi-amd64", "grub-efi-amd64-signed", "shim-signed", "linux-firmware", "pipewire", "wireplumber",
    "pulseaudio", "sddm", "lightdm", "plasma-desktop", "plasma-workspace", "kwin-wayland", "cinnamon",
    "xfce4-session", "bluez", "snapd", "flatpak", "apt", "dpkg", "gnome-control-center", "gnome-terminal",
    "ptyxis", "polkitd", "policykit-1", "dbus", "udisks2", "plymouth", "cups", "mesa-vulkan-drivers",
    "libgl1-mesa-dri", "xdg-desktop-portal", "xdg-desktop-portal-gnome", "gnome-session",
}


def scan_unused_packages(ctx: ScanContext) -> Category:
    cat = Category("unused-packages", "Packages no longer needed", "package-x-generic-symbolic",
                   Risk.CAUTION, UNUSED_EXPLANATION, needs_admin=True)
    entries: list[tuple[str, int, str]] = []   # name, size, summary
    mgr = ""
    if which("apt-get") and which("dpkg-query"):
        mgr = "apt"
        res = run(["apt-get", "-s", "autoremove"], timeout=120)
        if not res:
            cat.error = "APT could not be asked which packages are unused."
            return cat
        names = [n for n in policy.parse_apt_simulation(res.stdout)
                 if not policy.kernel_version_of_package(n)]
        if names:
            q = run(["dpkg-query", "-W", "-f=${Package}:${Architecture}\t${Package}\t${Installed-Size}\t"
                                          "${binary:Summary}\n", *names])
            info: dict[str, tuple[int, str]] = {}
            if q:
                for line in q.stdout.splitlines():
                    p = line.split("\t")
                    if len(p) == 4:
                        size = int(p[2]) * 1024 if p[2].isdigit() else 0
                        info[p[0]] = info[p[1]] = (size, p[3])
            for n in names:
                size, summary = info.get(n, (0, ""))
                entries.append((n, size, summary))
        if any(policy.kernel_version_of_package(n) for n in policy.parse_apt_simulation(res.stdout)):
            cat.info.append("Old kernels that APT would also remove are listed under “Old kernels” instead.")
    elif which("dnf") or which("dnf5"):
        mgr = "dnf"
        res = run(["dnf", "repoquery", "--unneeded", "--installed", "--queryformat", "%{name}\n"], timeout=180)
        names = sorted({line.strip() for line in (res.stdout if res else "").splitlines()
                        if line.strip() and policy.GENERIC_PKG_RE.match(line.strip())})
        if names:
            q = run(["rpm", "-q", "--qf", "%{NAME}\t%{SIZE}\t%{SUMMARY}\n", *names])
            for line in (q.stdout if q else "").splitlines():
                p = line.split("\t")
                if len(p) == 3:
                    entries.append((p[0], int(p[1]) if p[1].isdigit() else 0, p[2]))
    elif which("pacman"):
        mgr = "pacman"
        res = run(["pacman", "-Qdtq"])
        names = [n.strip() for n in (res.stdout if res else "").splitlines() if n.strip()]
        if names:
            q = run(["pacman", "-Qi", *names])
            cur: dict[str, str] = {}
            for line in (q.stdout if q else "").splitlines() + [""]:
                if not line.strip():
                    if "Name" in cur:
                        entries.append((cur["Name"], policy.parse_size(cur.get("Installed Size", "")) or 0,
                                        cur.get("Description", "")))
                    cur = {}
                elif " : " in line:
                    k, v = line.split(" : ", 1)
                    cur[k.strip()] = v.strip()
    else:
        cat.available = False
        cat.unavailable_reason = "No supported package manager (APT, DNF, pacman) was found."
        return cat

    critical = [n for n, _, _ in entries if n.split(":")[0] in CRITICAL_PACKAGES]
    unusual = bool(critical) or len(entries) > 60
    if unusual:
        cat.risk = Risk.REVIEW
        shown = ", ".join(critical[:5]) if critical else f"{len(entries)} packages"
        cat.warnings.append(
            f"This list looks unusual ({shown}). That normally happens after a “meta-package” such as "
            "ubuntu-desktop was removed: the package manager then thinks everything it brought in is no "
            "longer needed. Do NOT clean this unless you know why it happened. To keep packages, run: "
            "sudo apt-mark manual <package>")
    for name, size, summary in sorted(entries, key=lambda e: -e[1]):
        is_critical = name.split(":")[0] in CRITICAL_PACKAGES
        cat.items.append(Item(
            id=f"pkg-{name}",
            title=name,
            subtitle=summary or "No description",
            size=size,
            risk=Risk.REVIEW if (is_critical or unusual) else Risk.CAUTION,
            selected_by_default=False,
            note="Important system package — keep it unless you are sure." if is_critical else "",
            action=_root_op({"op": "remove-packages", "manager": mgr, "purpose": "autoremove",
                             "packages": [name]}, merge="packages"),
        ))
    if not entries:
        cat.info.append("No orphaned packages — nothing to remove.")
    return cat


# ==========================================================================
# 4. Old Snap versions
# ==========================================================================

SNAP_EXPLANATION = Explanation(
    what=("Snap apps (on Ubuntu for example Firefox, Thunderbird and the Snap Store) are stored as compressed "
          "images. When a snap updates, the previous version is kept on disk — disabled — so you can go back "
          "with “snap revert” if the update breaks something."),
    why=("snapd keeps 2 versions of every snap by default (3 on some systems). Big snaps such as GNOME "
         "platform runtimes, browsers or Steam waste hundreds of MB per old version."),
    safe=("Yes. Only DISABLED versions are listed — they are not running and not used by anything. You only "
          "lose the option to roll back to that exact version (together with that old version's data snapshot)."),
    how="Runs “snap remove <name> --revision=<number>” for each selected old version.",
    tip=("Below you can choose how many versions snapd keeps from now on. 2 is the lowest value snapd allows "
         "(the current one plus one to roll back to)."),
)


def scan_snaps(ctx: ScanContext) -> Category:
    cat = Category("snaps", "Old Snap versions", "software-update-available-symbolic", Risk.SAFE,
                   SNAP_EXPLANATION, needs_admin=True)
    if not which("snap"):
        cat.available = False
        cat.unavailable_reason = "Snap is not installed on this system."
        return cat
    res = run(["snap", "list", "--all"], timeout=60)
    if res and res.returncode != 0 and "No snaps" in res.stderr:
        cat.info.append("No snaps are installed.")
        return cat
    if not res or res.returncode != 0:
        cat.error = "Could not talk to snapd (is the snapd service running?)."
        return cat
    for row in policy.parse_snap_list_all(res.stdout):
        if "disabled" not in row["notes"].split(","):
            continue
        name, rev = row["name"], row["rev"]
        if not (policy.SNAP_NAME_RE.match(name) and policy.SNAP_REV_RE.match(rev)):
            continue
        path = f"/var/lib/snapd/snaps/{name}_{rev}.snap"
        cat.items.append(Item(
            id=f"snap-{name}-{rev}",
            title=f"{name} — old version {row['version']}",
            subtitle=f"Revision {rev} · disabled, only kept so you could roll back",
            size=_blocks_path(path) or (os.path.getsize(path) if os.path.exists(path) else 0),
            risk=Risk.SAFE, selected_by_default=True,
            action=_root_op({"op": "snap-remove-revision", "name": name, "revision": rev}),
        ))
    cat.items.sort(key=lambda i: -i.size)
    if not cat.items:
        cat.info.append("No old snap versions are stored.")

    current = ""
    got = run(["snap", "get", "system", "refresh.retain"], timeout=20)
    if got and got.returncode == 0:
        current = got.stdout.strip()
    cat.settings.append(Setting(
        id="snap-retain", title="Versions snapd keeps of each snap",
        subtitle=f"Currently: {current or 'system default (2 on desktops, 3 on Ubuntu Core)'}",
        choices=[("2 — current + 1 to roll back (smallest)", "2"), ("3 versions", "3")],
        current=current, op="snap-set-retain", param="value"))
    return cat


# ==========================================================================
# 5. Unused Flatpak runtimes
# ==========================================================================

FLATPAK_EXPLANATION = Explanation(
    what=("Flatpak apps don't use your system's libraries. They run on shared “runtimes” (for example GNOME 48, "
          "KDE 6 or Freedesktop 24.08), plus extras such as translations and graphics drivers. When your apps "
          "move to a newer runtime, the old one is left behind."),
    why="Runtimes are big — often 300 MB to 1 GB each — and Flatpak doesn't remove the old ones by itself.",
    safe=("Yes. The list comes from Flatpak itself (the same check as “flatpak uninstall --unused”): only "
          "runtimes and extensions that no installed app needs. Pinned runtimes are never included. Sizes are "
          "Flatpak's own numbers; because Flatpak shares identical files between runtimes, the space actually "
          "freed can be a little smaller."),
    how=("Runs “flatpak uninstall --noninteractive <runtime>” for each selected runtime. System-wide runtimes "
         "need your password, your personal ones don't. Before removing, the list is checked again."),
)


def _flatpak_kind(fid: str) -> str:
    if fid.endswith(".Locale"):
        return "Translations"
    if ".GL." in fid or ".GL32." in fid or fid.endswith(".GL"):
        return "Graphics driver for Flatpak apps"
    if fid.endswith(".Debug"):
        return "Debugging symbols (for developers)"
    if fid.endswith(".Sdk") or ".Sdk." in fid:
        return "Developer kit (for building apps)"
    if ".Codecs" in fid or "ffmpeg" in fid.lower():
        return "Audio/video codecs"
    if ".Platform" in fid or ".Runtime" in fid:
        return "Shared runtime"
    return "Runtime extension"


def _flatpak_unused_gi() -> list[dict] | None:
    try:
        import gi
        gi.require_version("Flatpak", "1.0")
        from gi.repository import Flatpak  # type: ignore
    except (ImportError, ValueError):
        return None
    out: list[dict] = []
    for kind in ("system", "user"):
        try:
            inst = (Flatpak.Installation.new_system(None) if kind == "system"
                    else Flatpak.Installation.new_user(None))
            refs = inst.list_unused_refs(None, None)
        except Exception:  # noqa: BLE001 - libflatpak raises GLib.Error
            continue
        for r in refs:
            out.append({"installation": kind, "ref": r.format_ref(), "id": r.get_name(),
                        "branch": r.get_branch(), "size": int(r.get_installed_size() or 0)})
    return out


def _flatpak_unused_cli() -> list[dict]:
    out: list[dict] = []
    for kind in ("system", "user"):
        res = run(["flatpak", "uninstall", "--unused", f"--{kind}"], timeout=60)
        if not res:
            continue
        rows = policy.parse_flatpak_unused(res.stdout)
        if not rows:
            continue
        sizes: dict[tuple[str, str], tuple[str, int]] = {}
        listing = run(["flatpak", "list", "--all", f"--{kind}", "--columns=ref,size"], timeout=60)
        for line in (listing.stdout if listing else "").splitlines():
            cells = line.split("\t")
            if len(cells) >= 2 and cells[0].count("/") == 2:
                rid, arch, branch = cells[0].split("/")
                sizes[(rid, branch)] = (arch, policy.parse_size(cells[1]) or 0)
        for row in rows:
            arch, size = sizes.get((row["id"], row["branch"]), (row["arch"] or os.uname().machine, 0))
            arch = row["arch"] or arch
            out.append({"installation": kind, "ref": f"runtime/{row['id']}/{arch}/{row['branch']}",
                        "id": row["id"], "branch": row["branch"], "size": size})
    return out


def scan_flatpak(ctx: ScanContext) -> Category:
    cat = Category("flatpak", "Unused Flatpak runtimes", "application-x-addon-symbolic", Risk.SAFE,
                   FLATPAK_EXPLANATION)
    if not which("flatpak"):
        cat.available = False
        cat.unavailable_reason = "Flatpak is not installed on this system."
        return cat
    found = _flatpak_unused_gi()
    if found is None:
        found = _flatpak_unused_cli()
    for f in sorted(found, key=lambda x: -x["size"]):
        if not policy.FLATPAK_REF_RE.match(f["ref"]):
            continue
        where = "system-wide" if f["installation"] == "system" else "your user only"
        if f["installation"] == "system":
            cat.needs_admin = True
            action = _root_op({"op": "flatpak-uninstall", "refs": [f["ref"]]}, merge="refs")
        else:
            action = {"kind": "flatpak-user", "refs": [f["ref"]]}
        cat.items.append(Item(
            id=f"flatpak-{f['installation']}-{f['ref']}",
            title=f"{f['id']} ({f['branch']})",
            subtitle=f"{_flatpak_kind(f['id'])} · installed {where}",
            size=f["size"], risk=Risk.SAFE, selected_by_default=True, action=action))
    if not cat.items:
        cat.info.append("Every installed Flatpak runtime is used by an app.")
    return cat


# ==========================================================================
# 6. System journal
# ==========================================================================

JOURNAL_EXPLANATION = Explanation(
    what=("The systemd journal is the system log: messages from the kernel, services and apps — the thing you "
          "read with “journalctl” when troubleshooting crashes or errors."),
    why=("By default the journal may use up to 10% of the disk (at most 4 GB) and keeps months of history. "
         "A service that logs errors over and over can fill it quickly."),
    safe=("Yes. Only the OLDEST archived log files are deleted until the journal is down to the size you "
          "pick. Recent logs — including everything from the current boot — are always kept. If you are "
          "investigating a problem from weeks ago, choose a bigger size or wait."),
    how="Runs “journalctl --vacuum-size=<size>”.",
    tip="Set a permanent size limit below, so the journal never grows this big again.",
)

JOURNAL_CONF = "/etc/systemd/journald.conf.d/60-safe-disk-cleaner.conf"


def _journal_files() -> list[tuple[str, int, float, bool]]:
    files = []
    for base in ("/var/log/journal", "/run/log/journal"):
        for root, _dirs, names in os.walk(base):
            for n in names:
                if not (n.endswith(".journal") or n.endswith(".journal~")):
                    continue
                p = os.path.join(root, n)
                try:
                    st = os.lstat(p)
                except OSError:
                    continue
                archived = "@" in n or n.endswith("~")
                files.append((p, st.st_blocks * 512, st.st_mtime, archived))
    return files


def _journald_limit() -> tuple[str, str]:
    """(value set by this app, value configured anywhere) for SystemMaxUse."""
    ours, effective = "", ""
    paths = ["/usr/lib/systemd/journald.conf", "/etc/systemd/journald.conf"]
    for d in ("/usr/lib/systemd/journald.conf.d", "/etc/systemd/journald.conf.d", "/run/systemd/journald.conf.d"):
        try:
            paths += sorted(os.path.join(d, f) for f in os.listdir(d) if f.endswith(".conf"))
        except OSError:
            pass
    for p in paths:
        try:
            with open(p, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line.startswith("SystemMaxUse="):
                        effective = line.split("=", 1)[1].strip()
                        if p == JOURNAL_CONF:
                            ours = effective
        except OSError:
            continue
    return ours, effective


def journal_vacuum_estimate(files: list[tuple[str, int, float, bool]], target: int) -> int:
    total = sum(f[1] for f in files)
    freed = 0
    for _p, size, _mtime, archived in sorted(files, key=lambda f: f[2]):
        if total <= target:
            break
        if archived:
            total -= size
            freed += size
    return freed


def scan_journal(ctx: ScanContext) -> Category:
    cat = Category("journal", "System journal (logs)", "text-x-generic-symbolic", Risk.SAFE,
                   JOURNAL_EXPLANATION, needs_admin=True)
    if not is_systemd() or not which("journalctl"):
        cat.available = False
        cat.unavailable_reason = "This system does not run systemd-journald."
        return cat
    files = _journal_files()
    total = sum(f[1] for f in files)
    archived = [f for f in files if f[3]]
    oldest = min((f[2] for f in files), default=None)
    if oldest:
        cat.info.append(f"The journal uses {human_size(total)} and goes back to {_date(oldest)}.")

    choices = []
    for value in policy.JOURNAL_VACUUM_SIZES:
        target = policy.parse_size(value) or 0
        choices.append(Choice(f"Keep the newest {human_size(target)}", value,
                              journal_vacuum_estimate(files, target)))
    default = 2  # 500M: gentle default, keeps a good amount of history
    if any(c.size for c in choices):
        cat.items.append(Item(
            id="journal-vacuum", title="Old entries in the system journal",
            subtitle=f"{len(archived)} archived log files; the newest logs are always kept",
            size=choices[default].size, risk=Risk.SAFE, selected_by_default=choices[default].size > 0,
            choices=choices, default_choice=default,
            action=_root_op({"op": "journal-vacuum", "size": choices[default].value})))
    else:
        cat.info.append("The journal is already small.")

    ours, effective = _journald_limit()
    current = ours or ("" if effective else "default")
    subtitle = ("Currently: system default (10% of the disk, at most 4 GB)" if not effective
                else f"Currently: {effective}" + ("" if ours else " (set by your system configuration)"))
    cat.settings.append(Setting(
        id="journal-limit", title="Maximum journal size", subtitle=subtitle,
        choices=[("200 MB", "200M"), ("500 MB (recommended)", "500M"), ("1 GB", "1G"), ("2 GB", "2G"),
                 ("System default", "default")],
        current=current, op="journal-set-limit", param="size"))
    return cat


# ==========================================================================
# 7. Crash reports and core dumps
# ==========================================================================

CRASH_EXPLANATION = Explanation(
    what=("When a program crashes, Linux saves a snapshot of its memory (a “core dump”), and Ubuntu's crash "
          "reporter Apport writes a crash report. Developers and crash-report tools use them to find out why "
          "the crash happened."),
    why=("Each crash can leave tens or hundreds of MB, and they are cleaned up only slowly (or never). A program "
         "that keeps crashing can fill the disk."),
    safe=("Yes. They are only useful for diagnosing a crash. If you are trying to fix a crash right now, look at "
          "the newest ones first (for example with “coredumpctl list” or a crash report viewer), then clean."),
    how=("Deletes the selected files from /var/crash and /var/lib/systemd/coredump. They belong to the system, "
         "so your password is needed."),
)


def scan_crashes(ctx: ScanContext) -> Category:
    cat = Category("crashes", "Crash reports & core dumps", "dialog-warning-symbolic", Risk.SAFE,
                   CRASH_EXPLANATION, needs_admin=True)
    groups: dict[str, list[os.DirEntry]] = {}
    for e in _scandir_files("/var/crash"):
        if policy.APPORT_CRASH_RE.match(e.name):
            groups.setdefault(e.name.rsplit(".", 1)[0], []).append(e)
    for stem, entries in groups.items():
        prog = stem.rsplit(".", 1)[0].replace("_", "/") if "." in stem else stem
        mtime = max(_mtime(e) for e in entries)
        cat.items.append(Item(
            id=f"crash-{stem}", title=os.path.basename(prog) or stem,
            subtitle=f"Crash report for {prog} · {_age(mtime)}",
            size=sum(_blocks(e) for e in entries), risk=Risk.SAFE, selected_by_default=True,
            action=_root_op({"op": "delete-files", "group": "crash",
                             "paths": [e.path for e in entries]}, merge="paths")))
    for e in _scandir_files("/var/lib/systemd/coredump"):
        if not policy.COREDUMP_RE.match(e.name):
            continue
        parts = e.name.split(".")
        comm = parts[1].replace("\\x2d", "-") if len(parts) > 1 else e.name
        cat.items.append(Item(
            id=f"core-{e.name}", title=comm,
            subtitle=f"Core dump (memory snapshot) · {_age(_mtime(e))}",
            size=_blocks(e), risk=Risk.SAFE, selected_by_default=True,
            action=_root_op({"op": "delete-files", "group": "coredump", "paths": [e.path]}, merge="paths")))
    cat.items.sort(key=lambda i: -i.size)
    if not cat.items:
        cat.info.append("No crash reports or core dumps are stored.")
    return cat


def _mtime(e: os.DirEntry) -> float:
    try:
        return e.stat(follow_symlinks=False).st_mtime
    except OSError:
        return 0.0


# ==========================================================================
# 8. Old rotated log files
# ==========================================================================

LOGS_EXPLANATION = Explanation(
    what=("Classic text logs in /var/log (syslog, kern.log, auth.log, dpkg.log …). Every week or so a tool "
          "called logrotate renames the current log to “.1” and compresses older ones (“.2.gz”, “.3.gz” …)."),
    why="Old copies are kept for several weeks. Usually small, but a noisy program can create big ones.",
    safe=("Yes. Only old, rotated copies are deleted. The current log files that are still being written to are "
          "never touched."),
    how="Deletes old copies such as syslog.1, kern.log.2.gz or messages-20261001 from /var/log.",
)


def scan_rotated_logs(ctx: ScanContext) -> Category:
    cat = Category("rotated-logs", "Old log files", "document-open-recent-symbolic", Risk.SAFE,
                   LOGS_EXPLANATION, needs_admin=True)
    paths, size, unreadable = [], 0, 0
    for root, dirs, files in os.walk("/var/log", onerror=lambda _e: None):
        if root == "/var/log" and "journal" in dirs:
            dirs.remove("journal")
        unreadable += sum(1 for d in dirs if not os.access(os.path.join(root, d), os.R_OK | os.X_OK))
        for f in files:
            if policy.ROTATED_LOG_RE.match(f):
                p = os.path.join(root, f)
                try:
                    st = os.lstat(p)
                except OSError:
                    continue
                if stat.S_ISREG(st.st_mode) and time.time() - st.st_mtime > policy.LIVE_LOG_SECONDS:
                    paths.append(p)
                    size += st.st_blocks * 512
    if paths:
        cat.items.append(Item(
            id="rotated-logs", title="Old, rotated log files", subtitle=f"{len(paths)} file{'s' if len(paths) != 1 else ''} in /var/log",
            size=size, risk=Risk.SAFE, selected_by_default=True,
            action=_root_op({"op": "delete-files", "group": "rotated-logs", "paths": paths}, merge="paths")))
    else:
        cat.info.append("No old log copies were found.")
    if unreadable:
        cat.size_is_estimate = True
        cat.info.append(f"{unreadable} log folders can only be read by the administrator, so a few files may "
                        "not be counted.")
    return cat


# ==========================================================================
# 9. Thumbnails
# ==========================================================================

THUMBS_EXPLANATION = Explanation(
    what=("Small preview pictures that your file manager and image viewers create for photos, videos and "
          "documents, so folders can show previews instantly. They live in ~/.cache/thumbnails."),
    why=("Previews are never cleaned up automatically — not even when you delete the original file. After years "
         "of photos, downloads and USB sticks, this folder can grow to several GB."),
    safe=("Yes. This app reads every preview to find out which file it belongs to, so it can delete only the "
          "previews of files that no longer exist and keep the useful ones. Previews of files on USB drives that "
          "aren't plugged in right now are kept."),
    how="Deletes the selected preview images from ~/.cache/thumbnails. No password needed.",
)


def png_thumb_uri(path: str) -> str | None:
    """Read the Thumb::URI text chunk from a freedesktop thumbnail PNG."""
    try:
        with open(path, "rb") as fh:
            if fh.read(8) != b"\x89PNG\r\n\x1a\n":
                return None
            for _ in range(64):
                head = fh.read(8)
                if len(head) < 8:
                    return None
                length = int.from_bytes(head[:4], "big")
                ctype = head[4:]
                if ctype == b"IDAT" or ctype == b"IEND":
                    return None
                if ctype in (b"tEXt", b"iTXt") and length < 65536:
                    data = fh.read(length)
                    key, _, value = data.partition(b"\0")
                    if key == b"Thumb::URI":
                        if ctype == b"iTXt":
                            # compression flag, method, language\0, translated keyword\0, text
                            value = value[2:].split(b"\0", 2)[-1]
                        return value.decode("utf-8", "replace")
                    fh.seek(4, 1)
                else:
                    fh.seek(length + 4, 1)
    except OSError:
        return None
    return None


def _removable_root(path: str) -> str | None:
    parts = path.split("/")
    if path.startswith("/media/") and len(parts) > 3:
        return "/".join(parts[:4])
    if path.startswith("/run/media/") and len(parts) > 4:
        return "/".join(parts[:5])
    if path.startswith("/mnt/") and len(parts) > 2:
        return "/".join(parts[:3])
    return None


def thumbnail_status(uri: str | None) -> str:
    """'orphan' (original is gone), 'kept' (exists or unknown) or 'offline' (on an unplugged drive)."""
    if not uri or not uri.startswith("file://"):
        return "kept"
    path = unquote(urlparse(uri).path)
    if os.path.lexists(path):
        return "kept"
    root = _removable_root(path)
    if root and not os.path.ismount(root):
        return "offline"
    return "orphan"


def scan_thumbnails(ctx: ScanContext) -> Category:
    cat = Category("thumbnails", "Picture previews", "image-x-generic-symbolic", Risk.SAFE, THUMBS_EXPLANATION)
    base = os.path.join(ctx.home, ".cache", "thumbnails")
    orphan_paths, orphan_size, other_paths, other_size, offline = [], 0, [], 0, 0
    try:
        subs = [e for e in os.scandir(base) if e.is_dir(follow_symlinks=False)]
    except OSError:
        subs = []
    for sub in subs:
        for e in _scandir_files(sub.path):
            size = _blocks(e)
            if sub.name == "fail" or not e.name.endswith(".png"):
                other_paths.append(e.path)
                other_size += size
                continue
            status = thumbnail_status(png_thumb_uri(e.path))
            if status == "orphan":
                orphan_paths.append(e.path)
                orphan_size += size
            else:
                if status == "offline":
                    offline += 1
                other_paths.append(e.path)
                other_size += size
        if sub.name == "fail":
            for root, _d, files in os.walk(sub.path):
                for f in files:
                    p = os.path.join(root, f)
                    if p not in other_paths:
                        other_paths.append(p)
                        other_size += _blocks_path(p)
    if orphan_paths:
        cat.items.append(Item(
            id="thumbs-orphan", title="Previews of files that no longer exist",
            subtitle=f"{len(orphan_paths)} previews of deleted or moved files — useless now",
            size=orphan_size, risk=Risk.SAFE, selected_by_default=True,
            action=_user_delete(base, orphan_paths)))
    if other_paths:
        cat.items.append(Item(
            id="thumbs-other", title="All other previews",
            subtitle=(f"{len(other_paths)} previews of files you still have. Your file manager recreates them "
                      "as you browse — photo folders will be slow to show previews at first"),
            size=other_size, risk=Risk.CAUTION, selected_by_default=False,
            action=_user_delete(base, other_paths)))
    if offline:
        cat.info.append(f"{offline} previews belong to drives that aren't plugged in; they are kept as “other "
                        "previews”.")
    if not cat.items:
        cat.info.append("There are no stored previews.")
    return cat


# ==========================================================================
# 10. User cache (~/.cache)
# ==========================================================================

CACHE_EXPLANATION = Explanation(
    what=("~/.cache is the folder where programs keep temporary data they can rebuild: downloads, compiled "
          "shaders, web caches, search indexes. Your documents, photos and settings are never stored here."),
    why=("Programs may fill it freely, and many never clean up after themselves. Programs you uninstalled often "
         "leave their cache behind forever."),
    safe=("Usually. Every folder is explained below and has its own risk label. In the worst case a program is "
          "slower the first time or downloads something again. Programs that are running right now are locked, "
          "so you can't break them while they're in use."),
    how="Deletes the selected folders inside ~/.cache. No password needed.",
)

_RISK = {"safe": Risk.SAFE, "caution": Risk.CAUTION, "review": Risk.REVIEW}


def scan_user_cache(ctx: ScanContext) -> Category:
    cat = Category("user-cache", "App caches in your home", "folder-symbolic", Risk.CAUTION, CACHE_EXPLANATION)
    base = os.path.join(ctx.home, ".cache")
    try:
        entries = list(os.scandir(base))
    except OSError:
        entries = []
    for e in entries:
        if e.name in SKIP_IN_USER_CACHE or e.is_symlink():
            continue
        size = disk_usage(e.path)
        if size < MIN_LISTED:
            cat.hidden_small += 1
            continue
        label, desc, risk, procs = KNOWN_CACHES.get(
            e.name, (e.name, UNKNOWN_DESCRIPTION.format(name=e.name), "caution", (e.name.lower(),)))
        running = [p for p in procs if p.lower() in ctx.procs]
        item = Item(
            id=f"cache-{e.name}", title=label if label == e.name else f"{label}",
            subtitle=f"~/.cache/{e.name} — {desc}", size=size, risk=_RISK[risk],
            selected_by_default=_RISK[risk] is Risk.SAFE and not running,
            action=_user_delete(base, [e.path]))
        if running:
            item.blocked = True
            item.note = f"“{running[0]}” is running — close it, then scan again."
        cat.items.append(item)
    cat.items.sort(key=lambda i: -i.size)
    if cat.hidden_small:
        cat.info.append(f"{cat.hidden_small} small folders (under 1 MB each) are not listed.")
    return cat


# ==========================================================================
# 11. Flatpak / Snap app caches
# ==========================================================================

APPCACHE_EXPLANATION = Explanation(
    what=("Apps installed as Flatpak or Snap are sandboxed, so they keep their own private cache instead of "
          "using ~/.cache — Flatpak apps in ~/.var/app/<app>/cache, Snap apps in ~/snap/<app>/common/.cache."),
    why="Just like normal caches, nothing cleans them up automatically.",
    safe=("Usually. Same as other caches: the app may be a bit slower the first time afterwards. Your settings and "
          "files of these apps are stored elsewhere and are not touched. Running apps are locked."),
    how="Deletes the cache folder of each selected app. No password needed.",
)


def scan_app_caches(ctx: ScanContext) -> Category:
    cat = Category("app-caches", "Flatpak & Snap app caches", "application-x-executable-symbolic",
                   Risk.CAUTION, APPCACHE_EXPLANATION)
    var_app = os.path.join(ctx.home, ".var", "app")
    try:
        flatpak_apps = sorted(e.name for e in os.scandir(var_app) if e.is_dir(follow_symlinks=False))
    except OSError:
        flatpak_apps = []
    for app in flatpak_apps:
        base = os.path.join(var_app, app)
        path = os.path.join(base, "cache")
        if not os.path.isdir(path) or os.path.islink(path):
            continue
        size = disk_usage(path)
        if size < MIN_LISTED:
            cat.hidden_small += 1
            continue
        short = app.rsplit(".", 1)[-1]
        item = Item(id=f"fp-cache-{app}", title=short, subtitle=f"Flatpak app {app} · ~/.var/app/{app}/cache",
                    size=size, risk=Risk.CAUTION, action=_user_delete(base, [path]))
        if app in ctx.flatpaks_running:
            item.blocked, item.note = True, f"{short} is running — close it, then scan again."
        cat.items.append(item)

    snap_home = os.path.join(ctx.home, "snap")
    try:
        snaps = sorted(e.name for e in os.scandir(snap_home) if e.is_dir(follow_symlinks=False))
    except OSError:
        snaps = []
    for name in snaps:
        base = os.path.join(snap_home, name, "common")
        path = os.path.join(base, ".cache")
        if not os.path.isdir(path) or os.path.islink(path):
            continue
        size = disk_usage(path)
        if size < MIN_LISTED:
            cat.hidden_small += 1
            continue
        item = Item(id=f"snap-cache-{name}", title=name, subtitle=f"Snap app · ~/snap/{name}/common/.cache",
                    size=size, risk=Risk.CAUTION, action=_user_delete(base, [path]))
        if name in ctx.snaps_running:
            item.blocked, item.note = True, f"{name} is running — close it, then scan again."
        cat.items.append(item)
    cat.items.sort(key=lambda i: -i.size)
    if not (flatpak_apps or snaps):
        cat.available = False
        cat.unavailable_reason = "No Flatpak or Snap apps have stored anything in your home folder."
    elif cat.hidden_small:
        cat.info.append(f"{cat.hidden_small} apps with caches under 1 MB are not listed.")
    return cat


# ==========================================================================
# 12. Trash
# ==========================================================================

TRASH_EXPLANATION = Explanation(
    what=("Files you delete in the file manager aren't really gone — they are moved to the Trash so you can "
          "restore them."),
    why="The Trash only empties when you empty it. Deleted videos, downloads and ISO files can quietly take many GB.",
    safe=("Review first: once emptied, these files can't be restored. Each item shows where it came from and when "
          "you deleted it."),
    how="Permanently deletes the selected items from your Trash (including Trash folders on other drives).",
)


def _trash_dirs(ctx: ScanContext) -> list[str]:
    dirs = [os.path.join(ctx.home, ".local", "share", "Trash")]
    for fs in filesystems():
        if fs.mountpoint != "/":
            d = os.path.join(fs.mountpoint, f".Trash-{ctx.uid}")
            if os.path.isdir(d) and not os.path.islink(d):
                dirs.append(d)
    return [d for d in dirs if os.path.isdir(os.path.join(d, "files"))]


def scan_trash(ctx: ScanContext) -> Category:
    cat = Category("trash", "Trash", "user-trash-symbolic", Risk.REVIEW, TRASH_EXPLANATION)
    found: list[Item] = []
    for tdir in _trash_dirs(ctx):
        files_dir = os.path.join(tdir, "files")
        info_dir = os.path.join(tdir, "info")
        try:
            names = os.listdir(files_dir)
        except OSError:
            continue
        for name in names:
            info = os.path.join(info_dir, name + ".trashinfo")
            orig, deleted = "", ""
            try:
                with open(info, encoding="utf-8", errors="replace") as fh:
                    for line in fh:
                        if line.startswith("Path="):
                            orig = unquote(line[5:].strip())
                        elif line.startswith("DeletionDate="):
                            deleted = line.split("=", 1)[1].strip().replace("T", " ")[:16]
            except OSError:
                pass
            paths = [os.path.join(files_dir, name)] + ([info] if os.path.exists(info) else [])
            where = os.path.dirname(orig) if orig.startswith("/") else (
                os.path.join(os.path.dirname(tdir), os.path.dirname(orig)) if orig else "unknown location")
            found.append(Item(
                id=f"trash-{tdir}-{name}", title=os.path.basename(orig) or name,
                subtitle=f"From {where}" + (f" · deleted {deleted}" if deleted else ""),
                size=disk_usage(os.path.join(files_dir, name)), risk=Risk.REVIEW,
                action=_user_delete(tdir, paths)))
        expunged = os.path.join(tdir, "expunged")
        if os.path.isdir(expunged):
            size = disk_usage(expunged)
            if size > 4096:
                found.append(Item(id=f"trash-{tdir}-expunged", title="Half-deleted leftovers",
                                  subtitle="Files the file manager started to delete but didn't finish",
                                  size=size, risk=Risk.SAFE, selected_by_default=True,
                                  action=_user_delete(tdir, [expunged])))
    found.sort(key=lambda i: -i.size)
    if len(found) > 80:
        rest = found[79:]
        merged_paths: dict[str, list[str]] = {}
        counts: dict[str, int] = {}
        for it in rest:
            merged_paths.setdefault(it.action["base"], []).extend(it.action["paths"])
            counts[it.action["base"]] = counts.get(it.action["base"], 0) + 1
        found = found[:79]
        for base, paths in merged_paths.items():
            found.append(Item(id=f"trash-rest-{base}", title=f"{counts[base]} smaller items",
                              subtitle="Everything else in the Trash", risk=Risk.REVIEW,
                              size=sum(i.size for i in rest if i.action["base"] == base),
                              action=_user_delete(base, paths)))
    cat.items = found
    if not found:
        cat.info.append("The Trash is empty.")
    return cat


# ==========================================================================

SCANNERS = [
    ("kernels", "Old kernels", "computer-symbolic", scan_kernels),
    ("package-cache", "Downloaded update files", "folder-download-symbolic", scan_package_cache),
    ("unused-packages", "Packages no longer needed", "package-x-generic-symbolic", scan_unused_packages),
    ("snaps", "Old Snap versions", "software-update-available-symbolic", scan_snaps),
    ("flatpak", "Unused Flatpak runtimes", "application-x-addon-symbolic", scan_flatpak),
    ("journal", "System journal (logs)", "text-x-generic-symbolic", scan_journal),
    ("crashes", "Crash reports & core dumps", "dialog-warning-symbolic", scan_crashes),
    ("rotated-logs", "Old log files", "document-open-recent-symbolic", scan_rotated_logs),
    ("thumbnails", "Picture previews", "image-x-generic-symbolic", scan_thumbnails),
    ("user-cache", "App caches in your home", "folder-symbolic", scan_user_cache),
    ("app-caches", "Flatpak & Snap app caches", "application-x-executable-symbolic", scan_app_caches),
    ("trash", "Trash", "user-trash-symbolic", scan_trash),
]


def run_scanner(func, ctx: ScanContext) -> Category:
    """Run one scanner; never let an exception escape."""
    try:
        return func(ctx)
    except Exception as exc:  # noqa: BLE001
        sid, title, icon = next(((s, t, i) for s, t, i, f in SCANNERS if f is func), ("?", "?", "folder-symbolic"))
        cat = Category(sid, title, icon, Risk.SAFE, Explanation("", "", "", ""))
        cat.error = f"Scanning failed: {exc}"
        return cat
