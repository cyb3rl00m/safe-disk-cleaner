"""Tests for the rules that keep cleaning safe. Run: python3 -m unittest discover tests"""

import json
import os
import struct
import sys
import tempfile
import unittest
import zlib
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from safe_disk_cleaner import cleaner, helper, policy, scanners  # noqa: E402
from safe_disk_cleaner.model import Category, Choice, Explanation, Item, Risk  # noqa: E402

EXPL = Explanation("", "", "", "")


class KernelRules(unittest.TestCase):
    def test_package_versions(self):
        cases = {
            "linux-image-6.17.0-5-generic": "6.17.0-5",
            "linux-modules-6.17.0-5-generic": "6.17.0-5",
            "linux-modules-extra-6.17.0-5-generic": "6.17.0-5",
            "linux-headers-6.17.0-5": "6.17.0-5",
            "linux-headers-6.17.0-5-generic": "6.17.0-5",
            "linux-hwe-6.11-headers-6.11.0-19": "6.11.0-19",
            "linux-modules-nvidia-580-open-6.17.0-5-generic": "6.17.0-5",
            "linux-objects-nvidia-580-6.17.0-5-generic": "6.17.0-5",
            "linux-signatures-nvidia-6.17.0-5-generic": "6.17.0-5",
            "linux-image-unsigned-6.17.0-5-generic": "6.17.0-5",
            "linux-tools-6.17.0-5-generic": "6.17.0-5",
            # meta packages and unrelated packages must never match
            "linux-image-generic": None,
            "linux-generic-hwe-24.04": None,
            "linux-firmware": None,
            "linux-libc-dev": None,
            "linux-tools-common": None,
            "linux-headers-generic": None,
            "linux-sound-base": None,
        }
        for pkg, expected in cases.items():
            self.assertEqual(policy.kernel_version_of_package(pkg), expected, pkg)

    def test_running_kernel_and_newest_are_always_kept(self):
        installed = {"6.17.0-3", "6.17.0-4", "6.17.0-5", "6.14.0-30"}
        keep, remove = policy.plan_kernels(installed, "6.17.0-5")
        self.assertIn("6.17.0-5", keep)
        self.assertEqual(len(keep), 2)          # running(newest) + one fallback
        self.assertIn("6.17.0-4", keep)
        self.assertEqual(remove, {"6.17.0-3", "6.14.0-30"})

    def test_running_older_than_newest(self):
        keep, remove = policy.plan_kernels({"6.17.0-3", "6.17.0-4", "6.17.0-5"}, "6.17.0-3")
        self.assertEqual(keep, {"6.17.0-3", "6.17.0-5"})
        self.assertEqual(remove, {"6.17.0-4"})

    def test_never_leaves_fewer_than_two(self):
        self.assertEqual(policy.plan_kernels({"6.17.0-5"}, "6.17.0-5")[1], set())
        self.assertEqual(policy.plan_kernels({"6.17.0-4", "6.17.0-5"}, "6.17.0-5")[1], set())
        # custom running kernel that isn't a package: still keep the 2 newest
        keep, remove = policy.plan_kernels({"6.8.0-1", "6.17.0-4", "6.17.0-5"}, None)
        self.assertEqual(keep, {"6.17.0-4", "6.17.0-5"})

    def test_numeric_sort_not_text_sort(self):
        keep, _ = policy.plan_kernels({"6.9.0-10", "6.10.0-2", "6.10.0-10"}, None)
        self.assertEqual(keep, {"6.10.0-10", "6.10.0-2"})


