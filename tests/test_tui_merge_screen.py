# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""CLI side of the Multitask auto-merge: a note, and a choice when needed."""

import asyncio

from navin.bus.outbound_events import WorktreeMergeEvent, outbound_message_for_event
from navin.tui.merge_screen import MergeScreen
from navin.tui.widgets import SystemNote, UserMessage
from tests.test_tui_queue import make_app


def _notes(app):
    return " ".join(str(note.render()) for note in app.query(SystemNote))


async def _dispatch(app, pilot, **fields):
    await app.runtime._dispatch(outbound_message_for_event(
        channel="cli", chat_id="direct", event=WorktreeMergeEvent(**fields),
    ))
    await pilot.pause()


_CONFLICT = dict(
    task_id="t1", label="Fix login", status="conflict", files=["a.py", "b.py"],
    conflicts=["a.py"], reason="conflict", branch="navin/multitask/t1",
    resolve_prompt="Please merge the changes from Fix login",
)


def test_a_merged_outcome_is_a_note_with_the_file_count(tmp_path):
    async def run():
        app = make_app(tmp_path)
        async with app.run_test(size=(110, 34)) as pilot:
            await _dispatch(app, pilot, task_id="t0", label="Add tests", status="merged",
                            files=["x.py", "y.py"])
            assert "Merged the changes from Add tests" in _notes(app)
            assert "2 files" in _notes(app)
            assert not isinstance(app.screen, MergeScreen)

    asyncio.run(run())


def test_a_conflict_opens_the_choice_and_discard_is_sent(tmp_path):
    async def run():
        app = make_app(tmp_path)
        calls = []

        async def merge_action(task_id, action):
            calls.append((task_id, action))
            return {"ok": True, "status": "discarded", "label": "Fix login"}

        app.runtime.merge_action = merge_action
        async with app.run_test(size=(110, 34)) as pilot:
            await _dispatch(app, pilot, **_CONFLICT)
            assert "could not be merged automatically: 1 file conflict" in _notes(app)
            assert isinstance(app.screen, MergeScreen)
            await pilot.click("#merge-discard")
            await pilot.pause()
            assert calls == [("t1", "discard")]
            assert "Discarded the changes from Fix login" in _notes(app)
            assert not isinstance(app.screen, MergeScreen)

    asyncio.run(run())


def test_resolve_sends_the_prompt_to_the_agent(tmp_path):
    async def run():
        app = make_app(tmp_path)

        async def merge_action(task_id, action):
            return {"ok": True, "status": "resolving", "resolve_prompt": _CONFLICT["resolve_prompt"]}

        app.runtime.merge_action = merge_action
        async with app.run_test(size=(110, 34)) as pilot:
            await _dispatch(app, pilot, **_CONFLICT)
            await pilot.press("r")
            await pilot.pause()
            msg = await asyncio.wait_for(app.runtime.bus.consume_inbound(), 2)
            assert "Please merge the changes from Fix login" in msg.content
            assert [row.raw_text for row in app.query(UserMessage)] == [_CONFLICT["resolve_prompt"]]

    asyncio.run(run())


def test_escape_keeps_separate_and_merge_command_answers_later(tmp_path):
    async def run():
        app = make_app(tmp_path)
        calls = []

        async def merge_action(task_id, action):
            calls.append((task_id, action))
            return {"ok": True, "status": "kept"}

        app.runtime.merge_action = merge_action
        async with app.run_test(size=(110, 34)) as pilot:
            await _dispatch(app, pilot, **{**_CONFLICT, "task_id": "t2"})
            await pilot.press("escape")
            await pilot.pause()
            assert calls == [("t2", "keep")]
            assert "Kept the changes from Fix login separate" in _notes(app)
            await app.submit_text("/merge")
            await pilot.pause()
            assert "No changes are waiting" in _notes(app)
            await _dispatch(app, pilot, **{**_CONFLICT, "task_id": "t3"})
            await pilot.press("escape")  # leaves the modal, answers keep for t3
            await pilot.pause()
            await _dispatch(app, pilot, **{**_CONFLICT, "task_id": "t4"})
            app.pop_screen()  # close without answering: still pending
            await pilot.pause()
            await app.submit_text("/merge discard")
            await pilot.pause()
            assert calls[-1] == ("t4", "discard")

    asyncio.run(run())


def test_a_handoff_to_the_agent_is_a_note_and_never_a_question(tmp_path):
    async def run():
        app = make_app(tmp_path)
        async with app.run_test(size=(110, 34)) as pilot:
            await _dispatch(app, pilot, task_id="t5", label="Fix login", status="resolving",
                            files=["a.py", "b.py"], conflicts=["a.py"], reason="uncommitted",
                            branch="navin/multitask/t5")
            assert "the agent is merging them now, keeping your edits" in _notes(app)
            assert "a.py" in _notes(app)
            assert not isinstance(app.screen, MergeScreen)
            await app.submit_text("/merge")
            await pilot.pause()
            assert "No changes are waiting" in _notes(app)

    asyncio.run(run())
