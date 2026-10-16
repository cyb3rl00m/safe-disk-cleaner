"""GTK 4 / libadwaita user interface."""

from __future__ import annotations

import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Pango", "1.0")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

from . import APP_ID, APP_NAME, VERSION, WEBSITE  # noqa: E402
from .cleaner import Plan, build_plan, execute, item_size, run_root_ops  # noqa: E402
from .model import Category, Item, Risk, Setting  # noqa: E402
from .scanners import SCANNERS, ScanContext, run_scanner  # noqa: E402
from .util import FileSystem, filesystems, human_size, os_pretty_name  # noqa: E402

CSS = """
.risk-badge { border-radius: 999px; padding: 2px 10px; font-size: 0.85em; font-weight: bold; }
.risk-badge.risk-safe { background-color: alpha(@success_color, 0.15); color: @success_color; }
.risk-badge.risk-caution { background-color: alpha(@warning_color, 0.18); color: @warning_color; }
.risk-badge.risk-review { background-color: alpha(@error_color, 0.15); color: @error_color; }
.risk-dot { min-width: 8px; min-height: 8px; border-radius: 4px; margin: 0 2px; }
.risk-dot.risk-safe { background-color: @success_color; }
.risk-dot.risk-caution { background-color: @warning_color; }
.risk-dot.risk-review { background-color: @error_color; }
.risk-dot.none { background-color: transparent; }
.explain-card { padding: 14px 16px; }
.explain-heading { font-weight: bold; margin-bottom: 4px; }
.notice-card { padding: 12px 14px; }
.notice-card.warning { background-color: alpha(@warning_color, 0.12); }
.mono { font-family: monospace; font-size: 0.9em; }
.hero-size { font-size: 2.6em; font-weight: 800; }
levelbar.disk block.critical { background-color: @error_color; }
levelbar.disk block.low { background-color: @warning_color; }
.item-note { color: @warning_color; }
.hero-caption { font-size: 1.05em; }
.step-number { font-weight: 800; color: @accent_color; }
"""


def risk_badge(risk: Risk) -> Gtk.Label:
    label = Gtk.Label(label=risk.label, valign=Gtk.Align.CENTER, tooltip_text=risk.meaning)
    label.add_css_class("risk-badge")
    label.add_css_class(risk.css)
    return label


def wrap_label(text: str, *classes: str, selectable: bool = False) -> Gtk.Label:
    label = Gtk.Label(label=text, wrap=True, xalign=0, selectable=selectable)
    for c in classes:
        label.add_css_class(c)
    return label


def center_label(text: str, *classes: str) -> Gtk.Label:
    label = Gtk.Label(label=text, wrap=True, xalign=0.5, justify=Gtk.Justification.CENTER,
                      halign=Gtk.Align.CENTER)
    for c in classes:
        label.add_css_class(c)
    return label


def make_spinner() -> Gtk.Widget:
    if hasattr(Adw, "Spinner"):
        return Adw.Spinner()
    spinner = Gtk.Spinner()
    spinner.start()
    return spinner


def new_alert(parent: Gtk.Window, heading: str, body: str):
    if hasattr(Adw, "AlertDialog"):
        return Adw.AlertDialog(heading=heading, body=body)
    return Adw.MessageDialog(transient_for=parent, heading=heading, body=body)


def present_dialog(dialog, parent: Gtk.Window) -> None:
    if hasattr(Adw, "AlertDialog") and isinstance(dialog, Adw.Dialog):
        dialog.present(parent)
    else:
        dialog.present()


