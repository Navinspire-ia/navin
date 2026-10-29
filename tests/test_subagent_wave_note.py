"""A subagent result tells the parent whether more results are still coming."""

import asyncio
from collections import deque

from tests.test_subagent_delivery_guards import _manager

ORIGIN = {"channel": "cli", "chat_id": "one", "session_key": "cli:one"}


def test_announcement_says_whether_siblings_are_still_running(tmp_path, monkeypatch):
    async def run():
        manager = _manager(tmp_path, monkeypatch)
        sibling = asyncio.get_running_loop().create_future()
        manager._session_tasks["cli:one"] = {"a", "b"}
        manager._running_tasks["b"] = sibling
        await manager._announce_result("a", "first", "task", "done a", ORIGIN, "ok")
        early = manager.bus.published[-1].content
        assert "Still running for this request: 1 other subagent(s)" in early
        assert "Do not give the final summary yet" in early

        sibling.set_result(None)
        manager._session_tasks["cli:one"] = {"b"}
        await manager._announce_result("b", "second", "task", "done b", ORIGIN, "error")
        last = manager.bus.published[-1].content
        assert "last subagent still running" in last
        assert "Still running" not in last
        # The closing summary lists every subagent's need, work and result.
        assert "Markdown table, one row per subagent" in last
        assert "ask that question yourself with ask_user" in last

    asyncio.run(run())


def test_announcement_without_a_known_session_adds_nothing(tmp_path, monkeypatch):
    manager = _manager(tmp_path, monkeypatch)
    assert manager._wave_note("orphan") == ""


def test_siblings_ending_together_still_close_the_wave(tmp_path, monkeypatch):
    """Two subagents finishing at once: exactly one result closes the wave.

    The note was read before the outcome write: each result saw the other as
    still running (its result was in flight), both said "Still running", the
    closing note never came, and the parent told the user that finished work
    was still going.
    """
    async def run():
        manager = _manager(tmp_path, monkeypatch)
        loop = asyncio.get_running_loop()
        tasks = {tid: loop.create_future() for tid in ("a", "b")}
        manager._session_tasks["cli:one"] = set(tasks)
        manager._running_tasks.update(tasks)
        release = asyncio.Event()
        entered: list[str] = []

        async def slow_save(task_id, *_args):
            entered.append(task_id)
            await release.wait()

        manager._save_outcome = slow_save
        announcing = [
            asyncio.create_task(
                manager._announce_result(tid, tid, "task", f"done {tid}", ORIGIN, "ok")
            )
            for tid in tasks
        ]
        while len(entered) < 2:
            await asyncio.sleep(0)
        release.set()
        await asyncio.gather(*announcing)

        notes = [message.content for message in manager.bus.published]
        assert len(notes) == 2
        assert sum("last subagent still running" in note for note in notes) == 1
        assert sum("Still running for this request" in note for note in notes) == 1
        # Both runs are still tidying up: alive, yet nothing is owed any more.
        assert manager.get_running_count_by_session("cli:one") == 2
        assert manager.pending_result_count("cli:one") == 0
        manager._forget("a")
        assert "a" not in manager._announced_results
        for future in tasks.values():
            future.set_result(None)

    asyncio.run(run())


def test_queued_work_is_still_owed(tmp_path, monkeypatch):
    async def run():
        manager = _manager(tmp_path, monkeypatch)
        running = asyncio.get_running_loop().create_future()
        manager._session_tasks["cli:one"] = {"a"}
        manager._running_tasks["a"] = running
        manager._queued["cli:one"] = deque([object()])
        assert manager.pending_result_count("cli:one") == 2
        manager._mark_announced("a")
        assert manager.pending_result_count("cli:one") == 1
        # A task that is not running records nothing (it would never be cleared).
        manager._mark_announced("gone")
        assert "gone" not in manager._announced_results
        running.set_result(None)

    asyncio.run(run())


def test_subagent_asks_through_the_parent_choice_modal():
    """A subagent question reaches the same broker (modal) as the main agent's."""
    from navin.agent import choice
    from navin.agent.tools.ask_user import AskUserTool

    # ToolLoader(scope="subagent") keeps a tool only when its scopes allow it.
    assert "subagent" in AskUserTool._scopes, "subagents must be able to ask the user"
    tool = AskUserTool()

    seen = []

    class Broker:
        async def ask(self, request):
            seen.append(request)
            return choice._unattended(request)

    async def run():
        token = choice.bind_choice_broker(Broker())
        try:
            # The subagent runs in a task spawned during the parent turn.
            child = asyncio.create_task(tool.execute(
                question="Which module reads the invoices?",
                options=[
                    {"id": "a", "label": "Invoice import", "recommended": True},
                    {"id": "b", "label": "PDF reader"},
                ],
            ))
        finally:
            choice.reset_choice_broker(token)
        return await child

    asyncio.run(run())
    assert len(seen) == 1
    assert "Which module" in seen[0].question


def test_subagent_prompt_routes_questions_to_ask_user():
    from pathlib import Path

    import navin

    text = (Path(navin.__file__).parent / "templates" / "agent" / "subagent_system.md").read_text(
        encoding="utf-8"
    )
    assert "call `ask_user`" in text
    assert "Never end your run with questions" in text
    assert "`Need:`" in text and "`Done:`" in text and "`Result:`" in text
