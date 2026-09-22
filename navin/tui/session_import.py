# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Interactive import of external chats into the current CLI workspace."""

from __future__ import annotations

import asyncio
from pathlib import Path

from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, SelectionList, Static

from navin.session import import_sessions
from navin.tui.screens import FormField, FormScreen, PickerScreen, PickItem

SOURCES = (("claude-code", "Claude Code"), ("codex", "Codex"),
           ("opencode", "OpenCode"), ("oh-my-pi", "oh-my-pi / OMP"), ("cursor", "Cursor"))


class SessionImportScreen(ModalScreen[bool]):
    BINDINGS = [Binding("escape", "close", "Close")]
    DEFAULT_CSS = """
    SessionImportScreen { align: center middle; background: $background; }
    SessionImportScreen > Vertical {
        width: 90; max-width: 96%; height: auto; max-height: 95%;
        border: solid #5A7A9A; background: $surface; padding: 1 2;
        overflow-y: auto;
    }
    SessionImportScreen #sources { height: 8; margin: 1 0; }
    SessionImportScreen #status { height: auto; min-height: 2; margin: 1 0; }
    SessionImportScreen #roots { height: auto; max-height: 6; overflow-y: auto; }
    SessionImportScreen Horizontal { height: auto; }
    SessionImportScreen Button { min-width: 12; margin-right: 1; }
    """

    def __init__(self, workspace: Path) -> None:
        super().__init__()
        self.workspace = workspace
        self._reports: list[import_sessions.SourceReport] = []
        self._busy = False
        self._importing = False
        self._imported = False
        self._import_screen_closing = False

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("Import chats", markup=False)
            yield Static(f"Into: {self.workspace}\nSpace: select a source. Existing chats are kept.", markup=False)
            yield SelectionList[str](id="sources")
            yield Static("", id="roots", markup=False)
            with Horizontal():
                yield Button("Scan again", id="scan")
                yield Button("Add folder", id="add-root")
                yield Button("Remove folder", id="remove-root")
            yield Static("", id="status", markup=False)
            with Horizontal():
                yield Button("Import selected", id="import", variant="primary", disabled=True)
                yield Button("Close", id="close")

    def on_mount(self) -> None:
        self.scan()

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        if self._import_screen_closing or not self.query("#scan"):
            return
        for name in ("scan", "add-root", "remove-root", "import"):
            self.query_one(f"#{name}", Button).disabled = busy or (name == "import" and not self._reports)
        self.query_one("#sources", SelectionList).disabled = busy
        self.query_one("#close", Button).disabled = self._importing

    def _status(self, message: str) -> None:
        if not self._import_screen_closing and self.query("#status"):
            self.query_one("#status", Static).update(message)

    @work(exclusive=True, group="import-scan")
    async def scan(self) -> None:
        self._set_busy(True)
        self._status("Scanning Claude Code, Codex, OpenCode, OMP and Cursor... Large histories can take several minutes.")
        try:
            self._reports = await asyncio.to_thread(import_sessions.discover, import_sessions.resolve_sources("auto"))
            if self._import_screen_closing:
                return
            choices = self.query_one("#sources", SelectionList)
            choices.clear_options()
            for report in self._reports:
                count = len(report.sessions)
                choices.add_option((f"{report.label}: {count} {'chat' if count == 1 else 'chats'}" if count else f"{report.label}: {report.note or 'no chats found'}",
                                    report.name, count > 0))
            roots = import_sessions.load_extra_roots()
            self.query_one("#roots", Static).update("\n".join(
                f"{source}: {path}" for source, paths in roots.items() for path in paths
            ))
            self._status("Choose sources, then Import selected. Add a folder for chats stored elsewhere.")
        except Exception as exc:  # noqa: BLE001
            self._reports = []
            self._status(f"Scan failed: {exc}")
        finally:
            if self.is_mounted:
                self._set_busy(False)

    @on(Button.Pressed, "#scan")
    def scan_again(self) -> None:
        self.scan()

    @on(Button.Pressed, "#import")
    @work(exclusive=True, group="import-write")
    async def import_selected(self) -> None:
        selected = set(self.query_one("#sources", SelectionList).selected)
        reports = [report for report in self._reports if report.name in selected and report.sessions]
        if not reports:
            self._status("Select at least one source containing chats.")
            return
        self._importing = True
        self._set_busy(True)
        self._status("Importing selected chats... Large histories can take several minutes.")
        try:
            # Reuse the scan so a large history is parsed only once.
            stats = await asyncio.to_thread(import_sessions.import_to_workspace, reports, self.workspace)
            imported = sum(stat.imported for stat in stats)
            skipped = sum(stat.skipped_existing for stat in stats)
            self._imported = self._imported or imported > 0
            self._status(f"{imported} chats imported, {skipped} already present. Close, then open Sessions (ctrl+s) to resume a chat.")
        except Exception as exc:  # noqa: BLE001
            self._status(f"Import failed: {exc}")
        finally:
            self._importing = False
            if self.is_mounted:
                self._set_busy(False)

    @on(Button.Pressed, "#add-root")
    @work(group="import-root")
    async def add_root(self) -> None:
        answer = await self.app.push_screen_wait(FormScreen("Add chat folder", [
            FormField("source", "Source", kind="select", options=SOURCES, value="codex"),
            FormField("path", "Folder", placeholder="~/.codex/sessions"),
        ], submit_label="Add and scan"))
        if answer:
            try:
                import_sessions.save_extra_root(answer["source"], answer["path"])
                self.scan()
            except (ValueError, OSError) as exc:
                self._status(str(exc))

    @on(Button.Pressed, "#remove-root")
    @work(group="import-root")
    async def remove_root(self) -> None:
        roots = [(source, path) for source, paths in import_sessions.load_extra_roots().items() for path in paths]
        if not roots:
            self._status("No extra folder configured.")
            return
        chosen = await self.app.push_screen_wait(PickerScreen("Remove chat folder", [
            PickItem(str(index), source, path) for index, (source, path) in enumerate(roots)
        ]))
        if chosen is not None:
            try:
                import_sessions.remove_extra_root(*roots[int(chosen)])
                self.scan()
            except OSError as exc:
                self._status(str(exc))

    @on(Button.Pressed, "#close")
    def action_close(self) -> None:
        if not self._importing:
            self._import_screen_closing = True
            self.dismiss(self._imported)
