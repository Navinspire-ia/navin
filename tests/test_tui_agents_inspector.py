import asyncio
import time
from types import SimpleNamespace

from textual.widgets import Button, DataTable, Input, Static

from navin.tui.agents import AgentsScreen, agent_state
from navin.tui.runtime import TuiRuntime, UiSubagent
from navin.tui.widgets import AgentsPanel
from tests.test_tui_queue import make_app


def test_all_two_hundred_agents_can_be_inspected_without_expanding_chat(tmp_path):
    async def run():
        app = make_app(tmp_path)
        async with app.run_test(size=(120, 40)) as pilot:
            panel = app.query_one(AgentsPanel)
            for index in range(200):
                await app._on_runtime_event(UiSubagent(
                    f"agent-{index:03}", f"Developer {index}", "awaiting_model", "Waiting for model response...",
                    "test-model", 2, False, None, task_description=f"Implement module {index}",
                    steps=[{"kind": "read", "verb": "Read", "target": f"module{index}.py", "status": "ok"}],
                ))
            assert len(str(panel.content).splitlines()) <= panel.MAX_ROWS
            assert len(str(panel.content).splitlines()) == 8
            assert "+194 more agents" in str(panel.content)
            assert "Developer 5" in str(panel.content)
            assert "Developer 6 " not in str(panel.content)
            await pilot.press("f8")
            await pilot.pause()
            assert isinstance(app.screen, AgentsScreen)
            table = app.screen.query_one(DataTable)
            assert table.row_count == 200
            field = app.screen.query_one(Input)
            field.value = "agent-199"
            await pilot.pause()
            assert table.row_count == 1
            detail = str(app.screen.query_one("#agent-detail", Static).content)
            assert "module199.py" in detail
            assert "test-model" in detail
            assert "Waiting for model response..." in detail
            await pilot.click(app.screen.query_one("#agents-back", Button))
            assert len(app.screen_stack) == 1
            await app._on_runtime_event(UiSubagent(
                "agent-199", "Developer 199", "done", "Completed", "test-model", 3, True, None,
            ))
            panel.dismiss("agent-199")
            panel.tick(False, 0)
            assert "agent-199" not in panel._agents
            await pilot.click(panel)
            await pilot.pause()
            assert isinstance(app.screen, AgentsScreen)
            assert "agent-199" in app.screen._rows
            app.screen.query_one(Input).value = "agent-199"
            await pilot.pause()
            assert "module199.py" in str(app.screen.query_one("#agent-detail", Static).content)
            await pilot.press("escape")

    asyncio.run(run())


def test_saved_agent_actions_paginate_and_return_preserves_chat(tmp_path):
    async def run():
        from navin.tui.agents import AgentsScreen

        calls = []

        async def load_agents():
            return [{"task_id": "saved", "label": "Saved developer", "phase": "interrupted",
                     "task_description": "Complete saved task", "model": "saved-model", "recoverable": True}]

        async def load_history(task, after):
            calls.append((task, after))
            return {"events": [{"seq": after + 1, "kind": "tool_done", "payload": {
                "name": "read_file", "arguments": {"path": "full-file.py"},
                "result": "complete output" if after == 0 else "final saved action",
            }}], "next_cursor": after + 1, "has_more": after == 0}

        operations = []

        async def operate(task, operation):
            operations.append((task, operation))
            return {"ok": False, "error": "Cannot resume: original model must be selected"}

        app = make_app(tmp_path)
        async with app.run_test(size=(120, 40)) as pilot:
            app.composer.set_text("Keep this draft")
            app.push_screen(AgentsScreen(
                lambda: {"saved": {"label": "Saved developer", "phase": "error", "status": "Cancelled"}},
                list_loader=load_agents, history_loader=load_history, operation_loader=operate,
            ))
            await pilot.pause()
            assert app.screen.query_one(DataTable).row_count == 1
            assert "complete output" in str(app.screen.query_one("#agent-detail", Static).content)
            await pilot.click(app.screen.query_one("#agents-more", Button))
            await pilot.pause()
            detail = str(app.screen.query_one("#agent-detail", Static).content)
            assert "complete output" in detail and "final saved action" in detail
            assert calls[:2] == [("saved", 0), ("saved", 1)]
            resume = app.screen.query_one("#agents-resume", Button)
            assert resume.display
            await pilot.click(resume)
            await pilot.pause()
            assert operations == [("saved", "recover")]
            assert "original model" in str(app.screen.query_one("#agents-heading", Static).content)
            await pilot.click(app.screen.query_one("#agents-back", Button))
            assert len(app.screen_stack) == 1
            assert app.composer.text == "Keep this draft"

    asyncio.run(run())


