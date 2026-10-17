"""Turns the user's selection into a plan and carries it out."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Callable

from . import policy
from .cache_info import KNOWN_CACHES
from .model import Category, Item
from .util import disk_usage, running_flatpaks, running_process_names, running_snaps, total_free_bytes, which

DEV_HELPER = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "helper",
                                           "safe-disk-cleaner-helper"))


def installed_helper(module_file: str = __file__) -> str | None:
    """The helper that belongs to this installation.

    The code lives in <prefix>/lib/safe-disk-cleaner/safe_disk_cleaner/ and the helper in
    <prefix>/libexec/ (prefix /usr for the .deb, /usr/local for install.sh).
    """
    package_dir = os.path.dirname(os.path.realpath(module_file))
    lib_dir = os.path.dirname(package_dir)
    if os.path.basename(lib_dir) != "safe-disk-cleaner":
        return None
    prefix = os.path.dirname(os.path.dirname(lib_dir))
    helper = os.path.join(prefix, "libexec", "safe-disk-cleaner-helper")
    return helper if os.path.exists(helper) else None


@dataclass
class PlanEntry:
    category: str
    item_title: str
    size: int


@dataclass
class Plan:
    entries: list[PlanEntry] = field(default_factory=list)
    root_ops: list[dict] = field(default_factory=list)
    user_deletes: list[dict] = field(default_factory=list)     # {"base", "paths", "label"}
    flatpak_user_refs: list[str] = field(default_factory=list)

    @property
    def size(self) -> int:
        return sum(e.size for e in self.entries)

    @property
    def needs_admin(self) -> bool:
        return bool(self.root_ops)

    def is_empty(self) -> bool:
        return not (self.root_ops or self.user_deletes or self.flatpak_user_refs)

    def preview(self) -> list[str]:
        lines: list[str] = []
        for d in self.user_deletes:
            paths = d["paths"]
            if len(paths) <= 3:
                lines += [f"delete {p}" for p in paths]
            else:
                lines.append(f"delete {len(paths)} files/folders in {d['base']}")
        if self.flatpak_user_refs:
            lines.append("flatpak uninstall --user --noninteractive " + " ".join(self.flatpak_user_refs))
        for op in self.root_ops:
            lines += ["[admin] " + line for line in policy.preview_op(op)]
        return lines


def item_size(item: Item, choice: int | None) -> int:
    if item.choices:
        return item.choices[choice if choice is not None else item.default_choice].size
    return item.size


def build_plan(categories: list[Category], selected: dict[str, bool], choices: dict[str, int]) -> Plan:
    """selected / choices are keyed by f"{category.id}/{item.id}"."""
    plan = Plan()
    merged: dict[tuple, dict] = {}
    for cat in categories:
        for item in cat.items:
            key = f"{cat.id}/{item.id}"
            if not selected.get(key) or item.blocked:
                continue
            choice = choices.get(key, item.default_choice) if item.choices else None
            plan.entries.append(PlanEntry(cat.title, item.title, item_size(item, choice)))
            action = item.action
            kind = action.get("kind")
            if kind == "root":
                op = dict(action["op"])
                if item.choices and choice is not None:
                    # the chosen value replaces the op's size parameter
                    op["size"] = item.choices[choice].value
                merge = action.get("merge")
                if merge:
                    ident = tuple(sorted((k, v) for k, v in op.items() if k != merge))
                    if ident in merged:
                        merged[ident][merge].extend(op[merge])
                        continue
                    op[merge] = list(op[merge])
                    merged[ident] = op
                plan.root_ops.append(op)
            elif kind == "user-delete":
                for d in plan.user_deletes:
                    if d["base"] == action["base"]:
                        d["paths"].extend(action["paths"])
                        break
                else:
                    plan.user_deletes.append({"base": action["base"], "paths": list(action["paths"])})
            elif kind == "flatpak-user":
                plan.flatpak_user_refs.extend(action["refs"])
    return plan


# ---------------------------------------------------------------------------
# Executing
# ---------------------------------------------------------------------------

Log = Callable[[str], None]


class UnsafePath(Exception):
    pass


def safe_remove(path: str, base: str) -> int:
    """Delete a file or folder that must be inside `base`. Never follows symlinks."""
    base_real = os.path.realpath(base)
    parent_real = os.path.realpath(os.path.dirname(path))
    if parent_real != base_real and not parent_real.startswith(base_real + os.sep):
        raise UnsafePath(f"{path} is outside {base}")
    target = os.path.join(parent_real, os.path.basename(path))
    if os.path.basename(path) in ("", ".", ".."):
        raise UnsafePath(f"bad path {path}")
    if not os.path.lexists(target):
        return 0
    size = disk_usage(target)
    if os.path.isdir(target) and not os.path.islink(target):
        shutil.rmtree(target)  # symlink-attack resistant on Linux; doesn't follow links
    else:
        os.unlink(target)
    return size


def helper_command() -> list[str] | None:
    pkexec = which("pkexec")
    if not pkexec:
        return None
    helper = installed_helper()
    if helper:
        return [pkexec, helper]
    if os.path.exists(DEV_HELPER):
        return [pkexec, "/usr/bin/python3", "-I", DEV_HELPER]
    return None


def run_root_ops(ops: list[dict], log: Log) -> tuple[int, list[str]]:
    """Run privileged operations in ONE pkexec call. Returns (failures, errors)."""
    cmd = helper_command()
    if cmd is None:
        return len(ops), ["pkexec (package “pkexec” or “polkit”) is not installed, so administrator tasks "
                          "can't run."]
    log("Asking for your password…")
    try:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True)
    except OSError as exc:
        return len(ops), [f"Could not start the helper: {exc}"]
    assert proc.stdin and proc.stdout and proc.stderr
    try:
        proc.stdin.write(json.dumps({"operations": ops}))
        proc.stdin.close()
    except BrokenPipeError:
        pass
    errors: list[str] = []
    failures = 0
    finished = False
    for raw in proc.stdout:
        try:
            ev = json.loads(raw)
        except ValueError:
            continue
        kind = ev.get("event")
        if kind == "start":
            idx = ev.get("index", 0)
            lines = policy.preview_op(ops[idx]) if 0 <= idx < len(ops) else []
            log("→ " + (lines[0] if lines else str(ev.get("op"))))
        elif kind == "log":
            log("   " + ev.get("line", ""))
        elif kind == "done" and not ev.get("ok"):
            failures += 1
            errors.append(ev.get("error", "failed"))
            log("   ✗ " + ev.get("error", "failed"))
        elif kind == "fatal":
            errors.append(ev.get("error", "failed"))
            log("✗ " + ev.get("error", "failed"))
        elif kind == "finished":
            finished = True
    stderr = proc.stderr.read()
    rc = proc.wait()
    if rc in (126, 127) and not finished:
        return len(ops), ["Administrator tasks were skipped: the password prompt was cancelled or not allowed."]
    if not finished:
        errors.append("The helper stopped unexpectedly. " + stderr.strip()[-300:])
        failures = max(failures, 1)
    return failures, errors


def execute(plan: Plan, log: Log) -> tuple[int, list[str]]:
    """Carry out a plan. Returns (bytes freed on disk, error messages)."""
    errors: list[str] = []
    before = total_free_bytes()
    procs = running_process_names()
    snaps = running_snaps()
    flatpaks = running_flatpaks() if which("flatpak") else set()

    for d in plan.user_deletes:
        base = d["base"]
        log(f"→ Deleting {len(d['paths'])} item(s) in {base}")
        done = 0
        for p in d["paths"]:
            name = os.path.basename(p)
            # re-check that the app isn't running *now*
            if base.endswith("/.cache"):
                watch = KNOWN_CACHES.get(name, (None, None, None, (name.lower(),)))[3]
                busy = [w for w in watch if w.lower() in procs]
                if busy:
                    log(f"   skipped {p}: {busy[0]} is running")
                    continue
            if "/.var/app/" in p and os.path.basename(base) in flatpaks:
                log(f"   skipped {p}: the app is running")
                continue
            if "/snap/" in p and base.rstrip("/").split("/")[-2] in snaps:
                log(f"   skipped {p}: the app is running")
                continue
            try:
                safe_remove(p, base)
                done += 1
            except (OSError, UnsafePath) as exc:
                errors.append(f"{p}: {exc}")
                log(f"   ✗ {p}: {exc}")
        log(f"   deleted {done}")

    if plan.flatpak_user_refs:
        argv = [which("flatpak") or "flatpak", "uninstall", "--user", "--noninteractive", *plan.flatpak_user_refs]
        log("→ " + " ".join(argv[1:]))
        res = subprocess.run(argv, capture_output=True, text=True, stdin=subprocess.DEVNULL)
        for line in (res.stdout + res.stderr).splitlines():
            if line.strip():
                log("   " + line.strip())
        if res.returncode != 0:
            errors.append("Removing your personal Flatpak runtimes failed.")

    if plan.root_ops:
        _failures, errs = run_root_ops(plan.root_ops, log)
        errors += errs

    os.sync()
    freed = max(0, total_free_bytes() - before)
    return freed, errors


if __name__ == "__main__":  # pragma: no cover - manual testing aid
    print(helper_command(), file=sys.stderr)
