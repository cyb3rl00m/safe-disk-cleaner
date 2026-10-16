#!/usr/bin/env python3
"""Build or update a signed APT repository from .deb files.

Usage:
    python3 packaging/apt_repo.py <repo-dir> <file.deb> [more.deb ...] --key <gpg-key-id>

Layout produced (served e.g. by GitHub Pages):
    <repo-dir>/pool/main/<package>_<version>_<arch>.deb   every version ever published
    <repo-dir>/dists/stable/main/binary-<arch>/Packages[.gz]
    <repo-dir>/dists/stable/Release, InRelease, Release.gpg
    <repo-dir>/safe-disk-cleaner.gpg                         public key users install

Needs only Python 3, dpkg-deb and gpg — no apt-ftparchive/reprepro.
"""

from __future__ import annotations

import argparse
import email.utils
import gzip
import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

SUITE = "stable"
COMPONENT = "main"
ARCHES = ["amd64", "arm64", "armhf", "i386"]    # our package is "all": listed for every arch
ORIGIN = "cyb3rl00m"
LABEL = "Safe Disk Cleaner"
KEYRING_NAME = "safe-disk-cleaner.gpg"

# Order of fields in a Packages stanza (others follow alphabetically).
FIELD_ORDER = ["Package", "Version", "Architecture", "Maintainer", "Installed-Size", "Depends",
               "Recommends", "Suggests", "Section", "Priority", "Homepage"]


def hashes(path: Path) -> dict[str, str]:
    h = {"MD5sum": hashlib.md5(), "SHA1": hashlib.sha1(), "SHA256": hashlib.sha256()}
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            for algo in h.values():
                algo.update(chunk)
    return {k: v.hexdigest() for k, v in h.items()}


def control_fields(deb: Path) -> dict[str, str]:
    """Read DEBIAN/control of a .deb (multi-line Description kept intact)."""
    out = subprocess.run(["dpkg-deb", "-f", str(deb)], check=True, capture_output=True, text=True).stdout
    fields: dict[str, str] = {}
    key = None
    for line in out.splitlines():
        if line.startswith((" ", "\t")) and key:
            fields[key] += "\n" + line
        elif ":" in line:
            key, value = line.split(":", 1)
            fields[key] = value.strip()
    return fields


def stanza(deb: Path, repo: Path) -> str:
    f = control_fields(deb)
    desc = f.pop("Description", "")
    lines = [f"{k}: {f.pop(k)}" for k in FIELD_ORDER if k in f]
    lines += [f"{k}: {v}" for k, v in sorted(f.items())]
    h = hashes(deb)
    lines += [f"Filename: {deb.relative_to(repo).as_posix()}", f"Size: {deb.stat().st_size}",
              f"MD5sum: {h['MD5sum']}", f"SHA1: {h['SHA1']}", f"SHA256: {h['SHA256']}"]
    if desc:
        lines.append(f"Description: {desc}")
    return "\n".join(lines) + "\n"


def write_packages(repo: Path) -> list[Path]:
    debs = sorted((repo / "pool").rglob("*.deb"))
    if not debs:
        raise SystemExit("no .deb files in the pool")
    for deb in debs:
        arch = control_fields(deb).get("Architecture")
        if arch != "all":
            raise SystemExit(f"{deb.name}: only Architecture: all packages are supported (got {arch})")
    text = "\n".join(stanza(d, repo) for d in debs)
    written = []
    for arch in ARCHES:
        d = repo / "dists" / SUITE / COMPONENT / f"binary-{arch}"
        d.mkdir(parents=True, exist_ok=True)
        (d / "Packages").write_text(text)
        with open(d / "Packages", "rb") as src, gzip.GzipFile(d / "Packages.gz", "wb", mtime=0) as dst:
            shutil.copyfileobj(src, dst)
        written += [d / "Packages", d / "Packages.gz"]
    return written


def write_release(repo: Path, files: list[Path]) -> Path:
    dist = repo / "dists" / SUITE
    rel = [
        f"Origin: {ORIGIN}", f"Label: {LABEL}", f"Suite: {SUITE}", f"Codename: {SUITE}",
        f"Date: {email.utils.formatdate(usegmt=True)}",
        f"Architectures: {' '.join(ARCHES)}", f"Components: {COMPONENT}",
        f"Description: {LABEL} - updates for the safe-disk-cleaner package",
    ]
    for title, key in (("MD5Sum", "MD5sum"), ("SHA1", "SHA1"), ("SHA256", "SHA256")):
        rel.append(f"{title}:")
        for f in files:
            rel.append(f" {hashes(f)[key]} {f.stat().st_size:>8} {f.relative_to(dist).as_posix()}")
    path = dist / "Release"
    path.write_text("\n".join(rel) + "\n")
    return path


def sign(repo: Path, release: Path, key: str) -> None:
    dist = release.parent
    base = ["gpg", "--batch", "--yes", "--pinentry-mode", "loopback", "--passphrase", "",
            "--local-user", key, "--digest-algo", "SHA512"]
    subprocess.run(base + ["--clearsign", "-o", str(dist / "InRelease"), str(release)], check=True)
    subprocess.run(base + ["--armor", "--detach-sign", "-o", str(dist / "Release.gpg"), str(release)], check=True)
    with open(repo / KEYRING_NAME, "wb") as fh:
        subprocess.run(["gpg", "--batch", "--export", key], check=True, stdout=fh)
    if (repo / KEYRING_NAME).stat().st_size == 0:
        raise SystemExit(f"gpg exported an empty public key for {key!r}")


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("repo")
    ap.add_argument("debs", nargs="+")
    ap.add_argument("--key", required=True, help="GPG key id / fingerprint used for signing")
    args = ap.parse_args(argv)

    repo = Path(args.repo).resolve()
    pool = repo / "pool" / COMPONENT
    pool.mkdir(parents=True, exist_ok=True)
    for deb in map(Path, args.debs):
        f = control_fields(deb)
        target = pool / f"{f['Package']}_{f['Version']}_{f['Architecture']}.deb"
        if target.exists():
            # A published file must never change (apt checks its hash). Keep the original.
            print(f"{target.name} is already published — keeping it (bump the version to publish changes)")
            continue
        shutil.copyfile(deb, target)
        print(f"added {target.relative_to(repo)}")

    files = write_packages(repo)
    release = write_release(repo, files)
    sign(repo, release, args.key)
    (repo / ".nojekyll").touch()
    print(f"repository updated: {repo}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