def _saved_agents_after_table_is_gone(screen_ref, gate):
    """A saved-agents load that answers once Textual removed the table.

    That is the window of a screen on its way out: its widgets are pruned
    one by one before the screen itself unmounts and cancels its workers.
    """
    async def load_agents():
        await gate.wait()
        screen = screen_ref[0]
        while screen.query("#agent-table"):
            await asyncio.sleep(0)
        return [{"task_id": "late", "label": "Late agent", "phase": "done",
                 "task_description": "Answered while the screen was leaving"}]

    return load_agents


def test_closing_while_saved_agents_load_keeps_the_chat_running(tmp_path):
    async def run():
        gate = asyncio.Event()
        screen_ref: list[AgentsScreen] = []
        app = make_app(tmp_path)
        async with app.run_test(size=(120, 40)) as pilot:
            screen = AgentsScreen(lambda: {}, list_loader=_saved_agents_after_table_is_gone(screen_ref, gate))
            screen_ref.append(screen)
            app.push_screen(screen)
            await pilot.pause()
            gate.set()
            await pilot.press("escape")
            for _ in range(20):
                await pilot.pause()
            assert len(app.screen_stack) == 1
            assert app._exception is None
            assert app.is_running

    asyncio.run(run())


def test_agent_states_read_like_the_desktop_journal():
    assert agent_state({"phase": "awaiting_model"}) == "running"
    assert agent_state({"phase": "done", "done": True}) == "succeeded"
    assert agent_state({"phase": "error", "done": True, "error": "Model timed out"}) == "failed"
    assert agent_state({"phase": "error", "done": True, "error": "cancelled"}) == "cancelled"
    assert agent_state({"phase": "interrupted", "done": False}) == "interrupted"
    # A live panel row: finished once it has done_at, failed when flagged.
    assert agent_state({"phase": "done", "done_at": 1.0, "error": False}) == "succeeded"
    assert agent_state({"phase": "error", "done_at": 1.0, "error": True}) == "failed"


def test_f8_filters_by_state_and_ctrl_r_resumes_or_relaunches(tmp_path):
    async def run():
        async def load_agents():
            return [
                {"task_id": "run", "label": "Runner", "phase": "awaiting_model", "done": False},
                {"task_id": "ok", "label": "Writer", "phase": "done", "done": True},
                {"task_id": "bad", "label": "Tester", "phase": "error", "done": True,
                 "error": "Model timed out", "requires_relaunch": True},
                {"task_id": "cut", "label": "Builder", "phase": "interrupted", "done": False,
                 "recoverable": True},
            ]

        async def load_history(task, after):
            return {"events": [], "next_cursor": after, "has_more": False}

        operations = []

        async def operate(task, operation):
            operations.append((task, operation))
            return {"ok": True, "detail": f"{operation} {task} accepted"}

        app = make_app(tmp_path)
        async with app.run_test(size=(140, 44)) as pilot:
            screen = AgentsScreen(lambda: {}, list_loader=load_agents, history_loader=load_history,
                                  operation_loader=operate)
            app.push_screen(screen)
            await pilot.pause()
            table = screen.query_one(DataTable)

            def shown():
                return [str(key.value) for key in table.rows]

            assert shown() == ["run", "ok", "bad", "cut"]
            heading = str(screen.query_one("#agents-heading", Static).content)
            assert "1 running, 1 succeeded, 2 with errors" in heading
            await pilot.click(screen.query_one("#agents-show-succeeded", Button))
            await pilot.pause()
            assert shown() == ["ok"]
            assert screen.query_one("#agents-show-succeeded", Button).variant == "primary"
            await pilot.click(screen.query_one("#agents-show-errors", Button))
            await pilot.pause()
            assert shown() == ["bad", "cut"]
            assert str(table.get_row("bad")[2]) == "Failed"

            # Ctrl+R from the filter field: relaunch the failed one ...
            screen.query_one(Input).focus()
            table.move_cursor(row=table.get_row_index("bad"))
            await pilot.pause()
            await pilot.press("ctrl+r")
            await pilot.pause()
            # ... and resume the interrupted one where it stopped.
            table.move_cursor(row=table.get_row_index("cut"))
            await pilot.pause()
            await pilot.press("ctrl+r")
            await pilot.pause()
            assert operations == [("bad", "relaunch"), ("cut", "recover")]

            await pilot.click(screen.query_one("#agents-show-running", Button))
            await pilot.pause()
            assert shown() == ["run"]
            await pilot.press("ctrl+r")
            await pilot.pause()
            assert operations == [("bad", "relaunch"), ("cut", "recover")]
            assert "nothing to resume or relaunch" in str(screen.query_one("#agents-heading", Static).content)
            await pilot.press("escape")

    asyncio.run(run())