class Parsers(unittest.TestCase):
    def test_apt_simulation(self):
        out = ("NOTE: This is only a simulation!\nReading package lists...\n"
               "Remv libfoo1 [1.2-3]\nPurg linux-image-6.17.0-3-generic [6.17.0-3.3]\n"
               "Remv libbar:i386 [2]\nInst something [1]\n")
        self.assertEqual(policy.parse_apt_simulation(out),
                         ["libfoo1", "linux-image-6.17.0-3-generic", "libbar:i386"])

    def test_snap_list(self):
        out = ("Name      Version   Rev    Tracking         Publisher   Notes\n"
               "core22    20250923  2133   latest/stable    canonical✓  base,disabled\n"
               "core22    20251002  2139   latest/stable    canonical✓  base\n"
               "firefox   143.0-1   6900   latest/stable/…  mozilla✓    disabled\n")
        rows = policy.parse_snap_list_all(out)
        self.assertEqual([(r["name"], r["rev"], "disabled" in r["notes"].split(",")) for r in rows],
                         [("core22", "2133", True), ("core22", "2139", False), ("firefox", "6900", True)])

    def test_flatpak_unused_without_arch(self):
        out = ("\n\t\tID\tBranch\tOp\n"
               " 1.\t   \torg.gnome.Platform\t46\tr\n"
               " 2.\t   \torg.gnome.Platform.Locale\t46\tr\n\n"
               "Proceed with these changes to the system installation? [Y/n]: n\n")
        rows = policy.parse_flatpak_unused(out)
        self.assertEqual(rows, [{"id": "org.gnome.Platform", "arch": None, "branch": "46"},
                                {"id": "org.gnome.Platform.Locale", "arch": None, "branch": "46"}])
        self.assertTrue(policy.flatpak_ref_in("runtime/org.gnome.Platform/x86_64/46", rows))
        self.assertFalse(policy.flatpak_ref_in("runtime/org.gnome.Platform/x86_64/48", rows))

    def test_flatpak_unused_with_arch(self):
        out = " 1.\t   \torg.freedesktop.Platform.GL.default\ti386\t24.08\tr\n"
        rows = policy.parse_flatpak_unused(out)
        self.assertEqual(rows[0], {"id": "org.freedesktop.Platform.GL.default", "arch": "i386",
                                   "branch": "24.08"})
        self.assertFalse(policy.flatpak_ref_in("runtime/org.freedesktop.Platform.GL.default/x86_64/24.08", rows))
        self.assertEqual(policy.parse_flatpak_unused("Nothing unused to uninstall\n"), [])

    def test_sizes(self):
        self.assertEqual(policy.parse_size("1.2 GB"), 1_200_000_000)
        self.assertEqual(policy.parse_size("512M"), 512_000_000)
        self.assertEqual(policy.parse_size("1.00 MiB"), 1_048_576)
        self.assertEqual(policy.parse_size("12.0 kB"), 12_000)
        self.assertIsNone(policy.parse_size("lots"))

    def test_rotated_logs(self):
        yes = ["syslog.1", "kern.log.2.gz", "messages-20261012", "Xorg.0.log.old", "auth.log.4.xz",
               "dpkg.log.1", "btmp.1"]
        no = ["syslog", "kern.log", "Xorg.0.log", "dpkg.log", "lastlog", "wtmp", "alternatives.log",
              "boot.log", "cloud-init.log"]
        for name in yes:
            self.assertTrue(policy.ROTATED_LOG_RE.match(name), name)
        for name in no:
            self.assertFalse(policy.ROTATED_LOG_RE.match(name), name)

    def test_validators(self):
        self.assertTrue(policy.SNAP_NAME_RE.match("gnome-46-2404"))
        self.assertFalse(policy.SNAP_NAME_RE.match("--purge"))
        self.assertFalse(policy.SNAP_NAME_RE.match("a;rm -rf /"))
        self.assertTrue(policy.APT_PKG_RE.match("libfoo1:i386"))
        self.assertFalse(policy.APT_PKG_RE.match("-y"))
        self.assertFalse(policy.FLATPAK_REF_RE.match("app/org.mozilla.firefox/x86_64/stable"))
        self.assertTrue(policy.FLATPAK_REF_RE.match("runtime/org.gnome.Platform/x86_64/48"))


