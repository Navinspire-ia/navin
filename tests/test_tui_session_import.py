"""Exercise CLI import discovery, selection, persistence and navigation."""

import asyncio
from pathlib import Path
from threading import Event
from unittest.mock import Mock

from textual.widgets import Input, SelectionList, Static

from navin.session import import_sessions
from navin.session.import_sessions import ExternalSession, SourceReport
from navin.session.manager import SessionManager
from navin.tui.screens import PickerScreen
from navin.tui.session_import import SOURCES, SessionImportScreen
from tests.test_tui_models import ChatHost, Host, model_config
from tests.test_tui_queue import make_app


def reports():
    return [SourceReport(name=name, label=label, root=Path("/tmp"), status="ready", sessions=[
        ExternalSession(source=name, session_id="one", title=f"Imported {label}",
                        messages=[{"role": "user", "content": "hello"}], origin_path=Path("/tmp/chat.jsonl")),
    ]) for name, label in SOURCES]


async def ready(pilot, screen):
    for _ in range(100):
        await pilot.pause()
        if not screen._busy:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("Import screen stayed busy")


def test_import_preview_preserves_selection_and_does_not_rescan(tmp_path, monkeypatch):
    discovery = Mock(return_value=reports())
    monkeypatch.setattr(import_sessions, "discover", discovery)
    monkeypatch.setattr(import_sessions, "_EXTRA_ROOTS_FILE", tmp_path / "roots.json")

    async def run():
        screen = SessionImportScreen(tmp_path)
        app = Host(screen)
        async with app.run_test(size=(110, 38)) as pilot:
            await ready(pilot, screen)
            choices = screen.query_one("#sources", SelectionList)
            assert len(choices.selected) == 5
            choices.deselect("cursor")
            await pilot.click("#import")
            await ready(pilot, screen)
            assert len(SessionManager(tmp_path).list_sessions()) == 4
            assert "4 chats imported" in str(screen.query_one("#status", Static).render())
            await pilot.pause(0.3)
            await pilot.click("#import")
            await ready(pilot, screen)
            assert "4 already present" in str(screen.query_one("#status", Static).render())
            assert len(SessionManager(tmp_path).list_sessions()) == 4
            discovery.assert_called_once()
            app.save_screenshot(str(tmp_path / "cli-session-import.svg"))
            await pilot.click("#close")
            assert app.result is True
    asyncio.run(run())


def test_slow_scan_can_be_closed_without_freezing_the_cli(tmp_path, monkeypatch):
    started, finish = Event(), Event()
    def discover(names):
        started.set()
        finish.wait(5)
        return reports()
    monkeypatch.setattr(import_sessions, "discover", discover)
    async def run():
        screen = SessionImportScreen(tmp_path)
        app = Host(screen)
        try:
            async with app.run_test(size=(100, 36)) as pilot:
                assert await asyncio.to_thread(started.wait, 2)
                await pilot.press("escape")
                assert app.screen is not screen
        finally:
            finish.set()
    asyncio.run(run())


def test_custom_folder_can_be_added_and_removed(tmp_path, monkeypatch):
    monkeypatch.setattr(import_sessions, "discover", lambda names: reports())
    monkeypatch.setattr(import_sessions, "_EXTRA_ROOTS_FILE", tmp_path / "roots.json")
    folder = tmp_path / "external"
    folder.mkdir()
    async def run():
        screen = SessionImportScreen(tmp_path)
        app = Host(screen)
        async with app.run_test(size=(110, 38)) as pilot:
            await ready(pilot, screen)
            await pilot.click("#add-root")
            app.screen.query_one("#f-path", Input).value = str(folder)
            await pilot.click("#ok")
            await ready(pilot, screen)
            assert import_sessions.load_extra_roots() == {"codex": [str(folder)]}
            await pilot.click("#remove-root")
            assert isinstance(app.screen, PickerScreen)
            await pilot.press("enter")
            await ready(pilot, screen)
            assert import_sessions.load_extra_roots() == {}
    asyncio.run(run())


def test_import_command_is_local_and_works_without_an_engine(tmp_path, monkeypatch):
    monkeypatch.setattr(import_sessions, "discover", lambda names: [])
    monkeypatch.setattr(import_sessions, "_EXTRA_ROOTS_FILE", tmp_path / "roots.json")
    async def run():
        app = make_app(tmp_path)
        async with app.run_test(size=(110, 38)) as pilot:
            app._engine_ready = False
            await app.submit_text("/import")
            await pilot.pause()
            assert isinstance(app.screen, SessionImportScreen)
            assert app.runtime.bus.inbound_size == 0
    asyncio.run(run())


def test_sessions_menu_returns_with_imported_chats(tmp_path, monkeypatch):
    monkeypatch.setattr(import_sessions, "discover", lambda names: reports())
    monkeypatch.setattr(import_sessions, "_EXTRA_ROOTS_FILE", tmp_path / "roots.json")
    async def run():
        app = ChatHost(model_config(tmp_path))
        async with app.run_test(size=(110, 38)) as pilot:
            app.action_pick_session()
            await pilot.pause()
            assert isinstance(app.screen, PickerScreen)
            await pilot.press(*"Import chats", "enter")
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, SessionImportScreen)
            await ready(pilot, screen)
            await pilot.click("#import")
            await ready(pilot, screen)
            await pilot.click("#close")
            await pilot.pause()
            assert isinstance(app.screen, PickerScreen)
            assert len([item for item in app.screen._items if item.title.startswith("Imported ")]) == 5
    asyncio.run(run())
