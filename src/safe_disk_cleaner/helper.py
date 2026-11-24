"""Privileged helper, started through pkexec (one password prompt per clean).

Reads a JSON list of operations on stdin and writes one JSON event per line on
stdout. It does NOT trust the app: every operation is validated again here,
against a fixed whitelist, with fresh data from the system:

* packages are only removed if they are still orphaned / still old kernels,
  and only if an APT dry run removes nothing beyond the selection;
* the running kernel and the newest kernel can never be removed;
* snap revisions are only removed if snapd still reports them as disabled;
* flatpak runtimes are only removed if Flatpak still reports them unused;
* files are only deleted inside three fixed folders, by walking the path
  with O_NOFOLLOW so symlinks can't redirect a delete elsewhere.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import sys
import time

from . import policy
from .i18n import _, install


SAFE_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
MAX_INPUT = 8 * 1024 * 1024
JOURNAL_CONF_DIR = "/etc/systemd/journald.conf.d"
JOURNAL_CONF = os.path.join(JOURNAL_CONF_DIR, "60-safe-disk-cleaner.conf")


INVOKING_UID: int | None = None   # the user who ran pkexec (from PKEXEC_UID)


# Not attached to a terminal, `flatpak uninstall --unused` lists what is unused, answers its own
# "Proceed?" question with "n" and exits with code 1. That is the NORMAL result whenever something
# is unused; only "Nothing unused to uninstall" exits with 0.
FLATPAK_DECLINED_RE = re.compile(r"Proceed with these changes to the [^\n]*\[Y/n\]: n\s*$")


def why_failed(res: subprocess.CompletedProcess, limit: int = 5) -> str:
    """The error lines a command printed, for the message shown to the user."""
    lines = []
    for line in (res.stderr or "").splitlines() + (res.stdout or "").splitlines():
        line = line.strip()
        if line.lower().startswith(("error", ":: ", "warning")) and line not in lines:
            lines.append(line[:300])
    if not lines:
        tail = [x.strip() for x in (res.stderr or "").splitlines() if x.strip()]
        lines = [x[:300] for x in tail[-2:]]
    return " — ".join(lines[:limit]) or _("exit code {arg0}, no error message").format(arg0=(res.returncode))


def flatpak_unused_as_invoking_user() -> str:
    """Output of `flatpak uninstall --unused --system`, run as the real user."""
    import pwd
    if INVOKING_UID is not None and INVOKING_UID != 0:
        try:
            pw = pwd.getpwuid(INVOKING_UID)
        except KeyError:
            raise Refused(_("could not look up the user who started the app"))
        argv = [exe("runuser"), "-u", pw.pw_name, "--", "/usr/bin/env", "-i", f"HOME={pw.pw_dir}",
                f"PATH={SAFE_PATH}", "LC_ALL=C", exe("flatpak"), "uninstall", "--unused", "--system"]
    else:
        argv = ["/usr/bin/env", "LC_ALL=C", exe("flatpak"), "uninstall", "--unused", "--system"]
    res = subprocess.run(argv, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=300)
    if res.returncode == 0:
        return res.stdout
    if res.returncode == 1 and FLATPAK_DECLINED_RE.search(res.stdout or ""):
        return res.stdout
    raise Refused(_("Flatpak could not list unused runtimes: ") + why_failed(res))


class Refused(Exception):
    """An operation failed validation; nothing was changed."""


def emit(**event) -> None:
    sys.stdout.write(json.dumps(event) + "\n")
    sys.stdout.flush()


def exe(name: str) -> str:
    path = shutil.which(name, path=SAFE_PATH)
    if not path:
        raise Refused(_("{arg0} is not installed").format(arg0=(name)))
    return path


def capture(argv: list[str], timeout: int = 300, c_locale: bool = False) -> subprocess.CompletedProcess:
    env = {**os.environ, "LC_ALL": "C"} if c_locale else None
    return subprocess.run([exe(argv[0]), *argv[1:]], capture_output=True, text=True,
                          stdin=subprocess.DEVNULL, timeout=timeout, env=env)


def stream(argv: list[str], timeout: int = 3600) -> int:
    """Run a command, forwarding its output as log events."""
    emit(event="log", line="$ " + " ".join(argv))
    proc = subprocess.Popen([exe(argv[0]), *argv[1:]], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL, text=True, errors="replace")
    assert proc.stdout is not None
    for line in proc.stdout:
        line = line.rstrip()
        if line:
            emit(event="log", line=line)
    try:
        return proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        return -1


def check_rc(rc: int, what: str) -> None:
    if rc != 0:
        raise RuntimeError(_("{arg0} failed (exit code {arg1})").format(arg0=(what), arg1=(rc)))


# ---------------------------------------------------------------------------
# Package manager operations
# ---------------------------------------------------------------------------

def op_pkg_cache_clean(op: dict) -> None:
    mgr = op.get("manager")
    if mgr not in policy.PACKAGE_MANAGERS:
        raise Refused(_("unknown package manager"))
    if mgr == "apt":
        check_rc(stream(["apt-get", "clean"]), "apt-get clean")
    elif mgr == "dnf":
        check_rc(stream(["dnf", "clean", "packages"]), "dnf clean")
    elif mgr == "pacman":
        check_rc(stream(["paccache", "-rk2"]), "paccache")
        check_rc(stream(["paccache", "-ruk0"]), "paccache")
    elif mgr == "zypper":
        check_rc(stream(["zypper", "--non-interactive", "clean"]), "zypper clean")


def apt_installed() -> dict[str, str]:
    res = capture(["dpkg-query", "-W", "-f=${db:Status-Abbrev}\t${Package}\n"])
    out = {}
    for line in res.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) == 2:
            out[parts[1]] = parts[0]
    return out


def apt_removable_kernel_packages() -> set[str]:
    by_version: dict[str, list[str]] = {}
    held: set[str] = set()
    for pkg, status in apt_installed().items():
        if len(status) >= 2 and status[1] == "i":
            v = policy.kernel_version_of_package(pkg)
            if v:
                by_version.setdefault(v, []).append(pkg)
                if status[0] == "h":
                    held.add(v)
    running = policy.running_kernel_version(os.uname().release)
    _keep, removable = policy.plan_kernels(set(by_version), running, held)
    return {p for v in removable for p in by_version[v]}


def apt_autoremovable() -> set[str]:
    res = capture(["apt-get", "-s", "autoremove"])
    if res.returncode != 0:
        raise Refused(_("APT could not compute the list of unused packages"))
    return set(policy.parse_apt_simulation(res.stdout))


def _strip_arch(names) -> set[str]:
    return {n.split(":", 1)[0] for n in names}


def op_remove_packages(op: dict) -> None:
    mgr, purpose = op.get("manager"), op.get("purpose")
    pkgs = op.get("packages")
    if not isinstance(pkgs, list) or not pkgs or len(pkgs) > 2000:
        raise Refused(_("no packages given"))
    if purpose not in ("autoremove", "kernels"):
        raise Refused(_("unknown purpose"))
    if purpose == "kernels" and mgr != "apt":
        raise Refused(_("kernel cleaning is only supported with APT"))
    regex = policy.APT_PKG_RE if mgr == "apt" else policy.GENERIC_PKG_RE
    for p in pkgs:
        if not isinstance(p, str) or not regex.match(p):
            raise Refused(_("invalid package name: {arg0!r}").format(arg0=(p)))
    wanted = set(pkgs)

    if mgr == "apt":
        allowed = apt_removable_kernel_packages() if purpose == "kernels" else apt_autoremovable()
        if purpose == "kernels":
            running = policy.running_kernel_version(os.uname().release)
            for p in wanted:
                if running and policy.kernel_version_of_package(p) == running:
                    raise Refused(_("{arg0} belongs to the running kernel").format(arg0=(p)))
        missing = _strip_arch(wanted) - _strip_arch(allowed)
        if missing:
            raise Refused(_("no longer safe to remove (system changed since the scan): ") + ", ".join(sorted(missing)))
        # Kernels are purged (they have no settings of yours). Orphaned packages
        # are only *removed*, so any config files you edited in /etc are kept,
        # exactly like `apt autoremove` does.
        verb = "purge" if purpose == "kernels" else "remove"
        sim = capture(["apt-get", "-s", verb, *pkgs])
        if sim.returncode != 0:
            raise Refused(_("APT dry run failed: ") + (sim.stderr.strip().splitlines() or ["?"])[-1])
        extra = _strip_arch(policy.parse_apt_simulation(sim.stdout)) - _strip_arch(wanted)
        if extra:
            raise Refused(_("APT would also remove packages you didn't select: ") + ", ".join(sorted(extra)))
        os.environ["DEBIAN_FRONTEND"] = "noninteractive"
        check_rc(stream(["apt-get", verb, "-y", *pkgs]), f"apt-get {verb}")
    elif mgr == "dnf":
        res = capture(["dnf", "repoquery", "--unneeded", "--installed", "--queryformat", "%{name}\n"])
        allowed = {line.strip() for line in res.stdout.splitlines() if line.strip()}
        missing = wanted - allowed
        if missing:
            raise Refused(_("no longer unneeded: ") + ", ".join(sorted(missing)))
        check_rc(stream(["dnf", "remove", "-y", *pkgs]), "dnf remove")
    elif mgr == "pacman":
        res = capture(["pacman", "-Qdtq"], c_locale=True)
        allowed = {line.strip() for line in res.stdout.splitlines() if line.strip()}
        missing = wanted - allowed
        if missing:
            raise Refused(_("no longer orphaned: ") + ", ".join(sorted(missing)))
        # Dry run with exactly the command that will run (pacman refuses --print together with -n).
        sim = capture(["pacman", "-R", "--print", "--print-format", "%n", *pkgs], c_locale=True)
        if sim.returncode != 0:
            raise Refused(_("pacman's dry run failed: ") + why_failed(sim))
        extra = {line.strip() for line in sim.stdout.splitlines() if line.strip()} - wanted
        if extra:
            raise Refused(_("pacman would also remove packages you didn't select: ") + ", ".join(sorted(extra)))
        # Plain -R (not -Rn): config files you edited are kept as .pacsave backups, just like
        # orphaned packages on APT keep their configs.
        check_rc(stream(["pacman", "-R", "--noconfirm", *pkgs]), "pacman -R")
    else:
        raise Refused(_("unknown package manager"))


# ---------------------------------------------------------------------------
# Snap
# ---------------------------------------------------------------------------

def op_snap_remove_revision(op: dict) -> None:
    name, rev = str(op.get("name", "")), str(op.get("revision", ""))
    if not policy.SNAP_NAME_RE.match(name) or not policy.SNAP_REV_RE.match(rev):
        raise Refused(_("invalid snap name or revision"))
    res = capture(["snap", "list", "--all", name])
    rows = [r for r in policy.parse_snap_list_all(res.stdout) if r["name"] == name and r["rev"] == rev]
    if not rows or "disabled" not in rows[0]["notes"].split(","):
        raise Refused(_("{arg0} revision {arg1} is not a disabled (old) revision anymore").format(arg0=(name), arg1=(rev)))
    check_rc(stream(["snap", "remove", name, f"--revision={rev}"]), "snap remove")


def op_snap_set_retain(op: dict) -> None:
    value = str(op.get("value", ""))
    if value not in ("2", "3"):
        raise Refused(_("refresh.retain must be 2 or 3"))
    check_rc(stream(["snap", "set", "system", f"refresh.retain={value}"]), "snap set")


# ---------------------------------------------------------------------------
# Journal
# ---------------------------------------------------------------------------

def op_journal_vacuum(op: dict) -> None:
    size = str(op.get("size", ""))
    if size not in policy.JOURNAL_VACUUM_SIZES:
        raise Refused(_("unsupported journal size"))
    check_rc(stream(["journalctl", f"--vacuum-size={size}"]), "journalctl --vacuum-size")


def op_journal_set_limit(op: dict) -> None:
    size = str(op.get("size", ""))
    if size not in policy.JOURNAL_LIMITS:
        raise Refused(_("unsupported journal limit"))
    if size == "default":
        if os.path.lexists(JOURNAL_CONF):
            os.unlink(JOURNAL_CONF)
            emit(event="log", line=_("Removed {arg0}").format(arg0=(JOURNAL_CONF)))
    else:
        os.makedirs(JOURNAL_CONF_DIR, mode=0o755, exist_ok=True)
        tmp = JOURNAL_CONF + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o644)
        with os.fdopen(fd, "w") as fh:
            fh.write(_("# Written by Safe Disk Cleaner. Delete this file to go back to the default.\n[Journal]\nSystemMaxUse={arg0}\n").format(arg0=(size)))
        os.replace(tmp, JOURNAL_CONF)
        emit(event="log", line=_("Wrote SystemMaxUse={arg0} to {arg1}").format(arg0=(size), arg1=(JOURNAL_CONF)))
    check_rc(stream(["systemctl", "restart", "systemd-journald"]), _("restarting systemd-journald"))


# ---------------------------------------------------------------------------
# Flatpak (system installation)
# ---------------------------------------------------------------------------

def op_flatpak_uninstall(op: dict) -> None:
    refs = op.get("refs")
    if not isinstance(refs, list) or not refs:
        raise Refused(_("no runtimes given"))
    for r in refs:
        if not isinstance(r, str) or not policy.FLATPAK_REF_RE.match(r):
            raise Refused(_("invalid runtime reference: {arg0!r}").format(arg0=(r)))
    # Ask Flatpak which system runtimes are unused. Flatpak also looks at the
    # per-user apps of whoever runs it, so this check runs as YOU (the user who
    # started the app), not as root — otherwise a runtime used by one of your
    # personal Flatpak apps could look unused. Not attached to a terminal,
    # Flatpak answers its own "proceed?" prompt with "n", so this changes nothing.
    unused = policy.parse_flatpak_unused(flatpak_unused_as_invoking_user())
    still = [r for r in refs if policy.flatpak_ref_in(r, unused)]
    skipped = [r for r in refs if r not in still]
    for r in skipped:
        emit(event="log", line=_("Skipping {arg0}: Flatpak no longer reports it as unused").format(arg0=(r)))
    if still:
        check_rc(stream(["flatpak", "uninstall", "--system", "--noninteractive", *still]), "flatpak uninstall")


# ---------------------------------------------------------------------------
# Files in /var/crash, /var/lib/systemd/coredump, /var/log
# ---------------------------------------------------------------------------

def safe_unlink(group: str, path: str) -> int:
    """Delete one regular file inside the group's base folder. Returns bytes freed."""
    if group not in policy.DELETE_GROUPS:
        raise Refused(_("unknown file group"))
    base, name_re, allow_subdirs = policy.DELETE_GROUPS[group]
    norm = os.path.normpath(path)
    if norm != path or not norm.startswith(base + "/"):
        raise Refused(_("{arg0} is outside {arg1}").format(arg0=(path), arg1=(base)))
    parts = norm[len(base) + 1:].split("/")
    if any(p in ("", ".", "..") for p in parts):
        raise Refused(_("bad path {arg0}").format(arg0=(path)))
    if not allow_subdirs and len(parts) != 1:
        raise Refused(_("{arg0} is in a sub-folder").format(arg0=(path)))
    if group == "rotated-logs" and parts[0] == "journal":
        raise Refused(_("the journal is cleaned with journalctl, not by deleting files"))
    if not name_re.match(parts[-1]):
        raise Refused(_("{arg0} does not look like a file this app may delete").format(arg0=(parts[-1])))

    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    fd = os.open(base, flags)
    try:
        for component in parts[:-1]:
            nfd = os.open(component, flags, dir_fd=fd)
            os.close(fd)
            fd = nfd
        st = os.stat(parts[-1], dir_fd=fd, follow_symlinks=False)
        if not stat.S_ISREG(st.st_mode):
            raise Refused(_("{arg0} is not a regular file").format(arg0=(path)))
        if group == "rotated-logs" and time.time() - st.st_mtime < policy.LIVE_LOG_SECONDS:
            raise Refused(_("{arg0} was changed in the last hour — it may still be in use").format(arg0=(path)))
        os.unlink(parts[-1], dir_fd=fd)
        return st.st_blocks * 512
    finally:
        os.close(fd)


