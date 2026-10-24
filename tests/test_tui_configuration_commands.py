# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

import asyncio

import pytest

from navin.tui.app import _TUI_SLASH
from navin.tui.settings import SettingsHub
from navin.tui.widgets import QueuedPromptRow
from tests.test_tui_prompt_cleanup import approval_request, start_work
from tests.test_tui_queue import make_app


@pytest.mark.parametrize("text,section", [
    ("/settings", "providers"), ("/models", "models"),
    ("/model", "models"), ("/settings\tmodels", "models"),
    ("/providers", "providers"), ("/mcp", "mcp"),
])
def test_enter_opens_the_configuration_screen_without_sending_a_prompt(tmp_path, text, section):
    async def run():
        app = make_app(tmp_path)
        async with app.run_test(size=(110, 36)) as pilot:
            app.slash_rows = [*map(dict, _TUI_SLASH), *app.runtime.slash_commands()]
            app.prefs.mode = "agent"
            app.composer.set_text(text)
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            assert isinstance(app.screen, SettingsHub)
            assert app.screen._section.id == section
            assert app.runtime.bus.inbound_size == 0
            assert not app.runtime.turn_active
    asyncio.run(run())


def test_permissions_command_bypasses_a_pending_approval_and_prompt_queue(tmp_path):
    async def run():
        app = make_app(tmp_path)
        async with app.run_test(size=(100, 36)) as pilot:
            await start_work(app)
            current = app._current
            await app.submit_text("next task")
            await app._on_runtime_event(approval_request())
            app.composer.set_text("/permission auto")
            await pilot.pause()
            await pilot.press("enter")
            sent = await asyncio.wait_for(app.runtime.bus.consume_inbound(), 2)
            assert sent.content == "/permission auto"
            assert app.runtime.turn_active
            assert app._current is current
            assert len(app.query(QueuedPromptRow)) == 1
            assert app.runtime.bus.inbound_size == 0
    asyncio.run(run())


def test_opening_settings_keeps_the_running_turn_and_queued_prompt(tmp_path):
    async def run():
        app = make_app(tmp_path)
        async with app.run_test(size=(110, 36)) as pilot:
            await start_work(app)
            current = app._current
            await app.submit_text("next task")
            await app.submit_text("/models")
            await pilot.pause()
            assert isinstance(app.screen, SettingsHub)
            assert app.runtime.turn_active
            assert app._current is current
            assert app.runtime.bus.inbound_size == 0
            assert len(app._queued_prompts[app.runtime.session_key]) == 1
    asyncio.run(run())


def test_settings_stay_available_when_engine_startup_has_failed(tmp_path):
    async def run():
        app = make_app(tmp_path)
        async with app.run_test(size=(110, 36)) as pilot:
            app._engine_ready = False
            app._engine_error = "No configured provider"
            await app.submit_text("/settings models")
            await pilot.pause()
            assert isinstance(app.screen, SettingsHub)
            assert app.screen._section.id == "models"
            assert app.runtime.bus.inbound_size == 0
    asyncio.run(run())


def test_bare_permission_opens_a_picker_that_applies_the_choice(tmp_path, monkeypatch):
    from navin.tui.screens import PickerScreen
    from navin.webui import exec_policy_api

    monkeypatch.setattr(exec_policy_api, "exec_policy_payload", lambda: {"approval_mode": "always"})

    async def run():
        app = make_app(tmp_path)
        async with app.run_test(size=(100, 36)) as pilot:
            await app.submit_text("/permission")
            for _ in range(20):
                await pilot.pause(0.05)
                if isinstance(app.screen, PickerScreen):
                    break
            assert isinstance(app.screen, PickerScreen)
            assert app.runtime.bus.inbound_size == 0, "opening the picker sends nothing"
            options = app.screen.query_one("#options")
            titles = [str(options.get_option_at_index(i).prompt) for i in range(options.option_count)]
            assert any("every command" in t and "current" in t for t in titles), titles
            await pilot.press("down", "enter")
            sent = await asyncio.wait_for(app.runtime.bus.consume_inbound(), 2)
            assert sent.content.startswith("/permission ")
            assert not isinstance(app.screen, PickerScreen)
    asyncio.run(run())


def test_permission_picker_cancelled_or_unreadable_changes_nothing(tmp_path, monkeypatch):
    from navin.tui.screens import PickerScreen
    from navin.webui import exec_policy_api

    def unreadable():
        raise OSError("config locked")

    monkeypatch.setattr(exec_policy_api, "exec_policy_payload", unreadable)

    async def run():
        app = make_app(tmp_path)
        async with app.run_test(size=(100, 36)) as pilot:
            await app.submit_text("/permission")
            for _ in range(20):
                await pilot.pause(0.05)
                if isinstance(app.screen, PickerScreen):
                    break
            # An unreadable setting still offers the three choices, none marked current.
            assert isinstance(app.screen, PickerScreen)
            options = app.screen.query_one("#options")
            titles = [str(options.get_option_at_index(i).prompt) for i in range(options.option_count)]
            assert len(titles) == 3 and not any("current" in t for t in titles), titles
            await pilot.press("escape")
            await pilot.pause()
            assert not isinstance(app.screen, PickerScreen)
            assert app.runtime.bus.inbound_size == 0, "a cancelled picker sends nothing"
            # With an argument the command still goes straight to the engine.
            await app.submit_text("/permission always")
            sent = await asyncio.wait_for(app.runtime.bus.consume_inbound(), 2)
            assert sent.content == "/permission always"
            assert not isinstance(app.screen, PickerScreen)
    asyncio.run(run())
