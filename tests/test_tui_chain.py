# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Claude Code style chain: brief thought, narration, one row per step with a
status icon and a short outcome, one live working line, echoed answers."""

from __future__ import annotations

import asyncio

from rich.console import Console
from textual.widgets import Static

from navin.tui.runtime import (
    UiChoiceClosed,
    UiChoiceRequested,
    UiReasoning,
    UiSubagent,
    UiToolEvent,
)
from navin.tui.widgets import (
    AgentsPanel,
    ProgressLine,
    PromptEcho,
    ReasoningBlock,
    ToolCall,
    WorkingLine,
)
from navin.utils.tool_hints import (
    activity_head_text,
    activity_label,
    activity_palette,
    ask_user_answer_text,
    humanize_agent_error,
    result_summary,
)
from tests.test_tui_activity import ActivityHost
from tests.test_tui_queue import make_app


def test_result_summaries_are_short_outcomes():
    read = "1| a\n2| b\n3| c\n\n(End of file - 3 lines total)"
    assert result_summary("read_file", {"path": "a.py"}, read) == "3 lines"
    assert result_summary("grep", {"pattern": "x"}, "a.py:1:x\nb.py:4:x\na.py:9:x") == "3 matches in 2 files"
    assert result_summary("grep", {"pattern": "x"}, "found\n\n(total matches: 7 in 3 files)") == "7 matches in 3 files"
    assert result_summary("grep", {"pattern": "x"}, "No matches found for pattern 'x' in .") == "no matches"
    assert result_summary("exec", {"command": "ls"}, "a\nb\nExit code: 0") == "exit 0 · 2 lines"
    assert result_summary("exec", {"command": "ls"}, "Exit code: 2", phase="error") == "exit 2 · no output"
    error = "Error: Invalid parameters for tool 'apply_patch': missing required edits[0].path"
    assert result_summary("apply_patch", {}, None, phase="error", error=error) == "invalid arguments"
    assert result_summary("read_file", {"path": "a.py"}, read, phase="start") == ""


def test_agent_errors_are_humanized_and_labels_say_what_happened():
    assert humanize_agent_error("httpx.ReadTimeout: The read operation timed out", retrying=True) == "Model timed out, retrying"
    assert humanize_agent_error("Error code: 429 - rate limit exceeded") == "Rate limited"
    assert activity_label("spawn", {"label": "auditor", "task": "Audit login"}) == "Agent auditor"
    assert activity_label("spawn", {"task": "Audit the login flow\nthen report"}) == "Agent Audit the login flow"
    assert activity_label("board", {"action": "move", "task_id": "T3", "status": "done"}) == "Completed task T3"
    assert activity_label("board", {"action": "plan"}) == "Updated plan"
    assert ask_user_answer_text('The user chose "b" (Beta).') == "Beta"


def test_status_icons_are_green_and_red_and_paths_stay_plain():
    console = Console()
    palette = activity_palette()
    ok = activity_head_text("✓ Ran pytest tests/a.py")
    bad = activity_head_text("├ × Read missing.py")
    assert ok.get_style_at_offset(console, 0).color.triplet.hex == palette["green"].lower()
    assert bad.get_style_at_offset(console, 2).color.triplet.hex == palette["coral"].lower()
    assert ok.get_style_at_offset(console, len("✓ Ran pytest ")).color is None


def test_reasoning_folds_to_one_line_merges_segments_and_closes_on_finish():
    async def run():
        app = ActivityHost()
        async with app.run_test(size=(90, 24)) as pilot:
            block = app.block
            await block.reasoning("first idea")
            await block.reasoning("", end=True)
            await block.reasoning("second idea")
            await pilot.pause()
            thoughts = list(block.query(ReasoningBlock))
            assert len(thoughts) == 1 and not thoughts[0]._done
            await block.finish(latency_ms=1, model=None, preset=None)
            await pilot.pause()
            assert thoughts[0]._done
            head = str(thoughts[0].query_one(".reasoning-head", Static).content)
            assert "Thought for" in head and "first idea" not in head

    asyncio.run(run())


def test_a_call_without_id_keeps_one_row_and_finished_rows_show_outcomes():
    async def run():
        app = ActivityHost()
        async with app.run_test(size=(100, 30)) as pilot:
            block = app.block
            await block.tool_event("", "web_fetch", "start", {"url": "https://example.com"}, None, None, None)
            await block.tool_event("", "web_fetch", "end", {}, "line one\nline two", None, None)
            await block.tool_event("r1", "read_file", "start", {"path": "a.py"}, None, None, None)
            await pilot.pause()
            read = next(row for row in block.query(ToolCall) if row.call_id == "r1")
            assert read.spinning, "grouped rows animate while running"
            await block.tool_event("r1", "read_file", "end", {}, "1| a\n\n(End of file - 1 lines total)", None, None)
            await pilot.pause()
            assert not read.spinning, "a finished row leaves the shared tick"
            fetch = [row for row in block.query(ToolCall) if row.tool_name == "web_fetch"]
            assert len(fetch) == 1 and fetch[0]._head_text().startswith("✓ ")
            assert "└ 2 lines" in str(fetch[0].query_one(".tool-summary", Static).content)
            assert "✓ Read a.py" in read._head_text()
            assert "1 line" in str(read.query_one(".tool-head", Static).content)

    asyncio.run(run())


def test_running_command_output_is_live_and_follows_the_newest_line():
    async def run():
        app = ActivityHost()
        async with app.run_test(size=(100, 30)) as pilot:
            block = app.block
            await block.tool_event("run", "exec", "start", {"command": "make"}, None, None, None)
            row = block.query_one(ToolCall)
            assert not row._open
            await block.tool_event("run", "exec", "output", {}, None, None, "building step 1\n")
            await pilot.pause(0.2)
            assert row._open
            assert "building step 1" in str(row.query_one(".tool-body", Static).content)

    asyncio.run(run())


def test_narration_and_tools_alternate_in_a_dense_chain():
    async def run():
        app = ActivityHost()
        async with app.run_test(size=(100, 30)) as pilot:
            block = app.block
            await block.progress("I check the config first.")
            await block.tool_event("a", "exec", "end", {"command": "ls"}, "x\nExit code: 0", None, None)
            await block.progress("Now the tests.")
            await block.tool_event("b", "exec", "end", {"command": "pytest"}, "1 passed\nExit code: 0", None, None)
            await pilot.pause()
            kinds = [type(widget).__name__ for widget in block._activity_blocks]
            assert kinds == ["ProgressLine", "ToolCall", "ProgressLine", "ToolCall"]
            rows = list(block.query(ToolCall))
            # Each command keeps a blank line after it; the narration margin collapses with it.
            assert all(row.styles.margin.top == 0 and row.styles.margin.bottom == 1 for row in rows)
            assert block.query(ProgressLine)[1].styles.margin.top == 1

    asyncio.run(run())


def test_working_line_names_the_current_step_and_choice_answers_stay(tmp_path):
    async def run():
        app = make_app(tmp_path)
        async with app.run_test(size=(100, 32)) as pilot:
            await app.submit_text("go")
            await app.runtime.bus.consume_inbound()
            await app._on_runtime_event(UiReasoning("hmm"))
            app._refresh_working_line()
            assert "Working…" in str(app.query_one(WorkingLine).content)
            await app._on_runtime_event(UiToolEvent("t1", "exec", "start", {"command": "pytest -q"}))
            app._refresh_working_line()
            assert "Running pytest" in str(app.query_one(WorkingLine).content)
            await app._on_runtime_event(UiToolEvent("t1", "exec", "end", result="ok\nExit code: 1"))
            app._refresh_working_line()
            assert "Running pytest" not in str(app.query_one(WorkingLine).content)
            assert app._activity[-1].startswith("×")
            await app._on_runtime_event(UiChoiceRequested(
                "q1", "Which database?", [{"id": "pg", "label": "Postgres"}, {"label": "SQLite"}],
                True, "pg", None,
            ))
            await pilot.pause()
            await app._on_runtime_event(UiChoiceClosed("q1", "2", False))
            await pilot.pause()
            echo = app.query_one(PromptEcho)
            assert echo.echo == "? Which database?\n→ SQLite"

    asyncio.run(run())


def test_agents_panel_shows_humanized_status_and_red_cross_only_when_done(tmp_path):
    async def run():
        app = make_app(tmp_path)
        async with app.run_test(size=(120, 30)) as pilot:
            panel = app.query_one("#agents", AgentsPanel)
            await app._on_runtime_event(UiSubagent(
                "t1", "auditor", "error", "", None, 1, False,
                "httpx.ReadTimeout: The read operation timed out after 600s",
            ))
            await pilot.pause()
            text = str(panel.content)
            assert "Model timed out, retrying" in text and "httpx" not in text
            assert "done_at" not in panel._agents["t1"]
            await app._on_runtime_event(UiSubagent(
                "t1", "auditor", "error", "", None, 2, True, "Error code: 429 rate limit",
            ))
            await pilot.pause()
            text = str(panel.content)
            assert "× auditor" in text and "Rate limited" in text
            assert app._activity[-1].startswith("×")

    asyncio.run(run())


def test_consecutive_commands_do_not_touch():
    async def run():
        app = ActivityHost()
        async with app.run_test(size=(100, 30)) as pilot:
            block = app.block
            await block.tool_event("a", "exec", "end", {"command": "ls"}, "x\nExit code: 0", None, None)
            await block.tool_event("b", "exec", "end", {"command": "pwd"}, "/tmp\nExit code: 0", None, None)
            await pilot.pause()
            first, second = list(block.query(ToolCall))[:2]
            gap = second.region.y - (first.region.y + first.region.height)
            assert gap >= 1, f"commands are glued together (gap {gap})"

    asyncio.run(run())
