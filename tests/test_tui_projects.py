# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""CLI sidebar: projects with their latest sessions, like the desktop panel."""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from navin.tui import projects
from navin.tui.projects import ProjectEntry, SessionEntry


def _write_session(root: Path, key: str, title: str, updated: str) -> None:
    folder = root / ".navin" / "sessions"
    folder.mkdir(parents=True, exist_ok=True)
    name = key.replace(":", "_")
    lines = [
        {"_type": "metadata", "key": key, "created_at": updated, "updated_at": updated,
         "metadata": {"title": title}},
        {"role": "user", "content": title},
    ]
    (folder / f"{name}.jsonl").write_text("\n".join(json.dumps(x) for x in lines) + "\n", encoding="utf-8")


class ProjectDataTests(unittest.TestCase):
    def test_folder_color_is_stable_and_in_the_palette(self) -> None:
        first = projects.folder_color("/home/a/app")
        self.assertEqual(first, projects.folder_color("/home/a/app/"))
        self.assertIn(first, projects.FOLDER_COLORS)
        colors = {projects.folder_color(f"/p/{i}") for i in range(40)}
        self.assertGreater(len(colors), 3)

    def test_more_shows_three_at_a_time_never_past_the_total(self) -> None:
        self.assertEqual(projects.next_visible(3, 20), 6)
        self.assertEqual(projects.next_visible(6, 20), 9)
        self.assertEqual(projects.next_visible(9, 10), 10)
        self.assertEqual(projects.next_visible(0, 2), 2)

    def test_resolve_project_dir(self) -> None:
        with TemporaryDirectory() as tmp:
            self.assertEqual(projects.resolve_project_dir(f' "{tmp}" '), Path(tmp).resolve())
            with self.assertRaises(ValueError):
                projects.resolve_project_dir("")
            missing = Path(tmp) / "new" / "app"
            with self.assertRaises(ValueError):
                projects.resolve_project_dir(str(missing))
            self.assertTrue(projects.resolve_project_dir(str(missing), create=True).is_dir())

    def test_sessions_pinned_first_then_latest_and_empty_ones_skipped(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_session(root, "cli:1", "old chat", "2026-01-01T10:00:00")
            _write_session(root, "cli:2", "new chat", "2026-03-01T10:00:00")
            _write_session(root, "cli:3", "pinned chat", "2026-02-01T10:00:00")
            (root / ".navin" / "sessions" / "cli_4.jsonl").write_text(
                json.dumps({"_type": "metadata", "key": "cli:4", "metadata": {}}) + "\n", encoding="utf-8")
            state = {"pinned_keys": ["cli:3"], "title_overrides": {"cli:2": "Renamed"}}
            rows = projects.list_project_sessions(str(root), state)
            self.assertEqual([r.key for r in rows], ["cli:3", "cli:2", "cli:1"])
            self.assertTrue(rows[0].pinned)
            self.assertEqual(rows[1].title, "Renamed")

    def test_listing_a_folder_without_sessions_creates_nothing(self) -> None:
        with TemporaryDirectory() as tmp:
            self.assertEqual(projects.list_project_sessions(tmp, {}), [])
            self.assertFalse((Path(tmp) / ".navin").exists())

    def test_a_desktop_chat_opened_in_another_project_is_not_listed_here(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "financeIa-v2"
            root.mkdir()
            _write_session(root, "cli:1", "cli chat", "2026-03-01T10:00:00")
            for key, owner in (("websocket:mine", str(root)), ("websocket:sub", str(root / "test")),
                               ("websocket:other", "/home/u/bbac")):
                _write_session(root, key, f"chat {key}", "2026-02-01T10:00:00")
                path = root / ".navin" / "sessions" / f"{key.replace(':', '_')}.jsonl"
                lines = path.read_text(encoding="utf-8").splitlines()
                head = json.loads(lines[0])
                head["metadata"]["workspace_scope"] = {"project_path": owner, "access_mode": "full"}
                path.write_text("\n".join([json.dumps(head), *lines[1:]]) + "\n", encoding="utf-8")
            rows = projects.list_project_sessions(str(root), {})
            self.assertEqual([r.key for r in rows], ["cli:1", "websocket:mine"])

    def test_scoped_elsewhere_reads_the_project_a_chat_was_opened_in(self) -> None:
        with TemporaryDirectory() as tmp:
            real = Path(tmp) / "real"
            real.mkdir()
            link = Path(tmp) / "link"
            link.symlink_to(real)
            # The same folder written two ways is the same project.
            self.assertFalse(projects.scoped_elsewhere({"workspace_scope": {"project_path": str(link)}}, real))
            self.assertTrue(projects.scoped_elsewhere({"workspace_scope": {"project_path": f"{real}/sub"}}, real))
        # No project recorded (every CLI chat): it belongs to its folder.
        for row in ({}, {"workspace_scope": None}, {"workspace_scope": {"project_path": ""}}):
            self.assertFalse(projects.scoped_elsewhere(row, "/p"))

    def test_engine_sessions_are_internal(self) -> None:
        for key in ("heartbeat", "dream:20261028-204804", "cron:abc", "system:x"):
            self.assertTrue(projects.is_internal_key(key), key)
        for key in ("cli:1", "websocket:abc", "sdk:x", "dreamer:1", ""):
            self.assertFalse(projects.is_internal_key(key), key)

    def test_known_projects_keep_desktop_order_and_include_the_current_one(self) -> None:
        state = {"recent_projects": [{"path": "/a", "name": ""}, {"path": "/b", "name": ""}, {"path": "/a/", "name": ""}]}
        self.assertEqual(projects.known_project_paths("/c", state), ["/c", "/a", "/b"])
        self.assertEqual(projects.known_project_paths("/B", state), ["/a", "/b"])

    def test_pin_toggles_and_remember_puts_a_project_first(self) -> None:
        store: dict = {"pinned_keys": [], "recent_projects": [{"path": "/x", "name": ""}]}
        with mock.patch.object(projects, "read_state", lambda: json.loads(json.dumps(store))), \
                mock.patch.object(projects, "_write_state", lambda s: store.update(s)):
            self.assertTrue(projects.toggle_pin("cli:1"))
            self.assertEqual(store["pinned_keys"], ["cli:1"])
            self.assertFalse(projects.toggle_pin("cli:1"))
            self.assertEqual(store["pinned_keys"], [])
            projects.remember_project("/y")
            projects.remember_project("/x")
            self.assertEqual([p["path"] for p in store["recent_projects"]], ["/x", "/y"])

    def test_register_adds_a_folder_once_without_reordering(self) -> None:
        store: dict = {"recent_projects": [{"path": "/a", "name": ""}]}
        with mock.patch.object(projects, "read_state", lambda: json.loads(json.dumps(store))), \
                mock.patch.object(projects, "_write_state", lambda s: store.update(s)):
            self.assertTrue(projects.register_project("/b"))
            self.assertFalse(projects.register_project("/B/"))
            self.assertEqual([p["path"] for p in store["recent_projects"]], ["/a", "/b"])

    def test_folders_that_no_longer_exist_are_not_listed(self) -> None:
        with TemporaryDirectory() as tmp:
            gone = str(Path(tmp) / "gone")
            state = {"recent_projects": [{"path": gone, "name": ""}, {"path": tmp, "name": ""}]}
            with mock.patch.object(projects, "read_state", lambda: state):
                self.assertEqual([p.path for p in projects.load_projects(tmp)], [tmp])

    def test_rename_and_delete_use_the_project_sessions(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_session(root, "cli:9", "first", "2026-01-01T10:00:00")
            with mock.patch.object(projects, "read_state", lambda: {"pinned_keys": ["cli:9"]}), \
                    mock.patch.object(projects, "_write_state", lambda s: None):
                self.assertEqual(projects.rename_session(str(root), "cli:9", "Better name"), "Better name")
                rows = projects.list_project_sessions(str(root), {})
                self.assertEqual(rows[0].title, "Better name")
                self.assertTrue(projects.delete_session(str(root), "cli:9"))
                self.assertEqual(projects.list_project_sessions(str(root), {}), [])

    def test_fork_copies_a_chat_of_any_project_and_refuses_an_empty_one(self) -> None:
        from navin.session.manager import SessionManager

        with TemporaryDirectory() as tmp:
            manager = SessionManager(Path(tmp))
            source = manager.get_or_create("cli:1")
            source.messages += [{"role": "user", "content": "fix the login"}, {"role": "assistant", "content": "done"}]
            manager.save(source)
            manager.save(manager.get_or_create("cli:3"))
            # no live store given: read from the project's own files
            self.assertEqual(projects.fork_session(tmp, "cli:1", "cli:2", "Login fix"), "Fork: Login fix")
            rows = {r.key: r.title for r in projects.list_project_sessions(tmp, {})}
            self.assertIn("cli:2", rows)
            copy = SessionManager(Path(tmp)).get_or_create("cli:2")
            self.assertEqual([m["content"] for m in copy.messages], ["fix the login", "done"])
            with self.assertRaises(ValueError):
                projects.fork_session(tmp, "cli:3", "cli:4")
            self.assertNotIn("cli:4", {r["key"] for r in SessionManager(Path(tmp)).list_sessions()})


def _text(widget) -> str:
    """Markup text of a Static, on any Textual version."""
    for name in ("content", "renderable"):
        if hasattr(widget, name):
            return str(getattr(widget, name))
    return ""


def _projects(count: int) -> list[ProjectEntry]:
    sessions = [SessionEntry(f"cli:{i}", f"chat {i}", f"2026-01-{i + 1:02d}", pinned=(i == 0)) for i in range(count)]
    return [
        ProjectEntry("/work/alpha", "alpha", "#61AFEF", sessions),
        ProjectEntry("/work/beta", "beta", "#98C379", []),
    ]


class ProjectsPanelTests(unittest.IsolatedAsyncioTestCase):
    async def _run(self, body) -> None:
        from textual.app import App

        from navin.tui.projects_panel import ProjectsPanel

        class Host(App):
            def __init__(self) -> None:
                super().__init__()
                self.hits: list[tuple[str, str, str]] = []

            def compose(self):
                yield ProjectsPanel(id="panel")

            def on_projects_panel_hit(self, event) -> None:
                self.hits.append((event.kind, event.path, event.key))

        app = Host()
        async with app.run_test(size=(40, 30)) as pilot:
            panel = app.query_one(ProjectsPanel)
            await body(app, pilot, panel)

    async def _settle(self, pilot) -> None:
        await pilot.pause()
        await pilot.pause()

    def _texts(self, panel) -> list[str]:
        from textual.widgets import Static

        return [_text(w) for w in panel.query(Static)]

    async def test_current_project_opens_with_three_sessions_then_more_adds_three(self) -> None:
        async def body(app, pilot, panel) -> None:
            panel.set_data(_projects(8), "/work/alpha", "cli:1")
            await self._settle(pilot)
            self.assertTrue(panel.is_open("/work/alpha"))
            self.assertEqual(len(panel._labels), 3)
            panel.post_message(panel.Hit("more", "/work/alpha"))
            await self._settle(pilot)
            self.assertEqual(len(panel._labels), 6)
            panel.post_message(panel.Hit("more", "/work/alpha"))
            panel.post_message(panel.Hit("more", "/work/alpha"))
            await self._settle(pilot)
            self.assertEqual(len(panel._labels), 8)
            panel.post_message(panel.Hit("less", "/work/alpha"))
            await self._settle(pilot)
            self.assertEqual(len(panel._labels), 3)

        await self._run(body)

    async def test_session_names_line_up_with_the_folder_name(self) -> None:
        async def body(app, pilot, panel) -> None:
            panel.set_data(_projects(5), "/work/alpha", "cli:1")
            await self._settle(pilot)
            texts = [re.sub(r"\[[^\]]*\]", "", t) for t in self._texts(panel)]
            folder = next(t for t in texts if "alpha" in t and "new agent" not in t)
            name_col = folder.index("alpha")
            for label_widget in panel._labels.values():
                label = re.sub(r"\[[^\]]*\]", "", _text(label_widget))
                title_col = len(label) - len(label.lstrip()) + 2  # mark, then a space
                self.assertEqual(title_col, name_col, label)
            more = next(t for t in texts if "more (" in t)
            self.assertEqual(len(more) - len(more.lstrip()) + 2, name_col, more)

        await self._run(body)

    async def test_clicking_a_project_folds_and_unfolds_its_sessions(self) -> None:
        async def body(app, pilot, panel) -> None:
            panel.set_data(_projects(5), "/work/alpha", "cli:1")
            await self._settle(pilot)
            panel.post_message(panel.Hit("toggle", "/work/alpha"))
            await self._settle(pilot)
            self.assertFalse(panel.is_open("/work/alpha"))
            self.assertEqual(len(panel._labels), 0)
            panel.post_message(panel.Hit("toggle", "/work/alpha"))
            await self._settle(pilot)
            self.assertEqual(len(panel._labels), 3)

        await self._run(body)

    async def test_running_session_gets_a_spinner_and_the_others_do_not(self) -> None:
        from navin.tui.projects_panel import SPINNER

        async def body(app, pilot, panel) -> None:
            panel.set_data(_projects(3), "/work/alpha", "cli:1")
            await self._settle(pilot)
            panel.set_running({"cli:1"})
            running = panel._labels[("/work/alpha", "cli:1")]
            idle = panel._labels[("/work/alpha", "cli:2")]
            self.assertIn(SPINNER[0], _text(running))
            self.assertNotIn(SPINNER[0], _text(idle))
            panel.tick()
            self.assertIn(SPINNER[1], _text(running))
            panel.set_running(set())
            self.assertFalse(any(ch in _text(running) for ch in SPINNER))

        await self._run(body)

    async def test_a_project_working_in_the_background_keeps_its_spinners(self) -> None:
        from navin.tui.projects_panel import SPINNER

        async def body(app, pilot, panel) -> None:
            data = _projects(3)
            data[1] = ProjectEntry("/work/beta", "beta", "#98C379", [SessionEntry("cli:7", "beta chat", "2026-01-09")])
            panel.set_data(data, "/work/alpha", "cli:1")
            panel.post_message(panel.Hit("toggle", "/work/beta"))
            await self._settle(pilot)
            panel.set_busy({"/work/beta": {"cli:7"}, "/work/alpha": {"cli:2"}})
            self.assertIn(SPINNER[0], _text(panel._labels[("/work/beta", "cli:7")]))
            self.assertIn(SPINNER[0], _text(panel._labels[("/work/alpha", "cli:2")]))
            self.assertNotIn(SPINNER[0], _text(panel._labels[("/work/alpha", "cli:1")]))
            # the folder says so too, and still does once folded
            self.assertIn(SPINNER[0], _text(panel._folders["/work/beta"]))
            panel.post_message(panel.Hit("toggle", "/work/beta"))
            await self._settle(pilot)
            self.assertNotIn(("/work/beta", "cli:7"), panel._labels)
            self.assertIn(SPINNER[0], _text(panel._folders["/work/beta"]))
            panel.set_busy({})
            self.assertFalse(any(ch in _text(panel._folders["/work/beta"]) for ch in SPINNER))

        await self._run(body)

    async def test_clicks_reach_the_app_with_project_and_key(self) -> None:
        async def body(app, pilot, panel) -> None:
            panel.set_data(_projects(3), "/work/alpha", "cli:1")
            await self._settle(pilot)
            label = panel._labels[("/work/alpha", "cli:2")]
            await pilot.click(label)
            await self._settle(pilot)
            self.assertIn(("open", "/work/alpha", "cli:2"), app.hits)
            # the fold buttons are handled by the panel and never leak to the app
            panel.post_message(panel.Hit("toggle", "/work/beta"))
            await self._settle(pilot)
            self.assertFalse([h for h in app.hits if h[0] == "toggle"])

        await self._run(body)

    async def test_sessions_are_spaced_from_their_folder_and_from_each_other(self) -> None:
        async def body(app, pilot, panel) -> None:
            panel.set_data(_projects(3), "/work/alpha", "cli:1")
            await self._settle(pilot)
            rows = [label.parent for label in panel._labels.values()]
            self.assertEqual(len(rows), 3)
            self.assertTrue(all(row.styles.margin.top == 1 for row in rows))

        await self._run(body)

    async def test_empty_state_explains_how_to_add_a_project(self) -> None:
        async def body(app, pilot, panel) -> None:
            panel.set_data([], "", "")
            await self._settle(pilot)
            self.assertTrue(any("Add" in t for t in self._texts(panel)))

        await self._run(body)


class ProjectsSidebarFootTests(unittest.IsolatedAsyncioTestCase):
    async def test_foot_shows_the_line_account_and_version_and_no_path(self) -> None:
        from textual.app import App
        from textual.widgets import Static

        from navin.optional_live import live_modules_available
        from navin.tui.widgets import ProjectsSidebar

        class Host(App):
            def compose(self):
                yield ProjectsSidebar(id="projects-side", classes="-visible")

        app = Host()
        async with app.run_test(size=(60, 30)) as pilot:
            side = app.query_one(ProjectsSidebar)
            side.set_version("2.3.7")
            side.set_update_available("2.3.8")
            side.set_account("alice@example.com")
            await pilot.pause()
            foot = side.query_one("#projects-foot")
            self.assertEqual(len(list(foot.query("#projects-split"))), 1)
            version = _text(side.query_one("#projects-version", Static))
            self.assertIn("v2.3.7", version)
            self.assertIn("v2.3.8", version)
            self.assertEqual(list(foot.query("#side-workspace")), [])
            self.assertEqual(len(list(foot.query("#projects-account"))), 1 if live_modules_available() else 0)


if __name__ == "__main__":
    unittest.main()
