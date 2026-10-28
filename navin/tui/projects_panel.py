# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Projects and their sessions in the CLI sidebar, laid out like the desktop panel.

Folder names in bold, sessions in grey under them, three at first and three more
per "more", a spinner in front of every session that is working (in any project:
the CLI runs a project left while it worked in the background) and next to its
folder, and on the right of each session an arrow that opens it in a new tab of
the terminal plus a small menu. The panel scrolls when the list outgrows the
screen. It only draws and reports clicks (:class:`ProjectsPanel.Hit`); the app
decides what a click does, because opening a session of another project moves
the engine to that folder.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from functools import partial

from textual import events
from textual.containers import Horizontal, VerticalScroll
from textual.message import Message
from textual.widgets import Static

from navin.tui.projects import (
    PAGE_SIZE,
    ProjectEntry,
    next_visible,
    same_path,
)
from navin.tui.textmarkup import escape

SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
_PIN = "◆"
_QUIET = "·"
# Opens the session in a new terminal tab (an arrow every terminal font has).
TERMINAL_MARK = "↗"
TERMINAL_TIP = "Open in a new terminal tab"

# Hit kinds the panel handles itself (they only change what is folded open).
LOCAL_KINDS = frozenset({"toggle", "more", "less"})

# A session row starts under the folder square, so its title lines up with
# the folder name ("▾ ■ name" puts the name at column 4).
_SESSION_INDENT = "  "


class _Hit(Static):
    """A clickable, focusable piece of text that reports ``(kind, path, key)``."""

    can_focus = True

    def __init__(self, text: str, kind: str, path: str = "", key: str = "", *, classes: str = "", tooltip: str | None = None) -> None:
        super().__init__(text, markup=True, classes=f"proj-hit {classes}".strip())
        self.kind = kind
        self.path = path
        self.key = key
        if tooltip:
            self.tooltip = tooltip

    def on_click(self, event: events.Click) -> None:
        event.stop()
        self.post_message(ProjectsPanel.Hit(self.kind, self.path, self.key))

    def on_key(self, event: events.Key) -> None:
        if event.key in {"enter", "space"}:
            event.stop()
            self.post_message(ProjectsPanel.Hit(self.kind, self.path, self.key))


class _FolderLabel(_Hit):
    """A project: fold chevron, colored square, bold name, a spinner while it works."""

    def __init__(self, project: ProjectEntry, opened: bool) -> None:
        super().__init__("", "toggle", project.path, classes="proj-name")
        chevron = "▾" if opened else "▸"
        self._base = f"[$text-muted]{chevron}[/] [{project.color}]■[/] [b $foreground]{escape(project.name)}[/]"
        self.paint(None)

    def paint(self, frame: int | None) -> None:
        spin = f" [$accent]{SPINNER[frame % len(SPINNER)]}[/]" if frame is not None else ""
        self.update(f"{self._base}{spin}")


class _SessionLabel(_Hit):
    """One session: spinner or pin mark, then its title."""

    def __init__(self, path: str, key: str, title: str, pinned: bool, current: bool) -> None:
        super().__init__("", "open", path, key, classes="proj-session-label")
        self.title = title
        self.pinned = pinned
        self.is_current = current
        self.set_class(current, "-current")
        self.paint(None)

    def paint(self, frame: int | None) -> None:
        """``frame`` is the spinner step while this session works, else None."""
        if frame is not None:
            mark = f"[$accent]{SPINNER[frame % len(SPINNER)]}[/]"
        elif self.pinned:
            mark = f"[$warning]{_PIN}[/]"
        else:
            mark = f"[$text-muted 50%]{_QUIET}[/]"
        ink = "b $foreground" if self.is_current else "$text-muted"
        self.update(f"{_SESSION_INDENT}{mark} [{ink}]{escape(self.title)}[/]")