def test_f8_lists_only_this_project_and_this_conversation(tmp_path):
    """The CLI asks the journal for its own project folder and its last /new only."""
    from navin.security.workspace_access import WorkspaceScopeResolver
    from navin.session.manager import CONVERSATION_STARTED_KEY, SessionManager

    async def run():
        calls = []

        def list_agents(session_key, *, offset, limit, workspaces, since):
            calls.append((session_key, workspaces, since))
            return [{"task_id": "mine"}]

        sessions = SessionManager(tmp_path)
        runtime = TuiRuntime(SimpleNamespace(workspace_path=tmp_path), session_id="cli:direct",
                             on_event=lambda _event: None)
        runtime.agent_loop = SimpleNamespace(
            subagents=SimpleNamespace(workspace=tmp_path, list_agents=list_agents),
            sessions=sessions,
            workspace_scopes=WorkspaceScopeResolver(default_workspace=tmp_path,
                                                    default_restrict_to_workspace=False),
        )
        assert await runtime.list_subagents() == [{"task_id": "mine"}]
        assert calls[-1] == ("cli:direct", {str(tmp_path)}, None)

        session = sessions.get_or_create("cli:direct")
        session.clear()  # what /new does: same key, new conversation
        started = session.metadata[CONVERSATION_STARTED_KEY]
        await runtime.list_subagents()
        assert calls[-1] == ("cli:direct", {str(tmp_path)}, started)

    asyncio.run(run())


def test_a_new_chat_drops_the_finished_agents_of_the_previous_one(tmp_path):
    async def run():
        app = make_app(tmp_path)
        async with app.run_test(size=(120, 40)) as pilot:
            panel = app.query_one(AgentsPanel)
            await app._on_runtime_event(UiSubagent("done", "Old", "done", "Completed", None, 1, True, None))
            await app._on_runtime_event(UiSubagent("live", "Busy", "awaiting_model", "Working", None, 1, False, None))
            await pilot.pause()
            panel.forget_finished()
            assert set(panel.inspection_rows()) == {"live"}

    asyncio.run(run())


def test_finished_rows_persist_until_dismissed_with_hint(tmp_path):
    async def run():
        app = make_app(tmp_path)
        async with app.run_test(size=(140, 40)) as pilot:
            panel = app.query_one(AgentsPanel)
            await app._on_runtime_event(UiSubagent("ok", "Writer", "done", "Completed", None, 1, True, None))
            await app._on_runtime_event(UiSubagent("bad", "Tester", "error", "", None, 1, True, "Model timed out"))
            await app._on_runtime_event(UiSubagent("run", "Runner", "awaiting_model", "Working", None, 1, False, None))
            await app._on_runtime_event(UiSubagent("ask", "Asker", "awaiting_approval",
                                                   "Waiting for your approval", None, 1, False, None))
            await pilot.pause()
            lines = str(panel.content).splitlines()
            assert len(lines) <= AgentsPanel.MAX_ROWS
            assert lines[1].startswith("×") and "Tester" in lines[1]
            assert lines[2].startswith("!") and "Waiting for your approval (answer above)" in lines[2]
            assert lines[3].startswith("◯") and "Runner" in lines[3]
            assert lines[4].startswith("✓") and "Writer" in lines[4]
            assert "✓ Done" in lines[4] and "· F9 dismiss" in lines[4]
            assert "× Model timed out" in lines[1] and "· F8 details" in lines[1]
            assert "F8 details" not in lines[4] and "F9 dismiss" not in lines[1]
            assert "F9 dismiss" not in lines[2] and "F8 details" not in lines[3], "running rows carry no hint"
            assert AgentsPanel.HINT not in lines, "the shared bottom hint line is gone"
            assert panel.dismiss("ok")
            assert not panel.dismiss("run"), "a running agent is not dismissed"
            await app._on_runtime_event(UiSubagent("ok", "Writer", "done", "Completed", None, 1, True, None))
            assert "Writer" not in str(panel.content), "late done frame stays hidden"
            await pilot.press("f9")
            await pilot.pause()
            content = str(panel.content)
            assert "Tester" not in content and "F8 details" not in content and "F9 dismiss" not in content
            assert "Runner" in content and "Asker" in content
            await app._on_runtime_event(UiSubagent("bad", "Tester", "awaiting_model", "Resumed", None, 1, False, None))
            assert "Resumed" in str(panel.content), "a resumed agent shows again"
            assert "ok" in panel.inspection_rows()

    asyncio.run(run())