def op_delete_files(op: dict) -> None:
    group, paths = op.get("group"), op.get("paths")
    if not isinstance(paths, list) or not paths:
        raise Refused(_("no files given"))
    freed, failed = 0, 0
    for p in paths:
        if not isinstance(p, str):
            raise Refused(_("bad path"))
        try:
            freed += safe_unlink(str(group), p)
        except FileNotFoundError:
            continue
        except (Refused, OSError, ValueError) as exc:
            failed += 1
            emit(event="log", line=_("Skipped {arg0}: {arg1}").format(arg0=(p), arg1=(exc)))
    emit(event="log", line=_("Deleted {arg0} files").format(arg0=(len(paths) - failed)))
    if failed == len(paths):
        raise RuntimeError(_("no files could be deleted"))


OPERATIONS = {
    "pkg-cache-clean": op_pkg_cache_clean,
    "remove-packages": op_remove_packages,
    "snap-remove-revision": op_snap_remove_revision,
    "snap-set-retain": op_snap_set_retain,
    "journal-vacuum": op_journal_vacuum,
    "journal-set-limit": op_journal_set_limit,
    "flatpak-uninstall": op_flatpak_uninstall,
    "delete-files": op_delete_files,
}


def _request_language(request: object) -> str | None:
    """The language for helper messages, if the request names a sane one.

    The helper runs as root and must not build a catalogue path from arbitrary
    text, so only a plain language tag is accepted.
    """
    lang = request.get("lang") if isinstance(request, dict) else None
    if isinstance(lang, str) and re.fullmatch(r"[a-z]{2,3}(?:_[A-Z]{2})?", lang):
        return lang
    return None


