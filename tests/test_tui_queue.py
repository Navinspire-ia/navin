# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from textual.widgets import Button, Static

from navin.bus.events import OutboundMessage
from navin.tui.prefs import TuiPrefs
from navin.tui.widgets import (
    AssistantMessage,
    ComposerMeta,
    PromptQueue,
    QueuedPromptRow,
    UserMessage,
)
from tests.test_tui_working import _InteractionApp


def make_app(tmp_path):
    prefs = TuiPrefs(sidebar=False, mode="chat", mode_explicit=True)
    prefs.save = lambda: None
    app = _InteractionApp(SimpleNamespace(workspace_path=Path(tmp_path)), prefs=prefs)
    app.theme = "navin"
    return app


def test_queue_waits_for_final_reply_and_sends_fifo_without_touching_draft(tmp_path):
    async def run():
        app = make_app(tmp_path)
        async with app.run_test(size=(100, 32)) as pilot:
            await app.submit_text("first")
            assert (await app.runtime.bus.consume_inbound()).content == "first"
            app.prefs.mode = "agent"
            await app.submit_text("second")
            app.prefs.mode = "chat"
            await app.submit_text("third")
            app.composer.set_text("unsent draft")
            await pilot.pause()
            assert app.runtime.bus.inbound_size == 0
            assert len(app.query(QueuedPromptRow)) == 2
            assert [row.raw_text for row in app.query(UserMessage)] == ["first"]
            # A turn-end notification can overtake the final answer.
            app.runtime._finish_turn({})
            await pilot.pause()
            assert app.runtime.bus.inbound_size == 0
            await app.runtime._dispatch(OutboundMessage("cli", "direct", "First answer"))
            second = await asyncio.wait_for(app.runtime.bus.consume_inbound(), 2)
            await pilot.pause()
            assert second.content == "/forge second"
            assert app.composer.text == "unsent draft"
            assert len(app.query(QueuedPromptRow)) == 1
            await app.runtime._dispatch(OutboundMessage("cli", "direct", "Second answer"))
            third = await asyncio.wait_for(app.runtime.bus.consume_inbound(), 2)
            assert third.content == "third"
            await app.runtime._dispatch(OutboundMessage("cli", "direct", "Third answer"))
            await pilot.pause()
            assert not app.query_one(PromptQueue).display
            assert [row.raw_text for row in app.query(UserMessage)] == ["first", "second", "third"]
            assert [row.text for row in app.query(AssistantMessage)] == ["First answer", "Second answer", "Third answer"]
            last = app.query(AssistantMessage).last()
            assert app.query_one("#composer-block").region.y - last.region.bottom <= 1
    asyncio.run(run())


def test_stop_pauses_queue_and_remove_resume_controls_work(tmp_path):
    async def run():
        app = make_app(tmp_path)
        async with app.run_test(size=(90, 30)) as pilot:
            await app.submit_text("first")
            await app.runtime.bus.consume_inbound()
            await app.submit_text("remove this")
            await app.submit_text("keep this")
            await pilot.pause()
            await pilot.click(app.query(QueuedPromptRow).first().query_one(Button))
            assert len(app.query(QueuedPromptRow)) == 1
            await pilot.press("escape")
            assert (await app.runtime.bus.consume_inbound()).content == "/stop"
            await app.runtime._dispatch(OutboundMessage("cli", "direct", "Stopped."))
            await pilot.pause()
            assert app.runtime.bus.inbound_size == 0
            assert "paused" in str(app.query_one("#queue-title", Static).content)
            await pilot.click("#queue-resume")
            assert (await asyncio.wait_for(app.runtime.bus.consume_inbound(), 2)).content == "keep this"
    asyncio.run(run())


def test_failed_queued_send_keeps_message_and_draft_for_retry(tmp_path):
    async def run():
        app = make_app(tmp_path)
        async with app.run_test(size=(90, 30)) as pilot:
            await app.submit_text("first")
            await app.runtime.bus.consume_inbound()
            await app.submit_text("retry me")
            app.composer.set_text("my draft")
            with patch.object(app.runtime, "send", new=AsyncMock(side_effect=RuntimeError("offline"))):
                await app.runtime._dispatch(OutboundMessage("cli", "direct", "First answer"))
                await pilot.pause(0.4)
            assert app._queued_prompts[app.runtime.session_key][0].text == "retry me"
            assert app.runtime.session_key in app._queue_paused
            assert app.composer.text == "my draft"
            assert [row.raw_text for row in app.query(UserMessage)] == ["first"]
            await pilot.click("#queue-resume")
            assert (await asyncio.wait_for(app.runtime.bus.consume_inbound(), 2)).content == "retry me"
    asyncio.run(run())


def test_queued_prompts_stay_in_their_session(tmp_path):
    async def run():
        app = make_app(tmp_path)
        async with app.run_test(size=(90, 30)) as pilot:
            await app.submit_text("first")
            await app.runtime.bus.consume_inbound()
            original = app.runtime.session_key
            await app.submit_text("for the first session")
            await app.runtime.switch_session("cli:other")
            await pilot.pause()
            assert not app.query_one(PromptQueue).display
            assert app.runtime.bus.inbound_size == 0
            assert app._queued_prompts[original][0].text == "for the first session"
            await app.submit_text("new session prompt")
            message = await app.runtime.bus.consume_inbound()
            assert message.chat_id == "other"
            assert message.content == "new session prompt"
    asyncio.run(run())


@pytest.mark.parametrize("width", [48, 100])
def test_context_percentage_stays_at_right_of_model_provider_line(tmp_path, width):
    async def run():
        app = make_app(tmp_path)
        async with app.run_test(size=(width, 25)) as pilot:
            st = app.runtime.status
            st.model = "model-with-a-long-name"
            st.provider = "example-provider"
            st.context_used = 46000
            st.context_window = 200000
            app._set_status()
            await pilot.pause()
            meta = app.query_one(ComposerMeta)
            context = app.query_one("#meta-context", Static)
            model = app.query_one("#meta-model", Static)
            assert str(context.content) == "Context 23%"
            assert "example-provider" in str(model.content)
            assert context.region.right == meta.content_region.right
            assert context.region.y == model.region.y
            assert context.region.x >= model.region.right
            assert app.query_one("#composer-block").styles.padding.top == 1
            assert app.transcript.styles.padding.bottom == 0
            assert app.screen.styles.background.hex == "#181A1D"
    asyncio.run(run())