class ProjectsPanel(VerticalScroll):
    """The projects of this machine with their latest sessions."""

    class Hit(Message):
        """Something in the list was clicked or activated with the keyboard."""

        def __init__(self, kind: str, path: str = "", key: str = "") -> None:
            super().__init__()
            self.kind = kind
            self.path = path
            self.key = key

    DEFAULT_CSS = """
    ProjectsPanel {
        height: 1fr;
        min-height: 6;
        padding: 0;
        scrollbar-size-vertical: 1;
        scrollbar-color: $text-muted 35%;
        scrollbar-color-hover: $text-muted 70%;
        scrollbar-background: $background;
    }
    ProjectsPanel .proj-row { height: 1; width: 1fr; }
    ProjectsPanel .proj-session { margin: 1 0 0 0; }
    ProjectsPanel .proj-gap { height: 1; }
    ProjectsPanel .proj-name {
        width: 1fr;
        height: 1;
        text-wrap: nowrap;
        text-overflow: ellipsis;
    }
    ProjectsPanel .proj-name:hover { background: $boost; }
    ProjectsPanel .proj-new {
        width: auto;
        height: 1;
        padding: 0 1 0 1;
        color: $text-muted 80%;
    }
    ProjectsPanel .proj-new:hover { color: $foreground; background: $boost; }
    ProjectsPanel .proj-session-label {
        width: 1fr;
        height: 1;
        text-wrap: nowrap;
        text-overflow: ellipsis;
    }
    ProjectsPanel .proj-session-label:hover { background: $boost; }
    ProjectsPanel .proj-session-label.-current { background: $boost 55%; }
    ProjectsPanel .proj-menu {
        width: 3;
        height: 1;
        content-align: center middle;
        color: $text-muted 70%;
    }
    ProjectsPanel .proj-menu:hover { color: $foreground; background: $boost; }
    ProjectsPanel .proj-step {
        width: 1fr;
        height: 1;
        color: $text-muted 75%;
    }
    ProjectsPanel .proj-step:hover { color: $foreground; background: $boost; }
    ProjectsPanel .proj-empty { height: 1; margin: 1 0 0 0; color: $text-muted 60%; padding: 0 0 0 4; }
    ProjectsPanel .proj-hint { height: auto; color: $text-muted; padding: 1 1; }
    ProjectsPanel .proj-hit:focus { background: $boost; }
    """

    def __init__(self, *, id: str | None = None, classes: str | None = None) -> None:
        super().__init__(id=id, classes=classes)
        self._projects: list[ProjectEntry] = []
        self._current_path = ""
        self._current_key = ""
        self._open: set[str] = set()
        self._shown: dict[str, int] = {}
        # Working sessions by project id, for every project open in this CLI.
        self._busy: dict[str, frozenset[str]] = {}
        self._labels: dict[tuple[str, str], _SessionLabel] = {}
        self._folders: dict[str, _FolderLabel] = {}
        self._spin = 0
        self._generation = 0
        self._seeded = False

    # -- data -----------------------------------------------------------------

    def set_data(self, projects: list[ProjectEntry], current_path: str, current_key: str) -> None:
        """New list from disk. The project open here starts unfolded, once."""
        self._projects = projects
        self._current_path = current_path
        self._current_key = current_key
        if not self._seeded and current_path:
            self._open.add(self._pid(current_path))
            self._seeded = True
        self.refresh_rows()

    def set_current(self, path: str, key: str) -> None:
        if (path, key) == (self._current_path, self._current_key):
            return
        self._current_path, self._current_key = path, key
        self.refresh_rows()

    def title_of(self, path: str, key: str) -> str:
        for project in self._projects:
            if same_path(project.path, path):
                return next((s.title for s in project.sessions if s.key == key), "")
        return ""

    def is_pinned(self, path: str, key: str) -> bool:
        for project in self._projects:
            if same_path(project.path, path):
                return any(s.key == key and s.pinned for s in project.sessions)
        return False

    def set_running(self, keys: Iterable[str]) -> None:
        """Sessions of the project open here that have a turn in progress."""
        keys = set(keys)
        self.set_busy({self._current_path: keys} if keys else {})

    def set_busy(self, by_project: Mapping[str, Iterable[str]]) -> None:
        """Working sessions of every project, by folder path."""
        busy = {
            self._pid(path): frozenset(keys)
            for path, keys in by_project.items()
            if keys
        }
        if busy == self._busy:
            return
        self._busy = busy
        self._paint_labels()

    def is_busy(self, path: str, key: str = "") -> bool:
        keys = self._busy.get(self._pid(path), frozenset())
        return key in keys if key else bool(keys)

    def tick(self) -> None:
        """One spinner step; free when nothing is working."""
        if not self._busy:
            return
        self._spin += 1
        self._paint_labels()

    # -- state ----------------------------------------------------------------

    @staticmethod
    def _pid(path: str) -> str:
        return path.replace("\\", "/").rstrip("/").lower()

    def is_open(self, path: str) -> bool:
        return self._pid(path) in self._open

    def shown_count(self, path: str) -> int:
        return self._shown.get(self._pid(path), PAGE_SIZE)

    def on_projects_panel_hit(self, event: Hit) -> None:
        if event.kind not in LOCAL_KINDS:
            return  # the app decides: open, new agent, menu, add
        event.stop()
        pid = self._pid(event.path)
        if event.kind == "toggle":
            self._open.symmetric_difference_update({pid})
        elif event.kind == "more":
            total = next((len(p.sessions) for p in self._projects if self._pid(p.path) == pid), 0)
            self._shown[pid] = next_visible(self._shown.get(pid, PAGE_SIZE), total)
        else:
            self._shown[pid] = PAGE_SIZE
        self.refresh_rows()

    # -- drawing --------------------------------------------------------------

    def refresh_rows(self) -> None:
        self._generation += 1
        self.run_worker(partial(self._rebuild, self._generation), exclusive=True, group="projects-rows")

    async def _rebuild(self, generation: int) -> None:
        offset = self.scroll_y
        await self.remove_children()
        if generation != self._generation:
            return
        self._labels = {}
        self._folders = {}
        widgets = self._build()
        await self.mount(*widgets)
        self._paint_labels()
        self.call_after_refresh(self.scroll_to, y=offset, animate=False)

    def _build(self) -> list[Static | Horizontal]:
        if not self._projects:
            return [Static("No project yet. Press + Add and type a folder path.", classes="proj-hint")]
        out: list[Static | Horizontal] = []
        for index, project in enumerate(self._projects):
            if index:
                out.append(Static("", classes="proj-gap"))
            out.extend(self._project_rows(project))
        return out

    def _project_rows(self, project: ProjectEntry) -> list[Static | Horizontal]:
        opened = self.is_open(project.path)
        folder = _FolderLabel(project, opened)
        self._folders[self._pid(project.path)] = folder
        rows: list[Static | Horizontal] = [
            Horizontal(
                folder,
                _Hit("[$text-muted]+[/] new agent", "new", project.path, classes="proj-new"),
                classes="proj-row",
            )
        ]
        if not opened:
            return rows
        sessions = project.sessions
        if not sessions:
            rows.append(Static("no session yet", classes="proj-empty"))
            return rows
        shown = min(self.shown_count(project.path), len(sessions))
        for entry in sessions[:shown]:
            current = same_path(project.path, self._current_path) and entry.key == self._current_key
            label = _SessionLabel(project.path, entry.key, entry.title, entry.pinned, current)
            self._labels[(self._pid(project.path), entry.key)] = label
            rows.append(
                Horizontal(
                    label,
                    _Hit(TERMINAL_MARK, "terminal", project.path, entry.key, classes="proj-menu proj-terminal", tooltip=TERMINAL_TIP),
                    _Hit("⋯", "menu", project.path, entry.key, classes="proj-menu"),
                    classes="proj-row proj-session",
                )
            )
        hidden = len(sessions) - shown
        if hidden > 0:
            rows.append(_Hit(f"{_SESSION_INDENT}[$text-muted]⌄[/] more ({hidden})", "more", project.path, classes="proj-step"))
        elif len(sessions) > PAGE_SIZE:
            rows.append(_Hit(f"{_SESSION_INDENT}[$text-muted]⌃[/] less", "less", project.path, classes="proj-step"))
        return rows

    def _paint_labels(self) -> None:
        for (pid, key), label in self._labels.items():
            working = key in self._busy.get(pid, ())
            label.paint(self._spin if working else None)
        for pid, folder in self._folders.items():
            folder.paint(self._spin if self._busy.get(pid) else None)