def main() -> int:
    if os.geteuid() != 0:
        emit(event="fatal", error=_("The helper must be started as root (through pkexec)."))
        return 2
    global INVOKING_UID
    uid = os.environ.get("PKEXEC_UID", "")
    INVOKING_UID = int(uid) if uid.isdigit() else None
    os.environ.clear()
    os.environ.update({"PATH": SAFE_PATH, "LC_ALL": "C", "LANG": "C", "HOME": "/root"})
    os.umask(0o022)
    raw = sys.stdin.read(MAX_INPUT + 1)
    if len(raw) > MAX_INPUT:
        emit(event="fatal", error=_("Request too large"))
        return 2
    try:
        request = json.loads(raw)
        ops = request["operations"]
        if not isinstance(ops, list):
            raise TypeError(_("operations must be a list"))
    except (ValueError, KeyError, TypeError):
        emit(event="fatal", error=_("Malformed request"))
        return 2

    # The app passes its language in the request: pkexec clears the environment,
    # and the helper needs C locale for the commands it runs.
    lang = _request_language(request)
    install(languages=[lang] if lang else None)

    failures = 0
    for index, op in enumerate(ops):
        name = op.get("op") if isinstance(op, dict) else None
        emit(event="start", index=index, op=name)
        handler = OPERATIONS.get(str(name))
        try:
            if handler is None:
                raise Refused(_("unknown operation {arg0!r}").format(arg0=(name)))
            if not isinstance(op, dict):
                raise Refused(_("operation must be an object"))
            handler(op)
            emit(event="done", index=index, ok=True)
        except Refused as exc:
            failures += 1
            emit(event="done", index=index, ok=False, error=_("Not done, for safety: {arg0}").format(arg0=(exc)))
        except Exception as exc:  # noqa: BLE001
            failures += 1
            emit(event="done", index=index, ok=False, error=str(exc))
    emit(event="finished", failures=failures)
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