class HelperFileDeletion(unittest.TestCase):
    """The root helper may only delete regular files inside its fixed folders."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = os.path.join(self.tmp.name, "log")
        self.outside = os.path.join(self.tmp.name, "precious")
        os.makedirs(os.path.join(self.base, "apt"))
        os.makedirs(self.outside)
        with open(os.path.join(self.outside, "shadow.1"), "w") as fh:
            fh.write("secret")
        self.patch = mock.patch.dict(policy.DELETE_GROUPS,
                                     {"test": (self.base, policy.ROTATED_LOG_RE, True)})
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def _file(self, rel, data="x" * 5000):
        path = os.path.join(self.base, rel)
        with open(path, "w") as fh:
            fh.write(data)
        return path

    def test_deletes_rotated_file(self):
        p = self._file("apt/history.log.1.gz")
        helper.safe_unlink("test", p)
        self.assertFalse(os.path.exists(p))

    def test_refuses_current_log(self):
        p = self._file("syslog")
        with self.assertRaises(helper.Refused):
            helper.safe_unlink("test", p)
        self.assertTrue(os.path.exists(p))

    def test_refuses_dotdot_and_outside(self):
        for bad in (os.path.join(self.base, "..", "precious", "shadow.1"),
                    os.path.join(self.outside, "shadow.1"),
                    self.base + "x/foo.1"):
            with self.assertRaises(helper.Refused):
                helper.safe_unlink("test", bad)
        self.assertTrue(os.path.exists(os.path.join(self.outside, "shadow.1")))

    def test_symlinked_directory_is_not_followed(self):
        os.symlink(self.outside, os.path.join(self.base, "evil"))
        with self.assertRaises(OSError):
            helper.safe_unlink("test", os.path.join(self.base, "evil", "shadow.1"))
        self.assertTrue(os.path.exists(os.path.join(self.outside, "shadow.1")))

    def test_symlinked_file_is_not_followed(self):
        link = os.path.join(self.base, "trap.1")
        os.symlink(os.path.join(self.outside, "shadow.1"), link)
        with self.assertRaises(helper.Refused):
            helper.safe_unlink("test", link)
        self.assertTrue(os.path.exists(os.path.join(self.outside, "shadow.1")))

    def test_unknown_group(self):
        with self.assertRaises(helper.Refused):
            helper.safe_unlink("everything", "/etc/passwd")


class HelperPackageRules(unittest.TestCase):
    """Package removal is re-validated against fresh data inside the helper."""

    def _fake_capture(self, apt_sim_purge="", autoremove="", dpkg=""):
        def capture(argv, timeout=300):
            res = mock.Mock(returncode=0, stderr="")
            if argv[:3] == ["apt-get", "-s", "autoremove"]:
                res.stdout = autoremove
            elif argv[:3] in (["apt-get", "-s", "purge"], ["apt-get", "-s", "remove"]):
                res.stdout = apt_sim_purge
            elif argv[0] == "dpkg-query":
                res.stdout = dpkg
            else:
                res.stdout = ""
            return res
        return capture

    def test_rejects_package_no_longer_orphaned(self):
        with mock.patch.object(helper, "capture", self._fake_capture(autoremove="Remv libold [1]\n")), \
             mock.patch.object(helper, "stream") as stream:
            with self.assertRaises(helper.Refused):
                helper.op_remove_packages({"manager": "apt", "purpose": "autoremove",
                                           "packages": ["gnome-shell"]})
            stream.assert_not_called()

    def test_rejects_when_apt_would_remove_more(self):
        fake = self._fake_capture(autoremove="Remv libold [1]\n",
                                  apt_sim_purge="Purg libold [1]\nPurg ubuntu-desktop [1]\n")
        with mock.patch.object(helper, "capture", fake), mock.patch.object(helper, "stream") as stream:
            with self.assertRaises(helper.Refused) as ctx:
                helper.op_remove_packages({"manager": "apt", "purpose": "autoremove", "packages": ["libold"]})
            self.assertIn("ubuntu-desktop", str(ctx.exception))
            stream.assert_not_called()

    def test_removes_exact_selection(self):
        fake = self._fake_capture(autoremove="Remv libold [1]\n", apt_sim_purge="Purg libold [1]\n")
        with mock.patch.object(helper, "capture", fake), \
             mock.patch.object(helper, "stream", return_value=0) as stream:
            helper.op_remove_packages({"manager": "apt", "purpose": "autoremove", "packages": ["libold"]})
            # orphans are only *removed*, so config files you edited in /etc are kept
            stream.assert_called_once_with(["apt-get", "remove", "-y", "libold"])

    def test_never_removes_running_or_newest_kernel(self):
        dpkg = ("ii \tlinux-image-6.17.0-3-generic\n"
                "ii \tlinux-image-6.17.0-4-generic\n"
                "ii \tlinux-image-6.17.0-5-generic\n")
        uname = mock.Mock(release="6.17.0-4-generic")
        for pkg in ("linux-image-6.17.0-4-generic", "linux-image-6.17.0-5-generic"):
            with mock.patch.object(helper, "capture", self._fake_capture(dpkg=dpkg)), \
                 mock.patch.object(helper.os, "uname", return_value=uname), \
                 mock.patch.object(helper, "stream") as stream:
                with self.assertRaises(helper.Refused):
                    helper.op_remove_packages({"manager": "apt", "purpose": "kernels", "packages": [pkg]})
                stream.assert_not_called()

    def test_bad_names_are_rejected(self):
        for pkgs in (["-y"], ["a b"], [], ["$(reboot)"]):
            with self.assertRaises(helper.Refused):
                helper.op_remove_packages({"manager": "apt", "purpose": "autoremove", "packages": pkgs})

    def test_unknown_operation_reports_error(self):
        out = []
        with mock.patch.object(helper, "emit", lambda **ev: out.append(ev)), \
             mock.patch.object(helper.os, "geteuid", return_value=0), \
             mock.patch.object(helper.sys, "stdin") as stdin, \
             mock.patch.dict(os.environ, {}, clear=False):
            stdin.read.return_value = json.dumps({"operations": [{"op": "rm-rf-slash"}]})
            saved = dict(os.environ)
            try:
                rc = helper.main()
            finally:
                os.environ.clear()
                os.environ.update(saved)
        self.assertEqual(rc, 1)
        self.assertTrue(any(ev.get("event") == "done" and not ev.get("ok") for ev in out))

    def test_journal_values_whitelisted(self):
        with mock.patch.object(helper, "stream") as stream:
            for bad in ("1K", "0", "999999G", "200M; rm -rf /"):
                with self.assertRaises(helper.Refused):
                    helper.op_journal_vacuum({"size": bad})
            stream.assert_not_called()


class UserSideDeletion(unittest.TestCase):
    def test_safe_remove_stays_inside_base(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = os.path.join(tmp, ".cache")
            os.makedirs(os.path.join(base, "pip", "http"))
            keep = os.path.join(tmp, "Documents")
            os.makedirs(keep)
            open(os.path.join(keep, "thesis.odt"), "w").close()
            # a symlink inside the cache pointing at Documents must only remove the link
            os.symlink(keep, os.path.join(base, "sneaky"))
            cleaner.safe_remove(os.path.join(base, "sneaky"), base)
            self.assertTrue(os.path.exists(os.path.join(keep, "thesis.odt")))
            with self.assertRaises(cleaner.UnsafePath):
                cleaner.safe_remove(os.path.join(base, "..", "Documents"), base)
            self.assertTrue(os.path.exists(os.path.join(keep, "thesis.odt")))
            cleaner.safe_remove(os.path.join(base, "pip"), base)
            self.assertFalse(os.path.exists(os.path.join(base, "pip")))


class Planning(unittest.TestCase):
    def test_merges_and_skips_blocked(self):
        cat = Category("kernels", "Old kernels", "x", Risk.SAFE, EXPL, items=[
            Item("a", "A", 10, action={"kind": "root", "merge": "packages", "op": {
                "op": "remove-packages", "manager": "apt", "purpose": "kernels", "packages": ["p1", "p2"]}}),
            Item("b", "B", 20, action={"kind": "root", "merge": "packages", "op": {
                "op": "remove-packages", "manager": "apt", "purpose": "kernels", "packages": ["p3"]}}),
            Item("c", "C", 99, blocked=True, action={"kind": "user-delete", "base": "/x", "paths": ["/x/y"]}),
        ])
        plan = cleaner.build_plan([cat], {"kernels/a": True, "kernels/b": True, "kernels/c": True}, {})
        self.assertEqual(len(plan.root_ops), 1)
        self.assertEqual(plan.root_ops[0]["packages"], ["p1", "p2", "p3"])
        self.assertEqual(plan.size, 30)
        self.assertEqual(plan.user_deletes, [])
        # building a plan must not modify the scanned items
        self.assertEqual(cat.items[0].action["op"]["packages"], ["p1", "p2"])

    def test_choice_sets_size(self):
        item = Item("j", "Journal", 0, choices=[Choice("100", "100M", 900), Choice("500", "500M", 400)],
                    default_choice=1, action={"kind": "root", "op": {"op": "journal-vacuum", "size": "500M"}})
        cat = Category("journal", "Journal", "x", Risk.SAFE, EXPL, items=[item])
        plan = cleaner.build_plan([cat], {"journal/j": True}, {"journal/j": 0})
        self.assertEqual(plan.root_ops[0]["size"], "100M")
        self.assertEqual(plan.size, 900)


def _png_with_uri(path, uri):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    raw = zlib.compress(b"\x00\x00\x00\x00")
    with open(path, "wb") as fh:
        fh.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"tEXt", b"Thumb::URI\0" + uri.encode())
                 + chunk(b"IDAT", raw) + chunk(b"IEND", b""))


class Thumbnails(unittest.TestCase):
    def test_orphans_detected_and_offline_drives_kept(self):
        with tempfile.TemporaryDirectory() as home:
            thumbs = os.path.join(home, ".cache", "thumbnails", "normal")
            os.makedirs(thumbs)
            existing = os.path.join(home, "photo one.jpg")
            open(existing, "w").close()
            _png_with_uri(os.path.join(thumbs, "a.png"), "file://" + existing.replace(" ", "%20"))
            _png_with_uri(os.path.join(thumbs, "b.png"), "file://" + os.path.join(home, "deleted.jpg"))
            _png_with_uri(os.path.join(thumbs, "c.png"), "file:///media/nobody/USBSTICK/x.jpg")
            self.assertEqual(scanners.png_thumb_uri(os.path.join(thumbs, "b.png")),
                             "file://" + os.path.join(home, "deleted.jpg"))
            ctx = scanners.ScanContext(home=home, uid=os.getuid())
            cat = scanners.scan_thumbnails(ctx)
            orphan = next(i for i in cat.items if i.id == "thumbs-orphan")
            self.assertEqual([os.path.basename(p) for p in orphan.action["paths"]], ["b.png"])
            self.assertTrue(orphan.selected_by_default)
            other = next(i for i in cat.items if i.id == "thumbs-other")
            self.assertEqual(sorted(os.path.basename(p) for p in other.action["paths"]), ["a.png", "c.png"])
            self.assertFalse(other.selected_by_default)


class Journal(unittest.TestCase):
    def test_vacuum_estimate_only_counts_archived(self):
        mb = 1_000_000
        files = [("old@1.journal", 300 * mb, 1, True), ("old@2.journal", 300 * mb, 2, True),
                 ("system.journal", 400 * mb, 3, False)]
        self.assertEqual(scanners.journal_vacuum_estimate(files, 500 * mb), 600 * mb)
        self.assertEqual(scanners.journal_vacuum_estimate(files, 800 * mb), 300 * mb)
        self.assertEqual(scanners.journal_vacuum_estimate(files, 2000 * mb), 0)


class UserCache(unittest.TestCase):
    def test_running_app_is_locked(self):
        with tempfile.TemporaryDirectory() as home:
            for name in ("mozilla", "pip", "somethingnew"):
                os.makedirs(os.path.join(home, ".cache", name))
                with open(os.path.join(home, ".cache", name, "blob"), "wb") as fh:
                    fh.write(os.urandom(1_200_000))
            ctx = scanners.ScanContext(home=home, uid=os.getuid(), procs={"firefox"})
            cat = scanners.scan_user_cache(ctx)
            items = {i.id: i for i in cat.items}
            self.assertTrue(items["cache-mozilla"].blocked)
            self.assertFalse(items["cache-mozilla"].selected_by_default)
            self.assertTrue(items["cache-pip"].selected_by_default)          # known + safe
            self.assertFalse(items["cache-somethingnew"].selected_by_default)  # unknown -> you decide


if __name__ == "__main__":
    unittest.main()
