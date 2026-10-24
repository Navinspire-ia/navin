# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""CLI Multitask: queued prompts launched as parallel subagents."""

import asyncio

from navin.bus.events import (
    INBOUND_META_RUNTIME_CONTROL,
    RUNTIME_CONTROL_ACK,
    RUNTIME_CONTROL_MULTITASK_SPAWN,
    OutboundMessage,
)
from navin.bus.outbound_events import SubagentProgressEvent, outbound_message_for_event
from navin.tui.widgets import AgentsPanel, QueuedPromptRow, SystemNote, UserMessage
from tests.test_tui_queue import make_app


async def _answer_spawns(app, count, answer):
    """Play the agent loop: ack ``count`` multitask controls, return them."""
    controls = []
    for _ in range(count):
        msg = await asyncio.wait_for(app.runtime.bus.consume_inbound(), 2)
        assert msg.metadata[INBOUND_META_RUNTIME_CONTROL] == RUNTIME_CONTROL_MULTITASK_SPAWN
        controls.append(msg.metadata)
        msg.metadata[RUNTIME_CONTROL_ACK].set_result(answer(msg.metadata))
    return controls


async def _queue_two(app, pilot):
    await app.submit_text("first")
    await app.runtime.bus.consume_inbound()
    await app.submit_text("add a README section")
    await app.submit_text("write the changelog")
    await pilot.pause()
    assert len(app.query(QueuedPromptRow)) == 2


async def _settle(app, pilot):
    """Let the multitask worker finish (it runs off the message loop)."""
    workers = [w for w in app.workers if w.group == "multitask"]
    if workers:
        await app.workers.wait_for_complete(workers)
    await pilot.pause()


def _notes(app):
    return " ".join(str(note.render()) for note in app.query(SystemNote))


def test_f6_runs_queued_prompts_in_parallel_and_cards_show(tmp_path):
    async def run():
        app = make_app(tmp_path)
        async with app.run_test(size=(110, 34)) as pilot:
            await _queue_two(app, pilot)
            answering = asyncio.create_task(
                _answer_spawns(app, 2, lambda meta: {"ok": True, "detail": "started"})
            )
            await pilot.press("f6")
            controls = await asyncio.wait_for(answering, 3)
            await _settle(app, pilot)
            prompts = sorted(meta["prompt"] for meta in controls)
            assert prompts == ["add a README section", "write the changelog"]
            assert all(meta["session_key"] == app.runtime.session_key for meta in controls)
            # Distinct idempotency keys, so neither replays the other.
            assert len({meta["client_key"] for meta in controls}) == 2
            assert all(meta["label"] for meta in controls)
            # Launched prompts leave the queue; nothing is sent as a chat turn.
            assert len(app.query(QueuedPromptRow)) == 0
            assert app.runtime.bus.inbound_size == 0
            assert "Running 2 prompts in parallel" in _notes(app)

            # The runtime's subagent progress lands as a card in the panel.
            await app.runtime._dispatch(outbound_message_for_event(
                channel="cli", chat_id="direct",
                event=SubagentProgressEvent(
                    task_id="ab12", label="add a README section", phase="awaiting_tools",
                    status_line="Editing README.md", model=None, iteration=1, done=False,
                ),
            ))
            await pilot.pause()
            panel = app.query_one("#agents", AgentsPanel)
            assert "ab12" in panel._agents
            assert "add a README section" in str(panel.content)

            # Its result is announced into this same chat as a normal reply.
            app.runtime._finish_turn({})
            await app.runtime._dispatch(OutboundMessage("cli", "direct", "README updated."))
            await pilot.pause()
            assert [row.raw_text for row in app.query(UserMessage)] == ["first"]

    asyncio.run(run())


def test_refused_prompt_stays_queued_with_a_note(tmp_path):
    async def run():
        app = make_app(tmp_path)
        async with app.run_test(size=(110, 34)) as pilot:
            await _queue_two(app, pilot)

            def answer(meta):
                if meta["prompt"] == "write the changelog":
                    return {"ok": False, "error": "too many agents are running"}
                return {"ok": True, "detail": "started"}

            answering = asyncio.create_task(_answer_spawns(app, 2, answer))
            await app.submit_text("/multitask")
            await asyncio.wait_for(answering, 3)
            await _settle(app, pilot)
            assert len(app.query(QueuedPromptRow)) == 1
            assert [item.text for item in app._queued_prompts[app.runtime.session_key]] == [
                "write the changelog"
            ]
            notes = _notes(app)
            assert "Running 1 prompt in parallel" in notes
            assert "could not start in parallel and stay queued" in notes
            assert "too many agents are running" in notes

    asyncio.run(run())


def test_multitask_with_argument_spawns_that_task_directly(tmp_path):
    async def run():
        app = make_app(tmp_path)
        async with app.run_test(size=(110, 34)) as pilot:
            answering = asyncio.create_task(
                _answer_spawns(app, 1, lambda meta: {"ok": True, "detail": "started"})
            )
            await app.submit_text("/multitask audit the auth module")
            (control,) = await asyncio.wait_for(answering, 3)
            await _settle(app, pilot)
            assert control["prompt"] == "audit the auth module"
            assert control["label"] == "audit the auth module"
            assert not app.query(QueuedPromptRow)

    asyncio.run(run())


def test_queue_button_and_empty_queue_note(tmp_path):
    async def run():
        app = make_app(tmp_path)
        async with app.run_test(size=(110, 34)) as pilot:
            await app.submit_text("/multitask")
            await _settle(app, pilot)
            assert "Nothing is queued" in _notes(app)
            assert app.runtime.bus.inbound_size == 0

            await _queue_two(app, pilot)
            answering = asyncio.create_task(
                _answer_spawns(app, 2, lambda meta: {"ok": True, "detail": "started"})
            )
            await pilot.click("#queue-parallel")
            await asyncio.wait_for(answering, 3)
            await _settle(app, pilot)
            assert not app.query(QueuedPromptRow)

    asyncio.run(run())
