"""Attack tests: try to make the root helper or the cleaner damage the system.

Every test here tries something hostile or broken and checks that nothing
outside the allowed places is deleted and no unexpected command runs.
"""

import io
import json
import os
import random
import string
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from safe_disk_cleaner import cleaner, helper, policy  # noqa: E402

# Commands the helper is ever allowed to execute (first two words).
ALLOWED_COMMANDS = {
    ("apt-get", "clean"), ("apt-get", "purge"), ("apt-get", "remove"),
    ("dnf", "clean"), ("dnf", "remove"), ("paccache", "-rk2"), ("paccache", "-ruk0"),
    ("pacman", "-Rn"), ("zypper", "--non-interactive"),
    ("snap", "remove"), ("snap", "set"), ("journalctl", "--vacuum-size=100M"),
    ("journalctl", "--vacuum-size=200M"), ("journalctl", "--vacuum-size=500M"),
    ("journalctl", "--vacuum-size=1G"), ("systemctl", "restart"), ("flatpak", "uninstall"),
}

NASTY_STRINGS = [
    "", " ", "-y", "--force", "-rf", "/", "/etc", "/etc/passwd", "/boot/vmlinuz", "../../../etc/shadow",
    "a;reboot", "$(rm -rf /)", "`id`", "x\ny", "x\x00y", "linux-image-generic", "systemd", "grub-efi-amd64",
    "ubuntu-desktop", "*", "?", "~", "..", ".", "core22", "x" * 5000, "ü", "runtime/../../etc/x/y",
    "runtime/org.gnome.Platform/x86_64/46\n--force", "app/org.mozilla.firefox/x86_64/stable",
]


def random_value(rng):
    pick = rng.random()
    if pick < 0.4:
        return rng.choice(NASTY_STRINGS)
    if pick < 0.55:
        return [rng.choice(NASTY_STRINGS) for _ in range(rng.randint(0, 4))]
    if pick < 0.65:
        return rng.randint(-5, 10**9)
    if pick < 0.7:
        return None
    if pick < 0.75:
        return {"nested": rng.choice(NASTY_STRINGS)}
    return "".join(rng.choice(string.printable) for _ in range(rng.randint(0, 40)))


