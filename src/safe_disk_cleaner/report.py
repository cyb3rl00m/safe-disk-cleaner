"""Plain-text report for the terminal (`safe-disk-cleaner --report`). Changes nothing."""

from __future__ import annotations

import textwrap
from concurrent.futures import ThreadPoolExecutor

from .model import Category
from .scanners import SCANNERS, ScanContext, run_scanner
from .util import filesystems, human_size, os_pretty_name


def _wrap(text: str, indent: str = "    ") -> str:
    return textwrap.fill(text, width=100, initial_indent=indent, subsequent_indent=indent)


def print_report() -> int:
    print(f"Safe Disk Cleaner — report for {os_pretty_name()}  (nothing is changed)\n")
    for fs in filesystems():
        print(f"  {fs.mountpoint:<12} {human_size(fs.used):>9} used of {human_size(fs.total):>9} "
              f"({int(fs.fraction * 100)}%)")
    print()
    ctx = ScanContext.create()
    with ThreadPoolExecutor(max_workers=4) as pool:
        cats: list[Category] = list(pool.map(lambda s: run_scanner(s[3], ctx), SCANNERS))
    total = 0
    for cat in cats:
        print("=" * 100)
        if not cat.available:
            print(f"{cat.title}: not needed here — {cat.unavailable_reason}")
            continue
        total += cat.total
        print(f"{cat.title}: {human_size(cat.total)}  [{cat.risk.label}]")
        if cat.error:
            print(_wrap("ERROR: " + cat.error))
        for w in cat.warnings:
            print(_wrap("WARNING: " + w))
        e = cat.explanation
        for head, text in (("What is it?", e.what), ("Why does it grow?", e.why), ("Safe?", e.safe),
                           ("What the app does", e.how), ("Tip", e.tip)):
            if text:
                print(f"  {head}")
                print(_wrap(text))
        for line in cat.info:
            print(_wrap("• " + line, "  "))
        for item in cat.items[:25]:
            mark = "[x]" if item.selected_by_default else "[ ]"
            note = f"  ⚠ {item.note}" if item.note else ""
            print(f"  {mark} {human_size(item.size if not item.choices else item.choices[item.default_choice].size):>9}"
                  f"  {item.title}  ({item.risk.label}){note}")
            if item.subtitle:
                print(_wrap(item.subtitle, "                  "))
        if len(cat.items) > 25:
            print(f"      … and {len(cat.items) - 25} more")
    print("=" * 100)
    print(f"Total that could be freed: {human_size(total)}")
    return 0
