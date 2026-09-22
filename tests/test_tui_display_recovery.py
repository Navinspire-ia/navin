# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Display failures and auxiliary messages must not cover continuing work."""

import asyncio
from unittest.mock import patch

from navin.tui.runtime import UiDisplayError, UiProgress, UiRetryWait, UiStreamDelta, UiToolEvent
from navin.tui.widgets import SystemNote, ToolCall, UpdateOffer
from tests.test_tui_queue import make_app


def test_display_failure_burst_is_transient_and_keeps_the_turn_and_queue(tmp_path):
    async def run():
        app = make_app(tmp_path)
        async with app.run_test(size=(100, 32)) as pilot:
            await app.submit_text("keep working")
            await app.runtime.bus.consume_inbound()
            await app.submit_text("next task")
            app.composer.set_text("my draft")
            render = app._render_runtime_event

            async def fail_tool(event):
                if isinstance(event, UiToolEvent):
                    raise ValueError("broken event for this test")
                await render(event)

            with patch.object(app, "_render_runtime_event", fail_tool):
                for _ in range(8):
                    await app.runtime._emit(UiToolEvent("broken", "exec", "output"))
            await pilot.pause()
            assert len(app.query(SystemNote)) == 1
            assert app.runtime.turn_active
            assert app._awaiting_reply
            assert app.runtime.session_key not in app._queue_paused
            assert app._queued_prompts[app.runtime.session_key][0].text == "next task"
            assert app.composer.text == "my draft"
            await app.runtime._emit(UiStreamDelta("Recovered and still working."))
            await pilot.pause()
            assert not app.query(SystemNote)
            assert app._display_error_note is None
            assert not app.runtime._display_error_reported
            assert app.transcript.children[-1] is app._current
            assert "Recovered" in app._current.text
    asyncio.run(run())


def test_display_notice_expires_even_without_further_events(tmp_path):
    async def run():
        app = make_app(tmp_path)
        async with app.run_test(size=(100, 32)) as pilot:
            timer = app.set_timer
            with patch.object(app, "set_timer", side_effect=lambda seconds, callback: timer(0.03, callback)):
                await app._on_runtime_event(UiDisplayError("Display update delayed."))
            await pilot.pause(0.1)
            assert not app.query(SystemNote)
            assert app._display_error_note is None
    asyncio.run(run())


def test_notes_and_update_offers_do_not_pin_new_tool_output_above_them(tmp_path):
    async def run():
        app = make_app(tmp_path)
        async with app.run_test(size=(100, 32)) as pilot:
            await app.submit_text("run the checks")
            await app.runtime.bus.consume_inbound()
            await app._on_runtime_event(UiToolEvent("run", "exec", "start", {"command": "checks"}))
            for note in (SystemNote("Intermediate information"), UpdateOffer("9.0.0", "New release")):
                await app.transcript.add(note)
                await app._on_runtime_event(UiToolEvent("run", "exec", "output", output="more output\n"))
                await pilot.pause()
                order = list(app.transcript.children)
                assert order.index(note) < order.index(app._current)
                assert app.transcript.children[-1] is app._current
                assert len(app.query(ToolCall)) == 1
                assert app.query_one(ToolCall).region.y >= note.region.bottom
    asyncio.run(run())


def test_repeated_retry_status_uses_one_note_and_progress_continues_below_it(tmp_path):
    async def run():
        app = make_app(tmp_path)
        async with app.run_test(size=(100, 32)) as pilot:
            for delay in (10, 5, 1):
                await app._on_runtime_event(UiRetryWait(f"Retrying in {delay}s"))
            assert len(app.query(SystemNote)) == 1
            await app._on_runtime_event(UiProgress("The next operation is running."))
            await pilot.pause()
            assert app.transcript.children[-1] is app._current
    asyncio.run(run())
