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
