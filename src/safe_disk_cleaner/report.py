"""Plain-text report for the terminal (`safe-disk-cleaner --report`). Changes nothing."""

from __future__ import annotations

import textwrap
from concurrent.futures import ThreadPoolExecutor

from .model import Category
from .scanners import SCANNERS, ScanContext, run_scanner
from .util import filesystems, human_size, os_pretty_name
from .i18n import _



def _wrap(text: str, indent: str = "    ") -> str:
    return textwrap.fill(text, width=100, initial_indent=indent, subsequent_indent=indent)


def print_report() -> int:
    print(_("Safe Disk Cleaner — report for {arg0}  (nothing is changed)\n").format(arg0=(os_pretty_name())))
    for fs in filesystems():
        print(_("  {arg0:<12} {arg1:>9} used of {arg2:>9} ({arg3}%)").format(arg0=(fs.mountpoint), arg1=(human_size(fs.used)), arg2=(human_size(fs.total)), arg3=(int(fs.fraction * 100))))
    print()
    ctx = ScanContext.create()
    with ThreadPoolExecutor(max_workers=4) as pool:
        cats: list[Category] = list(pool.map(lambda s: run_scanner(s[3], ctx), SCANNERS))
    total = 0
    for cat in cats:
        print("=" * 100)
        if not cat.available:
            print(_("{arg0}: not needed here — {arg1}").format(arg0=(cat.title), arg1=(cat.unavailable_reason)))
            continue
        total += cat.total
        print(f"{cat.title}: {human_size(cat.total)}  [{cat.risk.label}]")
        if cat.error:
            print(_wrap("ERROR: " + cat.error))
        for w in cat.warnings:
            print(_wrap(_("WARNING: ") + w))
        e = cat.explanation
        for head, text in ((_("What is it?"), e.what), (_("Why does it grow?"), e.why), (_("Safe?"), e.safe),
                           (_("What the app does"), e.how), (_("Tip"), e.tip)):
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
            print(_("      … and {arg0} more").format(arg0=(len(cat.items) - 25)))
    print("=" * 100)
    print(_("Total that could be freed: {arg0}").format(arg0=(human_size(total))))
    return 0
