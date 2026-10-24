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


def test_alt_enter_and_alt_s_reach_the_running_turn_instead_of_the_queue(tmp_path):
    """Ctrl+Enter is a plain Enter in most terminals: the agent never saw it."""
    async def run():
        for key in ("alt+enter", "alt+s"):
            workspace = tmp_path / key.replace("+", "-")
            workspace.mkdir()
            app = make_app(workspace)
            async with app.run_test(size=(100, 32)) as pilot:
                await app.submit_text("first")
                await app.runtime.bus.consume_inbound()
                # Plain Enter still queues, and the queue says how to send now.
                await app.submit_text("run the linter after")
                await pilot.pause()
                assert "Alt+Enter sends to the running task" in str(
                    app.query_one("#queue-title", Static).content
                )
                app.composer.focus()
                app.composer.set_text("also check the logs")
                await pilot.press(key)
                await pilot.pause()
                sent = await asyncio.wait_for(app.runtime.bus.consume_inbound(), 2)
                assert sent.content == "also check the logs"
                assert [row.text for row in app._queued_prompts[app.runtime.session_key]] == [
                    "run the linter after"
                ]
                assert app.composer.text == ""
                assert "also check the logs" in [r.raw_text for r in app.query(UserMessage)]

    asyncio.run(run())


def test_agents_command_opens_the_inspector_like_f8(tmp_path):
    async def run():
        from navin.tui.agents import AgentsScreen

        app = make_app(tmp_path)
        async with app.run_test(size=(100, 32)) as pilot:
            await app.submit_text("/agents")
            await pilot.pause()
            assert isinstance(app.screen, AgentsScreen)
            assert app.runtime.bus.inbound_size == 0

    asyncio.run(run())


def test_send_now_during_active_turn_publishes_and_keeps_message(tmp_path):
    async def run():
        app = make_app(tmp_path)
        async with app.run_test(size=(100, 32)) as pilot:
            await app.submit_text("first")
            await app.runtime.bus.consume_inbound()
            # A prompt queued while the first turn is still running.
            app.prefs.mode = "agent"
            await app.submit_text("urgent follow-up")
            app.prefs.mode = "chat"
            await pilot.pause()
            assert len(app.query(QueuedPromptRow)) == 1
            row = app.query(QueuedPromptRow).first()
            await pilot.click(row.query_one(".queue-send"))
            await pilot.pause()
            # The agent must receive the prompt as a follow-up of the running turn.
            sent = await asyncio.wait_for(app.runtime.bus.consume_inbound(), 2)
            assert sent.content == "urgent follow-up"
            # And the message must stay visible in the transcript.
            await pilot.pause()
            texts = [r.raw_text for r in app.query(UserMessage)]
            assert "urgent follow-up" in texts, texts
            # The turn ends and the reply arrives: the follow-up must still be
            # visible (it must not be swallowed by the turn-end repaint).
            app.runtime._finish_turn({})
            await app.runtime._dispatch(OutboundMessage("cli", "direct", "Done, follow-up handled."))
            await pilot.pause()
            texts = [r.raw_text for r in app.query(UserMessage)]
            assert texts == ["first", "urgent follow-up"], texts
            assert [r.text for r in app.query(AssistantMessage)] == ["Done, follow-up handled."]
    asyncio.run(run())


def test_edit_loads_queued_message_into_composer_and_keeps_draft(tmp_path):
    async def run():
        app = make_app(tmp_path)
        async with app.run_test(size=(100, 32)) as pilot:
            await app.submit_text("first")
            await app.runtime.bus.consume_inbound()
            await app.submit_text("queued one")
            await app.submit_text("queued two")
            await pilot.pause()
            app.composer.set_text("current draft")
            row = next(
                r for r in app.query(QueuedPromptRow) if "queued one" in str(r.query_one(Static).render())
            )
            await pilot.click(row.query_one(".queue-edit"))
            await pilot.pause()
            # The queued text is now in the composer, the previous draft went
            # back to the queue, and the edited row is gone.
            assert app.composer.text == "queued one"
            rows = app.query(QueuedPromptRow)
            texts = [str(r.query_one(Static).render()) for r in rows]
            assert len(texts) == 2
            assert any("current draft" in t for t in texts)
            assert not any("queued one" in t for t in texts)
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
            await pilot.click(app.query(QueuedPromptRow).first().query_one(".queue-remove"))
            # Removal is handled from the posted message: let it run before
            # counting (counting right after the click raced the handler).
            await pilot.pause()
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


def test_queued_message_is_never_shown_twice_while_it_is_sent(tmp_path):
    # Regression: a message sent while the reply was finishing went to the
    # queue, then stayed in the queue row and in the chat for the whole send.
    async def run():
        app = make_app(tmp_path)
        async with app.run_test(size=(90, 30)) as pilot:
            await app.submit_text("first")
            await app.runtime.bus.consume_inbound()
            await app.submit_text("second")
            await pilot.pause()
            assert len(app.query(QueuedPromptRow)) == 1
            seen: list[tuple[int, list[str]]] = []
            real_send = app.runtime.send

            async def slow_send(inbound, **kwargs):
                await asyncio.sleep(0.05)
                seen.append((
                    len(app.query(QueuedPromptRow)),
                    [row.raw_text for row in app.query(UserMessage)],
                ))
                return await real_send(inbound, **kwargs)

            with patch.object(app.runtime, "send", new=slow_send):
                await app.runtime._dispatch(OutboundMessage("cli", "direct", "First answer"))
                assert (await asyncio.wait_for(app.runtime.bus.consume_inbound(), 2)).content == "second"
                await pilot.pause()
            assert seen == [(0, ["first", "second"])]
            assert not app.query_one(PromptQueue).display
    asyncio.run(run())