class SidebarRow(Gtk.ListBoxRow):
    def __init__(self, key: str, title: str, icon: str):
        super().__init__()
        self.key = key
        box = Gtk.Box(spacing=12, margin_top=8, margin_bottom=8, margin_start=6, margin_end=6)
        box.append(Gtk.Image(icon_name=icon))
        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
        self.title = Gtk.Label(label=title, xalign=0, ellipsize=Pango.EllipsizeMode.END)
        self.sub = Gtk.Label(label="", xalign=0, ellipsize=Pango.EllipsizeMode.END)
        self.sub.add_css_class("caption")
        self.sub.add_css_class("dim-label")
        texts.append(self.title)
        texts.append(self.sub)
        box.append(texts)
        self.dot = Gtk.Box(valign=Gtk.Align.CENTER)
        self.dot.add_css_class("risk-dot")
        self.dot.add_css_class("none")
        box.append(self.dot)
        self.set_child(box)

    def set_risk(self, risk: Risk | None) -> None:
        for css in ("none", "risk-safe", "risk-caution", "risk-review"):
            self.dot.remove_css_class(css)
        self.dot.add_css_class(risk.css if risk else "none")
        self.dot.set_tooltip_text(risk.label if risk else None)


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application):
        super().__init__(application=app, title=APP_NAME)
        self.set_default_size(1120, 780)
        self.set_size_request(360, 480)

        self.categories: dict[str, Category] = {}
        self.selected: dict[str, bool] = {}
        self.choices: dict[str, int] = {}
        self.disks: list[FileSystem] = []
        self.scanning = False
        self.current = "overview"
        self.rows: dict[str, SidebarRow] = {}
        self._ov_checks: dict[str, tuple[Gtk.CheckButton, Adw.ActionRow]] = {}
        self._hero_selected: Gtk.Label | None = None
        self._syncing = False

        # ---- sidebar -------------------------------------------------------
        sidebar_view = Adw.ToolbarView()
        side_header = Adw.HeaderBar()
        self.scan_button = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text="Scan again (Ctrl+R)")
        self.scan_button.connect("clicked", lambda *_: self.start_scan())
        side_header.pack_start(self.scan_button)
        menu = Gio.Menu()
        menu.append("About Safe Disk Cleaner", "app.about")
        side_header.pack_end(Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu,
                                            tooltip_text="Main menu"))
        sidebar_view.add_top_bar(side_header)
        self.listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.listbox.add_css_class("navigation-sidebar")
        self.listbox.connect("row-activated", self._on_row_activated)
        sidebar_view.set_content(Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER,
                                                    child=self.listbox, vexpand=True))
        sidebar_page = Adw.NavigationPage(title=APP_NAME, tag="sidebar", child=sidebar_view)

        # ---- content -------------------------------------------------------
        self.content_view = Adw.ToolbarView()
        self.content_view.add_top_bar(Adw.HeaderBar())
        self.content_page = Adw.NavigationPage(title="Overview", tag="content", child=self.content_view)

        self.split = Adw.NavigationSplitView(sidebar=sidebar_page, content=self.content_page)
        self.split.set_min_sidebar_width(270)
        self.split.set_max_sidebar_width(340)

        # ---- bottom bar ----------------------------------------------------
        action_bar = Gtk.ActionBar()
        self.selection_label = Gtk.Label(label="Nothing selected", xalign=0)
        action_bar.pack_start(self.selection_label)
        self.clean_button = Gtk.Button(label="Clean Selected…", sensitive=False)
        self.clean_button.add_css_class("suggested-action")
        self.clean_button.connect("clicked", lambda *_: self.confirm_clean())
        action_bar.pack_end(self.clean_button)

        outer = Adw.ToolbarView(content=self.split)
        outer.add_bottom_bar(action_bar)
        self.toasts = Adw.ToastOverlay(child=outer)
        self.set_content(self.toasts)

        bp = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 720sp"))
        bp.add_setter(self.split, "collapsed", True)
        self.add_breakpoint(bp)

        self._build_sidebar()
        self.show_page("overview")
        GLib.idle_add(self.start_scan)

    # ------------------------------------------------------------------ sidebar

    def _build_sidebar(self) -> None:
        overview = SidebarRow("overview", "Overview", "drive-harddisk-symbolic")
        self.rows["overview"] = overview
        self.listbox.append(overview)
        for sid, title, icon, _func in SCANNERS:
            row = SidebarRow(sid, title, icon)
            self.rows[sid] = row
            self.listbox.append(row)
        self.listbox.select_row(overview)

    def _refresh_sidebar(self) -> None:
        total = sum(c.total for c in self.categories.values() if c.available)
        self.rows["overview"].sub.set_label("Scanning…" if self.scanning else f"{human_size(total)} can be freed")
        for sid, _t, _i, _f in SCANNERS:
            row = self.rows[sid]
            cat = self.categories.get(sid)
            if cat is None:
                row.sub.set_label("Scanning…" if self.scanning else "")
                row.set_risk(None)
                row.set_opacity(1.0)
                continue
            if not cat.available:
                row.sub.set_label("Not needed on this system")
                row.set_opacity(0.55)
                row.set_risk(None)
                continue
            row.set_opacity(1.0)
            if cat.error:
                row.sub.set_label("Couldn't scan")
                row.set_risk(None)
                continue
            text = human_size(cat.total) if cat.total else "Nothing to clean"
            if cat.total:
                text += " · " + self._selection_word(cat)
            row.sub.set_label(text)
            row.set_risk(cat.risk if cat.total else None)

    def _on_row_activated(self, _listbox, row: SidebarRow) -> None:
        self.show_page(row.key)
        self.split.set_show_content(True)

    # ------------------------------------------------------------------ scanning

    def start_scan(self) -> bool:
        if self.scanning:
            return False
        self.scanning = True
        self.scan_button.set_sensitive(False)
        self.categories.clear()
        self.selected.clear()
        self.choices.clear()
        self._refresh_sidebar()
        self._update_selection()
        self.show_page(self.current)
        threading.Thread(target=self._scan_thread, daemon=True).start()
        return False

    def _scan_thread(self, only: str | None = None) -> None:
        ctx = ScanContext.create()
        GLib.idle_add(self._on_disks, filesystems())
        scanners = [s for s in SCANNERS if only is None or s[0] == only]
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(run_scanner, s[3], ctx) for s in scanners]
            for fut in as_completed(futures):
                GLib.idle_add(self._on_category, fut.result())
        GLib.idle_add(self._on_scan_done)

    def rescan_category(self, sid: str) -> None:
        if self.scanning:
            return
        self.scanning = True
        self.scan_button.set_sensitive(False)
        threading.Thread(target=self._scan_thread, args=(sid,), daemon=True).start()

    def _on_disks(self, disks: list[FileSystem]) -> bool:
        self.disks = disks
        if self.current == "overview":
            self.show_page("overview")
        return False

    def _on_category(self, cat: Category) -> bool:
        self.categories[cat.id] = cat
        for item in cat.items:
            key = f"{cat.id}/{item.id}"
            self.selected[key] = item.selected_by_default and not item.blocked
            if item.choices:
                self.choices[key] = item.default_choice
        self._refresh_sidebar()
        self._update_selection()
        if self.current in (cat.id, "overview"):
            self.show_page(self.current)
        return False

    def _on_scan_done(self) -> bool:
        self.scanning = False
        self.scan_button.set_sensitive(True)
        self._refresh_sidebar()
        if self.current == "overview":
            self.show_page("overview")
        return False

    # ------------------------------------------------------------------ selection

    def _selected_size(self, cat: Category) -> int:
        total = 0
        for item in cat.items:
            key = f"{cat.id}/{item.id}"
            if self.selected.get(key) and not item.blocked:
                total += item_size(item, self.choices.get(key))
        return total

    def _selection_state(self, cat: Category) -> tuple[int, int]:
        """(selected items, selectable items) in a category."""
        selectable = [i for i in cat.items if not i.blocked]
        chosen = sum(1 for i in selectable if self.selected.get(f"{cat.id}/{i.id}"))
        return chosen, len(selectable)

    def _selection_word(self, cat: Category) -> str:
        chosen, total = self._selection_state(cat)
        if total == 0 or chosen == 0:
            return "not selected"
        if chosen == total:
            return "all selected"
        return f"{human_size(self._selected_size(cat))} of it selected"

    def _update_selection(self) -> None:
        count = sum(1 for v in self.selected.values() if v)
        size = sum(self._selected_size(c) for c in self.categories.values())
        if count:
            self.selection_label.set_label(f"Selected: {count} item{'s' if count != 1 else ''} · "
                                           f"frees about {human_size(size)}")
        else:
            self.selection_label.set_label("Scanning…" if self.scanning
                                           else "Nothing selected — tick what you want to clean")
        self.clean_button.set_sensitive(count > 0)
        self._refresh_sidebar()
        self._sync_overview()

    def _sync_overview(self) -> None:
        """Update the Overview's checkboxes and numbers in place (no rebuild, keeps scroll position)."""
        self._syncing = True
        try:
            for sid, (check, row) in self._ov_checks.items():
                cat = self.categories.get(sid)
                if cat is None:
                    continue
                chosen, total = self._selection_state(cat)
                check.set_inconsistent(0 < chosen < total)
                check.set_active(total > 0 and chosen == total)
                row.set_subtitle(f"{cat.risk.label} · {self._selection_word(cat)}")
            if self._hero_selected is not None:
                size = sum(self._selected_size(c) for c in self.categories.values())
                self._hero_selected.set_label(human_size(size))
        finally:
            self._syncing = False

    def _toggle_category(self, cat: Category, check: Gtk.CheckButton) -> None:
        if self._syncing:
            return
        chosen, total = self._selection_state(cat)
        value = chosen < total          # partly or not selected -> select all; all selected -> none
        for item in cat.items:
            self.selected[f"{cat.id}/{item.id}"] = value and not item.blocked
        self._update_selection()

    def set_selected(self, key: str, value: bool) -> None:
        self.selected[key] = value
        self._update_selection()

    def select_where(self, predicate) -> None:
        for cat in self.categories.values():
            for item in cat.items:
                key = f"{cat.id}/{item.id}"
                self.selected[key] = bool(predicate(cat, item)) and not item.blocked
        self._update_selection()
        if self.current != "overview":      # the Overview updates itself in place
            self.show_page(self.current)

    # ------------------------------------------------------------------ pages

    def show_page(self, key: str) -> None:
        self.current = key
        row = self.rows.get(key)
        if row is not None and self.listbox.get_selected_row() is not row:
            self.listbox.select_row(row)
        if key == "overview":
            self.content_page.set_title("Overview")
            widget = self._build_overview()
        else:
            self._ov_checks = {}
            self._hero_selected = None
            cat = self.categories.get(key)
            title = next((t for s, t, _i, _f in SCANNERS if s == key), key)
            self.content_page.set_title(title)
            widget = self._build_category(cat) if cat else self._build_scanning(title)
        self.content_view.set_content(widget)

    def _page(self) -> tuple[Gtk.ScrolledWindow, Gtk.Box]:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18, margin_top=24, margin_bottom=32,
                      margin_start=16, margin_end=16)
        clamp = Adw.Clamp(maximum_size=860, tightening_threshold=600, child=box)
        return Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, child=clamp, vexpand=True), box

    def _build_scanning(self, title: str) -> Gtk.Widget:
        page = Adw.StatusPage(title=f"Scanning {title.lower()}…",
                              description="Nothing is changed while scanning.")
        page.set_child(make_spinner())
        return page

    # ---- overview

    def _build_overview(self) -> Gtk.Widget:
        scroller, box = self._page()
        self._ov_checks = {}
        self._hero_selected = None
        available = [c for c in self.categories.values() if c.available and not c.error]
        total = sum(c.total for c in available)
        safe_total = sum(i.size if not i.choices else i.choices[i.default_choice].size
                         for c in available for i in c.items if i.risk is Risk.SAFE and not i.blocked)

        hero = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, halign=Gtk.Align.CENTER)
        if self.scanning and not available:
            hero.append(make_spinner())
            hero.append(center_label("Looking for wasted space…", "title-2"))
            box.append(hero)
        else:
            selected = sum(self._selected_size(c) for c in self.categories.values())
            hero.append(center_label("Selected to clean", "title-4"))
            big = center_label(human_size(selected), "hero-size")
            self._hero_selected = big
            hero.append(big)
            hero.append(center_label(
                f"out of {human_size(total)} that could be freed"
                + (" (still scanning…)" if self.scanning else ""), "dim-label", "hero-caption"))
            buttons = Gtk.Box(spacing=8, halign=Gtk.Align.CENTER, margin_top=10)
            safe_btn = Gtk.Button(label="Select all Safe items",
                                  tooltip_text=f"Ticks everything marked Safe ({human_size(safe_total)})")
            safe_btn.add_css_class("pill")
            safe_btn.connect("clicked", lambda *_: self.select_where(lambda c, i: i.risk is Risk.SAFE))
            clear_btn = Gtk.Button(label="Unselect everything")
            clear_btn.add_css_class("pill")
            clear_btn.connect("clicked", lambda *_: self.select_where(lambda c, i: False))
            buttons.append(safe_btn)
            buttons.append(clear_btn)
            hero.append(buttons)
            box.append(hero)

            # how it works: three numbered steps
            steps = Gtk.Box(spacing=0, homogeneous=True)
            steps.add_css_class("card")
            for n, text in (("1", "Tick the boxes of what you want to remove"),
                            ("2", "Click a row to see what it is and pick single items"),
                            ("3", "Press “Clean Selected…” and confirm")):
                step = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, margin_top=12, margin_bottom=12,
                               margin_start=10, margin_end=10)
                step.append(center_label(n, "step-number", "title-3"))
                step.append(center_label(text, "caption"))
                steps.append(step)
            box.append(steps)

        # what can be cleaned — one checkbox per category
        ranked = sorted((c for c in available if c.total > 0), key=lambda c: -c.total)
        if ranked:
            group = Adw.PreferencesGroup(
                title="What can be cleaned",
                description="Tick a box to clean that whole category. Click the row itself to open it, read "
                            "what it is, and choose single items.")
            biggest = ranked[0].total or 1
            for cat in ranked:
                row = Adw.ActionRow(title=cat.title, activatable=True,
                                    tooltip_text=f"Open “{cat.title}”")
                row.set_use_markup(False)
                check = Gtk.CheckButton(valign=Gtk.Align.CENTER,
                                        tooltip_text="Tick to clean this whole category")
                check.connect("toggled", lambda b, c=cat: self._toggle_category(c, b))
                row.add_prefix(check)
                row.add_prefix(Gtk.Image(icon_name=cat.icon))
                bar = Gtk.LevelBar(min_value=0, max_value=1, value=cat.total / biggest,
                                   valign=Gtk.Align.CENTER, width_request=100)
                for name_ in ("low", "high", "full"):
                    bar.remove_offset_value(name_)
                row.add_suffix(bar)
                size = Gtk.Label(label=human_size(cat.total), width_chars=9, xalign=1)
                size.add_css_class("numeric")
                row.add_suffix(size)
                row.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
                row.connect("activated", lambda _r, sid=cat.id: self._go(sid))
                group.add(row)
                self._ov_checks[cat.id] = (check, row)
            box.append(group)
            self._sync_overview()

        # disks
        if self.disks:
            disks = Adw.PreferencesGroup(title="Your disks", description=os_pretty_name())
            for fs in self.disks:
                name = {"/": "System ( / )", "/home": "Home ( /home )", "/boot": "Boot ( /boot )",
                        "/boot/efi": "EFI ( /boot/efi )"}.get(fs.mountpoint, fs.mountpoint)
                row = Adw.ActionRow(title=name, subtitle=f"{human_size(fs.used)} used of {human_size(fs.total)}"
                                                         f" · {human_size(fs.free)} free")
                row.set_use_markup(False)
                bar = Gtk.LevelBar(min_value=0, max_value=1, value=min(1.0, fs.fraction),
                                   valign=Gtk.Align.CENTER, width_request=160)
                bar.add_css_class("disk")
                for name_ in ("low", "high", "full"):
                    bar.remove_offset_value(name_)
                bar.add_offset_value("high", 0.80)
                bar.add_offset_value("low", 0.92)
                bar.add_offset_value("critical", 1.0)
                row.add_suffix(bar)
                pct = Gtk.Label(label=f"{int(fs.fraction * 100)}%", width_chars=4)
                pct.add_css_class("numeric")
                row.add_suffix(pct)
                disks.add(row)
            box.append(disks)

        unavailable = [c.title for c in self.categories.values() if not c.available]
        if unavailable:
            box.append(wrap_label("Not needed on this system: " + ", ".join(unavailable) + ".",
                                  "dim-label", "caption"))

        # promises
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        card.add_css_class("card")
        card.add_css_class("explain-card")
        card.append(wrap_label("What this app never touches", "explain-heading"))
        card.append(wrap_label(
            "• Your personal files — documents, photos, music, downloads, projects.\n"
            "• App settings, passwords, browser history and bookmarks.\n"
            "• The kernel you are running, and always one fallback kernel.\n"
            "• Anything you don't see listed here.\n\n"
            "Nothing is deleted until you press “Clean Selected…” and confirm. You can see the exact "
            "commands before they run. Only items marked Safe are selected for you; everything else "
            "you choose yourself."))
        box.append(card)

        legend = Adw.PreferencesGroup(title="What the labels mean")
        for risk in Risk:
            row = Adw.ActionRow(title=risk.label, subtitle=risk.meaning)
            row.set_use_markup(False)
            row.add_prefix(self._dot(risk))
            legend.add(row)
        box.append(legend)
        return scroller

    def _dot(self, risk: Risk) -> Gtk.Widget:
        dot = Gtk.Box(valign=Gtk.Align.CENTER)
        dot.add_css_class("risk-dot")
        dot.add_css_class(risk.css)
        return dot

    def _go(self, sid: str) -> None:
        self.show_page(sid)
        self.split.set_show_content(True)

    # ---- category page

    def _build_category(self, cat: Category) -> Gtk.Widget:
        if not cat.available:
            return Adw.StatusPage(icon_name=cat.icon, title="Not needed on this system",
                                  description=cat.unavailable_reason)
        scroller, box = self._page()

        head = Gtk.Box(spacing=16)
        icon = Gtk.Image(icon_name=cat.icon, pixel_size=48, valign=Gtk.Align.START)
        icon.add_css_class("dim-label")
        head.append(icon)
        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, hexpand=True)
        texts.append(wrap_label(cat.title, "title-1"))
        meta = Gtk.Box(spacing=10)
        size_text = (f"{'About ' if cat.size_is_estimate else ''}{human_size(cat.total)} can be freed"
                     if cat.total else "Nothing to clean right now")
        meta.append(wrap_label(size_text, "title-4"))
        if cat.total:
            meta.append(risk_badge(cat.risk))
        if cat.needs_admin and cat.total:
            lock = Gtk.Image(icon_name="system-lock-screen-symbolic",
                             tooltip_text="Needs your password, because these files belong to the system")
            lock.add_css_class("dim-label")
            meta.append(lock)
        texts.append(meta)
        head.append(texts)
        box.append(head)

        if cat.error:
            box.append(self._notice(cat.error))
        for w in cat.warnings:
            box.append(self._notice(w))

        # explanation cards
        e = cat.explanation
        flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=2,
                           min_children_per_line=1, column_spacing=12, row_spacing=12, homogeneous=True)
        cards = [("What is it?", e.what), ("Why does it get so big?", e.why),
                 ("Is it safe to delete?", e.safe), ("What this app will do", e.how)]
        for heading, text in cards:
            if not text:
                continue
            card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, valign=Gtk.Align.FILL)
            card.add_css_class("card")
            card.add_css_class("explain-card")
            card.append(wrap_label(heading, "explain-heading"))
            card.append(wrap_label(text))
            child = Gtk.FlowBoxChild(child=card, focusable=False)
            flow.append(child)
        box.append(flow)
        if e.tip:
            tip = Gtk.Box(spacing=10)
            tip.add_css_class("card")
            tip.add_css_class("notice-card")
            tip.append(Gtk.Image(icon_name="dialog-information-symbolic", valign=Gtk.Align.START))
            tip.append(wrap_label("Tip: " + e.tip, selectable=True))
            box.append(tip)
        for line in cat.info:
            box.append(wrap_label(line, "dim-label"))

        # items
        if cat.items:
            group = Adw.PreferencesGroup(
                title="Found on this computer",
                description="Ticked items will be removed when you press “Clean Selected…”. "
                            "Click a row to tick or untick it.")
            suffix = Gtk.Box(spacing=6, valign=Gtk.Align.END)
            all_btn = Gtk.Button(label="Select all")
            all_btn.connect("clicked", lambda *_: self._select_category(cat, True))
            none_btn = Gtk.Button(label="Unselect all")
            none_btn.connect("clicked", lambda *_: self._select_category(cat, False))
            suffix.append(all_btn)
            suffix.append(none_btn)
            group.set_header_suffix(suffix)
            for item in cat.items:
                group.add(self._item_row(cat, item))
            box.append(group)

        if cat.settings:
            sgroup = Adw.PreferencesGroup(title="Keep it from growing again",
                                          description="These change a system setting right away when you press "
                                                      "Apply (needs your password).")
            for setting in cat.settings:
                sgroup.add(self._setting_row(cat, setting))
            box.append(sgroup)
        return scroller

    def _notice(self, text: str) -> Gtk.Widget:
        notice = Gtk.Box(spacing=10)
        notice.add_css_class("card")
        notice.add_css_class("notice-card")
        notice.add_css_class("warning")
        img = Gtk.Image(icon_name="dialog-warning-symbolic", valign=Gtk.Align.START)
        img.add_css_class("warning")
        notice.append(img)
        notice.append(wrap_label(text, selectable=True))
        return notice

    def _item_row(self, cat: Category, item: Item) -> Gtk.Widget:
        key = f"{cat.id}/{item.id}"
        subtitle = item.subtitle
        if item.note:
            subtitle = (subtitle + "\n" if subtitle else "") + "⚠ " + item.note
        row = Adw.ActionRow(title=item.title, subtitle=subtitle)
        row.set_use_markup(False)
        check = Gtk.CheckButton(valign=Gtk.Align.CENTER, active=bool(self.selected.get(key)))
        check.set_sensitive(not item.blocked)
        check.connect("toggled", lambda b: self.set_selected(key, b.get_active()))
        row.add_prefix(check)
        if not item.blocked:
            row.set_activatable_widget(check)
        if item.risk is not cat.risk:
            row.add_suffix(risk_badge(item.risk))
        size_label = Gtk.Label(label=human_size(item_size(item, self.choices.get(key))), width_chars=9, xalign=1)
        size_label.add_css_class("numeric")
        if item.choices:
            dropdown = Gtk.DropDown.new_from_strings([c.label for c in item.choices])
            dropdown.set_valign(Gtk.Align.CENTER)
            dropdown.set_selected(self.choices.get(key, item.default_choice))

            def on_choice(dd, _pspec, key=key, item=item, size_label=size_label):
                self.choices[key] = dd.get_selected()
                size_label.set_label(human_size(item_size(item, self.choices[key])))
                self._update_selection()
            dropdown.connect("notify::selected", on_choice)
            row.add_suffix(dropdown)
        row.add_suffix(size_label)
        return row

    def _select_category(self, cat: Category, value: bool) -> None:
        for item in cat.items:
            self.selected[f"{cat.id}/{item.id}"] = value and not item.blocked
        self._update_selection()
        self.show_page(cat.id)

    def _setting_row(self, cat: Category, setting: Setting) -> Gtk.Widget:
        row = Adw.ActionRow(title=setting.title, subtitle=setting.subtitle)
        row.set_use_markup(False)
        labels = [label for label, _v in setting.choices]
        values = [v for _l, v in setting.choices]
        dropdown = Gtk.DropDown.new_from_strings(labels)
        dropdown.set_valign(Gtk.Align.CENTER)
        if setting.current in values:
            dropdown.set_selected(values.index(setting.current))
        apply = Gtk.Button(label="Apply", valign=Gtk.Align.CENTER)
        apply.connect("clicked", lambda *_: self.apply_setting(cat, setting, values[dropdown.get_selected()]))
        row.add_suffix(dropdown)
        row.add_suffix(apply)
        return row

    # ------------------------------------------------------------------ actions

    def apply_setting(self, cat: Category, setting: Setting, value: str) -> None:
        op = {"op": setting.op, setting.param: value}

        def work():
            failures, errors = run_root_ops([op], lambda _line: None)
            GLib.idle_add(done, failures, errors)

        def done(failures, errors):
            if failures:
                self.toasts.add_toast(Adw.Toast(title=errors[0] if errors else "Could not change the setting",
                                                timeout=8))
            else:
                self.toasts.add_toast(Adw.Toast(title=f"Saved: {setting.title.lower()} → {value}"))
                self.rescan_category(cat.id)
            return False

        threading.Thread(target=work, daemon=True).start()

    def confirm_clean(self) -> None:
        plan = build_plan(list(self.categories.values()), self.selected, self.choices)
        if plan.is_empty():
            return
        by_cat: dict[str, list[int]] = {}
        for entry in plan.entries:
            by_cat.setdefault(entry.category, []).append(entry.size)
        lines = [f"• {name}: {len(sizes)} item{'s' if len(sizes) != 1 else ''} ({human_size(sum(sizes))})"
                 for name, sizes in by_cat.items()]
        body = "\n".join(lines) + "\n\nThis can't be undone."
        if plan.needs_admin:
            body += " You'll be asked for your password once."
        dialog = new_alert(self, f"Free about {human_size(plan.size)}?", body)

        commands = Gtk.Label(label="\n".join(plan.preview()), xalign=0, yalign=0, selectable=True, wrap=True,
                             wrap_mode=Pango.WrapMode.WORD_CHAR)
        commands.add_css_class("mono")
        scroll = Gtk.ScrolledWindow(child=commands, max_content_height=220, propagate_natural_height=True,
                                    hscrollbar_policy=Gtk.PolicyType.NEVER)
        expander = Gtk.Expander(label="Show the exact commands", child=scroll)
        dialog.set_extra_child(expander)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("clean", "Clean")
        dialog.set_response_appearance("clean", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        def on_response(_dialog, response: str) -> None:
            if response == "clean":
                self.run_clean(plan)
        dialog.connect("response", on_response)
        present_dialog(dialog, self)

    def run_clean(self, plan: Plan) -> None:
        dlg = ProgressDialog(self, plan)
        dlg.show_it()

        def log(line: str) -> None:
            GLib.idle_add(dlg.append, line)

        def work():
            freed, errors = execute(plan, log)
            GLib.idle_add(dlg.finish, freed, errors)

        threading.Thread(target=work, daemon=True).start()


class ProgressDialog:
    """Shows the log while cleaning; rescans when closed."""

    def __init__(self, window: MainWindow, plan: Plan):
        self.window = window
        self.plan = plan
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_top=12, margin_bottom=18,
                          margin_start=18, margin_end=18)
        top = Gtk.Box(spacing=12)
        self.spinner = make_spinner()
        top.append(self.spinner)
        self.status = wrap_label("Cleaning…", "title-3")
        top.append(self.status)
        content.append(top)
        self.buffer = Gtk.TextBuffer()
        self.view = Gtk.TextView(buffer=self.buffer, editable=False, cursor_visible=False, monospace=True,
                                 wrap_mode=Gtk.WrapMode.WORD_CHAR, top_margin=8, bottom_margin=8, left_margin=8, right_margin=8)
        scroll = Gtk.ScrolledWindow(child=self.view, vexpand=True, min_content_height=260)
        scroll.add_css_class("card")
        content.append(scroll)
        self.close_button = Gtk.Button(label="Close", sensitive=False, halign=Gtk.Align.END)
        self.close_button.add_css_class("pill")
        self.close_button.connect("clicked", lambda *_: self.close())
        self.dialog: Any
        content.append(self.close_button)

        if hasattr(Adw, "Dialog"):
            self.dialog = Adw.Dialog(title="Cleaning", content_width=640, content_height=460)
            view = Adw.ToolbarView(content=content)
            header = Adw.HeaderBar(show_end_title_buttons=False)
            view.add_top_bar(header)
            self.dialog.set_child(view)
            self.dialog.set_can_close(False)
        else:
            self.dialog = Adw.Window(title="Cleaning", modal=True, transient_for=window,
                                     default_width=640, default_height=460, deletable=False)
            view = Adw.ToolbarView(content=content)
            view.add_top_bar(Adw.HeaderBar(show_end_title_buttons=False))
            self.dialog.set_content(view)

    def show_it(self) -> None:
        if isinstance(self.dialog, Adw.Window):
            self.dialog.present()
        else:
            self.dialog.present(self.window)

    def append(self, line: str) -> bool:
        end = self.buffer.get_end_iter()
        self.buffer.insert(end, line + "\n")
        self.buffer.place_cursor(self.buffer.get_end_iter())
        self.view.scroll_to_mark(self.buffer.get_insert(), 0.0, False, 0.0, 0.0)
        return False

    def finish(self, freed: int, errors: list[str]) -> bool:
        self.spinner.set_visible(False)
        if errors:
            self.status.set_label(f"Freed {human_size(freed)} — {len(errors)} thing(s) were skipped, see below")
            self.append("")
            for err in errors:
                self.append("✗ " + err)
        else:
            self.status.set_label(f"Done — freed {human_size(freed)}")
        self.append("")
        self.append(f"Free space grew by {human_size(freed)} (measured on your disks).")
        self.close_button.set_sensitive(True)
        self.close_button.add_css_class("suggested-action")
        if isinstance(self.dialog, Adw.Window):
            self.dialog.set_deletable(True)
        else:
            self.dialog.set_can_close(True)
            self.dialog.connect("closed", lambda *_: self.window.start_scan())
        return False

    def close(self) -> None:
        if isinstance(self.dialog, Adw.Window):
            self.dialog.close()
            self.window.start_scan()
        else:
            self.dialog.close()