def test_finished_rows_auto_dismiss_after_grace_period(tmp_path):
    async def run():
        app = make_app(tmp_path)
        async with app.run_test(size=(140, 40)) as pilot:
            panel = app.query_one(AgentsPanel)
            await app._on_runtime_event(UiSubagent("ok", "Writer", "done", "Completed", None, 1, True, None))
            await app._on_runtime_event(UiSubagent("bad", "Tester", "error", "", None, 1, True, "Model timed out"))
            await app._on_runtime_event(UiSubagent("run", "Runner", "awaiting_model", "Working", None, 1, False, None))
            await pilot.pause()
            assert panel.AUTO_DISMISS_DONE_S == 10 * 60 and panel.AUTO_DISMISS_ERROR_S == 15 * 60
            assert {"ok", "bad", "run"} <= set(panel._agents)

            def age(seconds: float) -> None:
                for row in panel._agents.values():
                    if "done_at" in row:
                        row["done_at"] = time.monotonic() - seconds
                    else:
                        row["started"] = time.monotonic() - seconds

            age(9 * 60)
            panel.tick(False, 0)
            assert {"ok", "bad", "run"} <= set(panel._agents), "nothing leaves before 10 minutes"
            age(10 * 60)
            panel.tick(False, 0)
            assert "ok" not in panel._agents and "ok" in panel._dismissed
            assert "bad" in panel._agents and "run" in panel._agents
            content = str(panel.content)
            assert "Writer" not in content and "Tester" in content and "Runner" in content
            age(15 * 60)
            panel.tick(False, 0)
            assert "bad" not in panel._agents and "bad" in panel._dismissed
            assert "run" in panel._agents, "a running agent is never auto-dismissed"
            assert "Tester" not in str(panel.content) and "Runner" in str(panel.content)
            assert {"ok", "bad"} <= set(panel.inspection_rows()), "auto-dismissed agents stay in F8"
            assert panel.display is True
            # A late duplicate done frame of an auto-dismissed agent stays hidden.
            await app._on_runtime_event(UiSubagent("ok", "Writer", "done", "Completed", None, 1, True, None))
            assert "Writer" not in str(panel.content)
            # The last running agent finishes and ages out: the panel hides itself.
            await app._on_runtime_event(UiSubagent("run", "Runner", "done", "Completed", None, 1, True, None))
            await pilot.pause()
            assert panel.display is True and "✓ Done" in str(panel.content)
            age(10 * 60)
            panel.tick(False, 0)
            assert not panel._agents and panel.display is False
            assert str(panel.content) == ""

    asyncio.run(run())


def test_f8_cancel_and_dismiss_buttons(tmp_path):
    async def run():
        operations = []

        async def operate(task, operation):
            operations.append((task, operation))
            return {"ok": True, "detail": f"{operation} {task} done"}

        app = make_app(tmp_path)
        async with app.run_test(size=(160, 44)) as pilot:
            panel = app.query_one(AgentsPanel)
            app.runtime.subagent_operation = operate
            await app._on_runtime_event(UiSubagent("run", "Runner", "awaiting_model", "Working", None, 1, False, None))
            await app._on_runtime_event(UiSubagent("bad", "Tester", "error", "", None, 1, True, "Model timed out"))
            await pilot.pause()
            app.action_inspect_agents("run")
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, AgentsScreen)
            assert screen._selected == "run"
            cancel = screen.query_one("#agents-cancel", Button)
            assert cancel.display
            assert not screen.query_one("#agents-dismiss", Button).display
            cancel.press()
            await pilot.pause()
            await pilot.pause()
            assert operations == [("run", "cancel")]
            assert "cancel run done" in str(screen.query_one("#agents-heading", Static).content)
            table = screen.query_one(DataTable)
            table.move_cursor(row=table.get_row_index("bad"))
            await pilot.pause()
            assert not screen.query_one("#agents-cancel", Button).display
            screen.query_one("#agents-dismiss", Button).press()
            await pilot.pause()
            assert "bad" not in panel._agents
            assert "Tester" not in str(panel.content)

    asyncio.run(run())


def test_clicking_a_failed_row_opens_f8_on_it(tmp_path):
    async def run():
        app = make_app(tmp_path)
        async with app.run_test(size=(140, 40)) as pilot:
            panel = app.query_one(AgentsPanel)
            await app._on_runtime_event(UiSubagent("run", "Runner", "awaiting_model", "Working", None, 1, False, None))
            await app._on_runtime_event(UiSubagent("bad", "Tester", "error", "", None, 1, True, "Model timed out"))
            await pilot.pause()
            lines = str(panel.content).splitlines()
            assert "Tester" in lines[1]
            await pilot.click(panel, offset=(4, 1))
            await pilot.pause()
            await pilot.pause()
            assert isinstance(app.screen, AgentsScreen)
            assert app.screen._selected == "bad"

    asyncio.run(run())