def test_failed_queued_send_keeps_message_and_draft_for_retry(tmp_path):
    async def run():
        app = make_app(tmp_path)
        async with app.run_test(size=(90, 30)) as pilot:
            await app.submit_text("first")
            await app.runtime.bus.consume_inbound()
            await app.submit_text("retry me")
            app.composer.set_text("my draft")
            resume = app.query_one("#queue-resume", Button)
            with patch.object(app.runtime, "send", new=AsyncMock(side_effect=RuntimeError("offline"))):
                await app.runtime._dispatch(OutboundMessage("cli", "direct", "First answer"))
                # Wait on the state, not a fixed delay: on a busy machine the failed
                # send pauses the queue late, and the resume button is laid out
                # after that, so clicking earlier would miss a hidden button.
                for _ in range(200):
                    if app.runtime.session_key in app._queue_paused and resume.display and resume.region.area:
                        break
                    await pilot.pause(0.05)
            assert app._queued_prompts[app.runtime.session_key][0].text == "retry me"
            assert app.runtime.session_key in app._queue_paused
            assert resume.display and resume.region.area, "the resume button is shown once the queue pauses"
            assert app.composer.text == "my draft"
            assert [row.raw_text for row in app.query(UserMessage)] == ["first"]
            await pilot.click("#queue-resume")
            assert (await asyncio.wait_for(app.runtime.bus.consume_inbound(), 10)).content == "retry me"
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
            for _ in range(50):
                if not app.query_one(PromptQueue).display:
                    break
                await pilot.pause(0.05)
            assert not app.query_one(PromptQueue).display
            assert app.runtime.bus.inbound_size == 0
            assert app._queued_prompts[original][0].text == "for the first session"
            await app.submit_text("new session prompt")
            message = await app.runtime.bus.consume_inbound()
            assert message.chat_id == "other"
            assert message.content == "new session prompt"
    asyncio.run(run())


def test_queue_sends_after_turn_end_without_any_assistant_message(tmp_path):
    """A turn that ends silently (stop, error, empty answer) must not leave
    the queue blocked forever: after a short grace period the next queued
    prompt is sent automatically."""
    async def run():
        app = make_app(tmp_path)
        app.AWAITING_REPLY_GRACE_S = 0.1
        async with app.run_test(size=(100, 32)) as pilot:
            await app.submit_text("first")
            await app.runtime.bus.consume_inbound()
            app.prefs.mode = "agent"
            await app.submit_text("follow-up")
            app.prefs.mode = "chat"
            await pilot.pause()
            assert len(app.query(QueuedPromptRow)) == 1
            assert app._awaiting_reply

            # Turn ends without any assistant message ever arriving.
            app.runtime._finish_turn({})
            await pilot.pause()
            # Skip the empty turn-end frame if the runtime emits one; the
            # queued prompt must arrive on its own after the grace period.
            sent = None
            for _ in range(30):
                try:
                    message = await asyncio.wait_for(
                        app.runtime.bus.consume_inbound(), 0.5
                    )
                except asyncio.TimeoutError:
                    continue
                if message.content:
                    sent = message
                    break
            if sent is None:
                print("DBG awaiting:", app._awaiting_reply, "ready:", app._queue_ready(),
                      "outbound:", getattr(app.runtime.bus, "outbound_size", None),
                      "turn_active:", app.runtime.turn_active,
                      "tasks:", [t.done() for t in getattr(app.runtime.agent_loop, "_active_tasks", {}).get(app.runtime.session_key, [])],
                      "current:", app._current, "queue_sending:", app._queue_sending)
            assert sent is not None
            assert sent.content == "/forge follow-up"
            # The queue state machine finishes removing the row right after
            # publishing; give the event loop the remaining pumps.
            for _ in range(50):
                if not app.query(QueuedPromptRow):
                    break
                await pilot.pause(0.05)
            assert not app.query(QueuedPromptRow)
    asyncio.run(run())


@pytest.mark.parametrize("width", [48, 100])
@pytest.mark.parametrize("effort", ["", "high"])
def test_model_and_effort_share_the_footer_line_without_context_or_permission(tmp_path, width, effort):
    async def run():
        app = make_app(tmp_path)
        async with app.run_test(size=(width, 25)) as pilot:
            st = app.runtime.status
            st.model = "example-provider/model-with-a-long-name"
            st.provider = "example-provider"
            st.context_used = 46000
            st.context_window = 200000
            with patch.object(app.runtime, "reasoning_details", return_value=(effort, ())):
                app._set_status()
            await pilot.pause()
            meta = app.query_one(ComposerMeta)
            model = app.query_one("#meta-model", Static)
            reasoning = app.query_one("#meta-reasoning", Static)
            assert not app.query("#meta-context") and not app.query("#meta-permission")
            assert str(model.content) == "model-with-a-long-name"
            assert str(reasoning.content) == (effort or "Auto")
            dock = app.query_one("#dock")
            assert meta.parent is dock
            assert model.region.y == reasoning.region.y == dock.region.y
            assert reasoning.region.x >= model.region.right
            assert app.query_one("#composer-block").styles.padding.top == 1
            assert app.transcript.styles.padding.bottom == 0
            assert app.screen.styles.background.hex == "#181A1D"
    asyncio.run(run())
