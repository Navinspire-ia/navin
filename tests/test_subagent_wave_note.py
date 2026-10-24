"""A subagent result tells the parent whether more results are still coming."""

import asyncio

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