class FuzzHelper(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = self.tmp.name
        self.sentinels = []
        for rel in ("etc/passwd", "boot/vmlinuz-6.17.0-5-generic", "home/user/thesis.odt", "var/log/syslog",
                    "var/log/journal/system.journal", "var/crash/../keep.crash"):
            path = os.path.normpath(os.path.join(root, rel))
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as fh:
                fh.write("important")
            self.sentinels.append(path)
        os.makedirs(os.path.join(root, "var", "crash"), exist_ok=True)
        os.symlink(os.path.join(root, "etc"), os.path.join(root, "var", "log", "etc-link"))
        self.root = root
        groups = {
            "crash": (os.path.join(root, "var/crash"), policy.APPORT_CRASH_RE, False),
            "coredump": (os.path.join(root, "var/coredump"), policy.COREDUMP_RE, False),
            "rotated-logs": (os.path.join(root, "var/log"), policy.ROTATED_LOG_RE, True),
        }
        self.patches = [mock.patch.dict(policy.DELETE_GROUPS, groups, clear=True)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def _run_helper(self, ops):
        executed = []

        def fake_capture(argv, timeout=300):
            # an empty system: nothing is orphaned, no old kernels, no snaps
            return mock.Mock(returncode=0, stdout="", stderr="")

        def fake_stream(argv, timeout=3600):
            executed.append(argv)
            return 0

        events = []
        saved_env = dict(os.environ)
        try:
            with mock.patch.object(helper, "capture", fake_capture), \
                 mock.patch.object(helper, "stream", fake_stream), \
                 mock.patch.object(helper, "flatpak_unused_as_invoking_user", return_value=""), \
                 mock.patch.object(helper, "emit", lambda **ev: events.append(ev)), \
                 mock.patch.object(helper, "op_journal_set_limit", lambda op: None), \
                 mock.patch.object(helper.os, "geteuid", return_value=0), \
                 mock.patch.object(helper.sys, "stdin", io.StringIO(json.dumps({"operations": ops}))):
                rc = helper.main()
        finally:
            os.environ.clear()
            os.environ.update(saved_env)
        return rc, executed, events

    def test_thousands_of_random_requests(self):
        rng = random.Random(1234)
        ops_names = list(helper.OPERATIONS) + ["rm", "exec", "", None, 5]
        keys = ["op", "manager", "purpose", "packages", "name", "revision", "value", "size", "refs", "group",
                "paths"]
        total_ops = 0
        for _round in range(400):
            ops = []
            for _ in range(rng.randint(1, 6)):
                op = {k: random_value(rng) for k in rng.sample(keys, rng.randint(0, len(keys)))}
                op["op"] = rng.choice(ops_names)
                if op["op"] == "delete-files" and rng.random() < 0.7:
                    op["group"] = rng.choice(["crash", "coredump", "rotated-logs", "x"])
                    op["paths"] = [os.path.join(self.root, rng.choice([
                        "etc/passwd", "var/log/../../etc/passwd", "var/log/etc-link/passwd",
                        "var/log/syslog", "var/log/journal/system.journal", "boot/vmlinuz-6.17.0-5-generic",
                        "var/crash/../keep.crash", "home/user/thesis.odt"]))]
                ops.append(op)
            total_ops += len(ops)
            rc, executed, events = self._run_helper(ops)
            self.assertIn(rc, (0, 1))
            self.assertEqual(events[-1]["event"], "finished")     # never crashed half-way
            for argv in executed:
                self.assertIn(tuple(argv[:2]), ALLOWED_COMMANDS, argv)
                if argv[:2] in (["apt-get", "purge"], ["apt-get", "remove"], ["dnf", "remove"],
                                ["pacman", "-Rn"], ["snap", "remove"]):
                    self.fail(f"removed something on a system where nothing is removable: {argv}")
        for path in self.sentinels:
            self.assertTrue(os.path.exists(path), f"{path} was deleted!")
        self.assertGreater(total_ops, 1000)

    def test_malformed_requests(self):
        for raw in ["", "null", "[]", "{}", '{"operations": "rm -rf /"}', '{"operations": [1, "x", null]}',
                    "{" * 10000, '{"operations": [{"op": "delete-files", "paths": [null]}]}']:
            events = []
            saved = dict(os.environ)
            try:
                with mock.patch.object(helper, "emit", lambda **ev: events.append(ev)), \
                     mock.patch.object(helper.os, "geteuid", return_value=0), \
                     mock.patch.object(helper.sys, "stdin", io.StringIO(raw)), \
                     mock.patch.object(helper, "stream") as stream:
                    helper.main()
                    stream.assert_not_called()
            finally:
                os.environ.clear()
                os.environ.update(saved)
        for path in self.sentinels:
            self.assertTrue(os.path.exists(path))

    def test_refuses_without_root(self):
        events = []
        with mock.patch.object(helper, "emit", lambda **ev: events.append(ev)), \
             mock.patch.object(helper.os, "geteuid", return_value=1000):
            self.assertEqual(helper.main(), 2)
        self.assertEqual(events[0]["event"], "fatal")


class LiveLogs(unittest.TestCase):
    def test_recently_written_log_is_not_deleted(self):
        with tempfile.TemporaryDirectory() as tmp:
            # Samba writes live per-client logs named like log.192.168.1.5
            live = os.path.join(tmp, "log.192.168.1.5")
            old = os.path.join(tmp, "syslog.2.gz")
            for p in (live, old):
                with open(p, "w") as fh:
                    fh.write("x")
            past = time.time() - 7 * 86400
            os.utime(old, (past, past))
            with mock.patch.dict(policy.DELETE_GROUPS, {"rotated-logs": (tmp, policy.ROTATED_LOG_RE, True)}):
                with self.assertRaises(helper.Refused):
                    helper.safe_unlink("rotated-logs", live)
                helper.safe_unlink("rotated-logs", old)
            self.assertTrue(os.path.exists(live))
            self.assertFalse(os.path.exists(old))


class Kernels(unittest.TestCase):
    def test_held_kernel_is_kept(self):
        keep, remove = policy.plan_kernels({"6.8.0-50", "6.14.0-30", "6.17.0-4", "6.17.0-5"}, "6.17.0-5",
                                           held={"6.8.0-50"})
        self.assertIn("6.8.0-50", keep)
        self.assertIn("6.17.0-5", keep)
        self.assertNotIn("6.17.0-5", remove)
        self.assertNotIn("6.8.0-50", remove)

    def test_at_least_two_kernels_in_every_situation(self):
        rng = random.Random(7)
        for _ in range(3000):
            versions = {f"6.{rng.randint(0, 20)}.0-{rng.randint(1, 60)}" for _ in range(rng.randint(1, 8))}
            running = rng.choice(sorted(versions) + [None, "5.15.0-1"])
            keep, remove = policy.plan_kernels(versions, running)
            self.assertEqual(keep | remove, versions)
            self.assertGreaterEqual(len(keep), min(2, len(versions)))
            newest = max(versions, key=policy.kernel_sort_key)
            self.assertIn(newest, keep)
            if running in versions:
                self.assertIn(running, keep)

    def test_helper_keeps_held_kernel(self):
        dpkg = ("hi \tlinux-image-6.8.0-50-generic\n"
                "ii \tlinux-image-6.14.0-30-generic\n"
                "ii \tlinux-image-6.17.0-5-generic\n")
        with mock.patch.object(helper, "capture", return_value=mock.Mock(returncode=0, stdout=dpkg)), \
             mock.patch.object(helper.os, "uname", return_value=mock.Mock(release="6.17.0-5-generic")):
            removable = helper.apt_removable_kernel_packages()
        self.assertEqual(removable, {"linux-image-6.14.0-30-generic"})


class FlatpakCheckRunsAsUser(unittest.TestCase):
    def test_unused_check_runs_as_invoking_user(self):
        calls = []

        def fake_run(argv, **kw):
            calls.append(argv)
            return mock.Mock(returncode=0, stdout="", stderr="")

        pw = mock.Mock(pw_name="alex", pw_dir="/home/alex")
        with mock.patch.object(helper, "INVOKING_UID", 1000), \
             mock.patch("pwd.getpwuid", return_value=pw), \
             mock.patch.object(helper, "exe", side_effect=lambda n: f"/usr/bin/{n}"), \
             mock.patch.object(helper.subprocess, "run", fake_run):
            helper.flatpak_unused_as_invoking_user()
        argv = calls[0]
        self.assertEqual(argv[:4], ["/usr/bin/runuser", "-u", "alex", "--"])
        self.assertIn("HOME=/home/alex", argv)
        self.assertEqual(argv[-3:], ["uninstall", "--unused", "--system"])

    def test_runtime_in_use_is_skipped(self):
        # Flatpak (run as the user) says only GNOME 46 is unused; 48 is used by a personal app.
        out = " 1.\t   \torg.gnome.Platform\t46\tr\n"
        with mock.patch.object(helper, "flatpak_unused_as_invoking_user", return_value=out), \
             mock.patch.object(helper, "emit"), \
             mock.patch.object(helper, "stream", return_value=0) as stream:
            helper.op_flatpak_uninstall({"refs": ["runtime/org.gnome.Platform/x86_64/46",
                                                  "runtime/org.gnome.Platform/x86_64/48"]})
        stream.assert_called_once_with(["flatpak", "uninstall", "--system", "--noninteractive",
                                        "runtime/org.gnome.Platform/x86_64/46"])


class InstallLayout(unittest.TestCase):
    def test_helper_is_found_next_to_the_installation(self):
        for prefix in ("usr", "usr/local"):
            with tempfile.TemporaryDirectory() as root:
                pkg = os.path.join(root, prefix, "lib", "safe-disk-cleaner", "safe_disk_cleaner")
                os.makedirs(pkg)
                helper = os.path.join(root, prefix, "libexec", "safe-disk-cleaner-helper")
                os.makedirs(os.path.dirname(helper))
                open(helper, "w").close()
                self.assertEqual(cleaner.installed_helper(os.path.join(pkg, "cleaner.py")), helper)

    def test_source_folder_has_no_installed_helper(self):
        self.assertIsNone(cleaner.installed_helper(cleaner.__file__))


class HomeDeletion(unittest.TestCase):
    def test_cleaner_never_leaves_its_folder(self):
        with tempfile.TemporaryDirectory() as home:
            cache = os.path.join(home, ".cache")
            os.makedirs(cache)
            docs = os.path.join(home, "Documents")
            os.makedirs(docs)
            with open(os.path.join(docs, "cv.pdf"), "w") as fh:
                fh.write("x")
            os.symlink(home, os.path.join(cache, "home-link"))
            os.symlink(docs, os.path.join(cache, "docs-link"))
            attempts = [cache, home, docs, os.path.join(cache, ".."), os.path.join(cache, "home-link", "Documents"),
                        os.path.join(cache, "docs-link", "cv.pdf"), "/", "/etc", os.path.join(docs, "cv.pdf")]
            for target in attempts:
                try:
                    cleaner.safe_remove(target, cache)
                except (cleaner.UnsafePath, OSError):
                    pass
            self.assertTrue(os.path.exists(os.path.join(docs, "cv.pdf")))
            self.assertTrue(os.path.isdir(cache))
            self.assertTrue(os.path.exists("/etc/hostname"))


if __name__ == "__main__":
    unittest.main()
