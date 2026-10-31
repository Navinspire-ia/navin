# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""A terminal restart restores drafts, pasted text and queued prompts."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from navin.tui.runtime import TuiRuntime
from navin.tui.session_state import TuiSessionStore
from navin.tui.widgets import QueuedPromptRow
from tests.test_tui_queue import make_app


def test_restart_restores_queue_and_long_draft_before_clean_shutdown(tmp_path):
    async def run():
        first = make_app(tmp_path)
        draft = "Do not lose this pasted text.\n" * 300
        async with first.run_test(size=(100, 32)) as pilot:
            await first.submit_text("running")
            await first.runtime.bus.consume_inbound()
            first.prefs.mode = "agent"
            await first.submit_text("queued task")
            first.composer.set_text(draft)
            await pilot.pause()
            # Read with a fresh store while the first app is still alive:
            # persistence must not depend on on_unmount or a graceful exit.
            state = TuiSessionStore(first._session_store.root, tmp_path).load("cli:direct")
            assert state["draft"] == draft
            assert state["queue"] == [{"text": "queued task", "inbound": "/forge queued task"}]
        second = make_app(tmp_path)
        async with second.run_test(size=(100, 32)) as pilot:
            await second._refresh_queue()
            await pilot.pause()
            assert second.composer.expand_for_submit() == draft
            # Restored queue goes out on its own after a restart (no manual Resume).
            message = await asyncio.wait_for(second.runtime.bus.consume_inbound(), 2)
            assert message.content == "/forge queued task"
            await pilot.pause()
            assert len(second.query(QueuedPromptRow)) == 0
            assert "cli:direct" not in second._queue_paused
            await second._flush_unsent_work()
            assert second._session_store.load("cli:direct")["queue"] == []
            assert second.composer.expand_for_submit() == draft
    asyncio.run(run())


def test_explicit_pause_without_queue_survives_restart(tmp_path):
    async def run():
        store = TuiSessionStore(make_app(tmp_path)._session_store.root, tmp_path)
        store.save("cli:direct", draft="", queue=[], paused=True)
        app = make_app(tmp_path)
        async with app.run_test(size=(100, 32)) as pilot:
            await pilot.pause()
            assert "cli:direct" in app._queue_paused
            assert app.runtime.bus.inbound_size == 0
    asyncio.run(run())


def test_drafts_remain_in_their_session_across_switches_and_restart(tmp_path):
    async def run():
        app = make_app(tmp_path)
        async with app.run_test(size=(100, 32)) as pilot:
            app.composer.set_text("first draft")
            await pilot.pause()
            await app.runtime.switch_session("cli:other")
            await pilot.pause(0.2)
            assert app.composer.text == ""
            app.composer.set_text("second draft")
            await pilot.pause()
            await app.runtime.switch_session("cli:direct")
            await pilot.pause(0.2)
            assert app.composer.text == "first draft"
        fresh = TuiSessionStore(app._session_store.root, tmp_path)
        assert fresh.load("cli:direct")["draft"] == "first draft"
        assert fresh.load("cli:other")["draft"] == "second draft"
        assert TuiSessionStore(app._session_store.root, tmp_path / "different").load("cli:direct") == {}
    asyncio.run(run())


def test_closing_cli_flushes_conversation_history(tmp_path):
    async def run():
        runtime = TuiRuntime(SimpleNamespace(workspace_path=tmp_path), on_event=lambda _: None)
        sessions = Mock()
        runtime.agent_loop = SimpleNamespace(stop=Mock(), close_mcp=AsyncMock(), sessions=sessions)
        with patch("navin.cli.commands._close_agent_subprocesses", new=AsyncMock()):
            await runtime.close()
            await runtime.close()
        sessions.flush_saved.assert_called_once()
    asyncio.run(run())


def test_scrolling_up_reaches_turns_that_compaction_moved_out_of_the_session(tmp_path):
    """An idle chat is compacted to its last exchanges; the CLI paints its
    history from the session, so scrolling up stopped there."""
    from navin.session.manager import SessionManager
    from navin.tui.history import visible_chat_page

    runtime = TuiRuntime(SimpleNamespace(workspace_path=tmp_path), on_event=lambda _: None)
    sessions = SessionManager(tmp_path)
    runtime.agent_loop = SimpleNamespace(sessions=sessions)
    session = sessions.get_or_create(runtime.session_key)
    old = [
        row
        for i in range(30)
        for row in ({"role": "user", "content": f"old {i}"}, {"role": "assistant", "content": f"reply {i}"})
    ]
    session.messages = [{"role": "user", "content": "latest"}, {"role": "assistant", "content": "done"}]
    sessions.save(session)
    sessions.keep_earlier_messages(session, old)

    snapshot = runtime.history_snapshot()
    assert [m["content"] for m in snapshot] == [m["content"] for m in old] + ["latest", "done"]
    rows, older = runtime.history()
    assert (rows[0]["content"], rows[-1]["content"], older) == ("old 0", "done", 0)

    # Page backwards the way the transcript does when the reader scrolls up.
    seen: list[str] = []
    cursor = None
    while True:
        page, cursor = visible_chat_page(snapshot, before=cursor)
        seen = [row["content"] for row in page] + seen
        if cursor is None:
            break
    assert seen[0] == "old 0"
    assert seen[-2:] == ["latest", "done"]
    assert len(seen) == 62


def test_editing_queue_preserves_the_full_pasted_draft(tmp_path):
    async def run():
        app = make_app(tmp_path)
        pasted = "Keep the complete pasted source.\n" * 200
        async with app.run_test(size=(100, 32)) as pilot:
            await app.submit_text("running")
            await app.runtime.bus.consume_inbound()
            await app.submit_text("queued")
            app.composer.set_text(pasted)
            await pilot.pause()
            row = app.query(QueuedPromptRow).first()
            await pilot.click(row.query_one(".queue-edit"))
            await app._flush_unsent_work()
            assert app.composer.text == "queued"
            saved = app._session_store.load("cli:direct")
            assert saved["draft"] == "queued"
            assert saved["queue"][0]["text"] == pasted.strip()
            assert saved["queue"][0]["inbound"] == pasted.strip()
    asyncio.run(run())