class Application(Adw.Application):
    def __init__(self):
        flags = getattr(Gio.ApplicationFlags, "DEFAULT_FLAGS", Gio.ApplicationFlags.FLAGS_NONE)
        super().__init__(application_id=APP_ID, flags=flags)
        self.window: MainWindow | None = None
        self._add_action("about", self.on_about)
        self._add_action("quit", lambda *_: self.quit(), ["<Control>q"])
        self._add_action("rescan", lambda *_: self.window and self.window.start_scan(), ["<Control>r"])

    def _add_action(self, name: str, callback, accels: list[str] | None = None) -> None:
        action = Gio.SimpleAction.new(name, None)
        action.connect("activate", callback)
        self.add_action(action)
        if accels:
            self.set_accels_for_action(f"app.{name}", accels)

    def do_startup(self) -> None:
        Adw.Application.do_startup(self)
        provider = Gtk.CssProvider()
        if hasattr(provider, "load_from_string"):
            provider.load_from_string(CSS)
        else:
            provider.load_from_data(CSS.encode())
        display = Gdk.Display.get_default()
        if display is not None:
            Gtk.StyleContext.add_provider_for_display(display, provider,
                                                      Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
            # When running from the source tree, find the icon in ./data/icons
            dev_icons = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "data", "icons"))
            if os.path.isdir(dev_icons):
                Gtk.IconTheme.get_for_display(display).add_search_path(dev_icons)
        Gtk.Window.set_default_icon_name(APP_ID)

    def do_activate(self) -> None:
        if self.window is None:
            self.window = MainWindow(self)
            if os.geteuid() == 0:
                self.window.toasts.add_toast(Adw.Toast(
                    title="Don't run this app as root — it would clean root's files instead of yours.",
                    timeout=0))
        self.window.present()

    def on_about(self, *_args) -> None:
        kwargs: dict[str, Any] = dict(application_name=APP_NAME, application_icon=APP_ID, version=VERSION,
                      developer_name="cyb3rl00m", license_type=Gtk.License.MIT_X11, website=WEBSITE,
                      issue_url=WEBSITE + "/issues",
                      comments="Shows where disk space is wasted on your Linux system and explains, in plain "
                               "language, what is safe to delete.")
        if hasattr(Adw, "AboutDialog"):
            Adw.AboutDialog(**kwargs).present(self.window)
        else:
            Adw.AboutWindow(transient_for=self.window, **kwargs).present()


def run_app(argv: list[str]) -> int:
    return Application().run(argv)
