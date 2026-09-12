# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Developer activity must report real changes, including replay and failures."""

from __future__ import annotations

import asyncio
import io
import subprocess
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from rich.cells import cell_len
from rich.console import Console
from rich.text import Text

from navin.agent.hooks.file_edit_activity import create_file_edit_activity_hook
from navin.agent.runner import AgentRunner, AgentRunSpec
from navin.agent.tools.apply_patch import ApplyPatchTool
from navin.agent.tools.file_manage import ManageFilesTool
from navin.agent.turn_hooks import AgentTurnHookSpec, build_agent_turn_hook
from navin.cli.activity import ActivityPrinter
from navin.providers.base import LLMResponse
from navin.tui.history import visible_chat_rows
from navin.tui.runtime import UiFileEdit
from navin.utils.file_edit_events import (
    build_file_edit_end_event,
    build_file_edit_start_event,
    prepare_file_edit_trackers,
)
from navin.utils.llm_runtime import LLMRuntime
from navin.utils.tool_hints import (
    activity_label,
    edit_group_key,
    format_preview_markup_line,
    format_tool_detail,
    preview_rows,
)
from tests.test_long_task_continuation import ScriptedProvider, registry, tool_call


def test_actual_add_replace_delete_events_preserve_counts_and_operation(tmp_path):
    target = tmp_path / "example.py"
    for before, after, operation, counts in [
        (None, "answer = [1, 2]\n", "create", (1, 0)),
        ("answer = [1, 2]\n", "answer = [3]\n", "edit", (1, 1)),
        ("answer = [3]\n", None, "delete", (0, 1)),
        (None, "", "create", (0, 0)),
        ("", "", "unchanged", (0, 0)),
    ]:
        if before is not None:
            target.write_text(before)
        trackers = prepare_file_edit_trackers(
            call_id="edit", tool_name="write_file", tool=None,
            workspace=tmp_path, params={"path": "example.py"},
        )
        start = UiFileEdit.from_payload(build_file_edit_start_event(trackers[0]))
        assert start.phase == "start" and start.added == start.removed == 0
        if after is None:
            target.unlink()
        else:
            target.write_text(after)
        event = UiFileEdit.from_payload(build_file_edit_end_event(trackers[0]))
        assert event.kind == operation
        assert (event.added, event.removed) == counts
        assert event.call_id == "edit"
        if sum(counts):
            rows = preview_rows("write_file", {}, diff_text=event.diff)
            assert sum(row[1] == "add" for row in rows) == counts[0]
            assert sum(row[1] == "del" for row in rows) == counts[1]


def test_standard_diff_headers_gaps_and_brackets_render_as_literal_code():
    diff = "--- a.py\n+++ a.py\n@@ -8,2 +8,2 @@\n-values[0]\n+values[1]\n keep\n@@ -90 +90 @@\n-old\n+new\n"
    rendered = format_tool_detail("edit_file", {"path": "a.py"}, diff_text=diff)
    assert "   8 -values[0]" in rendered and "   8 +values[1]" in rendered
    assert "  90 +new" in rendered and "⋮" in rendered
    assert "--- a.py" not in rendered
    text = Text.from_markup(format_preview_markup_line(8, "add", "values[1]", width=24))
    assert text.plain.rstrip() == "   8 +values[1]"
    assert text.cell_len == 24


@pytest.mark.parametrize("dark", [True, False])
def test_diff_colors_remain_readable_and_marks_work_without_color(dark):
    for kind, sign in [("add", "+"), ("del", "-"), ("ctx", " ")]:
        text = Text.from_markup(format_preview_markup_line(3, kind, "値 = [42]", width=22, dark=dark))
        assert text.plain.startswith(f"   3 {sign}値 = [42]")
        assert text.cell_len == 22
        style = text.spans[0].style
        from rich.style import Style

        parsed = Style.parse(str(style))
        foreground = parsed.color.get_truecolor()
        background = parsed.bgcolor.get_truecolor()
        def luminance(rgb):
            channels = [v / 255 for v in rgb]
            linear = [v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in channels]
            return sum(v * weight for v, weight in zip(linear, (0.2126, 0.7152, 0.0722)))
        high, low = sorted((luminance(foreground), luminance(background)), reverse=True)
        assert (high + 0.05) / (low + 0.05) >= 4.5


@pytest.mark.parametrize("dark", [True, False])
@pytest.mark.parametrize("kind,sign", [("add", "+"), ("del", "-")])
def test_long_source_lines_keep_markers_color_and_copyable_content(dark, kind, sign):
    source = '\tmessage = "Résultat 値: [first_value] and [last_value] remain readable"'
    width = 34
    rendered = Text.from_markup(format_preview_markup_line(7, kind, source, width=width, dark=dark))
    rows = rendered.split("\n")
    assert len(rows) > 1
    assert rows[0].plain.startswith(f"   7 {sign}    message")
    assert all(row.plain.startswith(f"     {sign}") for row in rows[1:])
    assert all(cell_len(row.plain) == width for row in rows)
    unwrapped = "".join(row.plain[6:] for row in rows)
    assert unwrapped.replace(" ", "") == source.expandtabs(4).replace(" ", "")
    console = Console()
    for row in rows:
        # The wash extends through padding, including the last wrapped line.
        start = row.get_style_at_offset(console, 0)
        end = row.get_style_at_offset(console, len(row) - 1)
        assert start.bgcolor is not None and end.bgcolor == start.bgcolor
    diff = f"@@ -7 +7 @@\n{sign}{source}\n"
    assert source in format_tool_detail("edit_file", {}, diff_text=diff)


def test_homonymous_and_case_distinct_files_never_share_a_row():
    keys = {edit_group_key("edit_file", {"path": path}) for path in ["src/a.py", "tests/a.py", "src/A.py"]}
    assert len(keys) == 3
    assert edit_group_key("edit_file", {"path": "./src/a.py"}) in keys
    assert edit_group_key("edit_file", {"path": r"C:\src\a.py"}) != edit_group_key("edit_file", {"path": r"C:\tests\a.py"})


def test_command_arguments_and_streamed_output_are_not_lost_or_double_counted():
    command = ".venv/bin/python -m pytest tests/test_alpha.py tests/test_beta.py -q --tb=short"
    output = "\n".join(f"test-{i} passed" for i in range(80))
    stream = io.StringIO()
    printer = ActivityPrinter(Console(file=stream, width=140, force_terminal=False))
    printer.consume(tool_events=[{"call_id": "run", "name": "exec", "phase": "start", "arguments": {"command": command}}])
    printer.consume(tool_events=[{"call_id": "run", "name": "exec", "phase": "output", "output": output}])
    printer.consume(tool_events=[{"call_id": "run", "name": "exec", "phase": "end", "result": output}])
    rendered = stream.getvalue()
    assert command in rendered
    assert rendered.count("test-0 passed") == rendered.count("test-79 passed") == 1
    assert "(+80" not in rendered and "+80" not in activity_label("exec", {"command": command})
    assert "\x1b" not in rendered


def test_multi_file_patch_replays_with_the_exact_historical_diff(tmp_path):
    """Real tools -> hooks -> persisted messages -> both terminal event consumers."""
    async def run():
        (tmp_path / "src").mkdir()
        (tmp_path / "tests").mkdir()
        (tmp_path / "src/a.py").write_text("value = 1\n")
        tools = registry(tmp_path)
        tools.register(ApplyPatchTool(workspace=tmp_path))
        provider = ScriptedProvider([
            tool_call("apply_patch", edits=[
                {"action": "replace", "path": "src/a.py", "old_text": "value = 1", "new_text": "value = 2"},
                {"action": "add", "path": "tests/a.py", "new_text": "assert 2 == 2\n"},
            ]),
            LLMResponse(content="Patch applied."),
        ])
        output = io.StringIO()
        printer = ActivityPrinter(Console(file=output, width=100, force_terminal=False))
        seen = []
        async def progress(content, **kwargs):
            printer.consume(tool_events=kwargs.get("tool_events"), file_edit_events=kwargs.get("file_edit_events"))
            seen.extend(kwargs.get("file_edit_events") or [])
        hook = build_agent_turn_hook(AgentTurnHookSpec(
            on_progress=progress, workspace=tmp_path,
            registered_hook_factories=[create_file_edit_activity_hook],
        ))
        result = await AgentRunner().run(AgentRunSpec(
            initial_messages=[{"role": "user", "content": "Apply this patch."}], tools=tools,
            runtime=LLMRuntime.capture(provider, "test-activity", context_window_tokens=128_000),
            workspace=tmp_path, max_iterations=4, max_tool_result_chars=4_000, hook=hook,
        ))
        assert result.stop_reason == "completed"
        completed = [event for event in seen if event["phase"] == "end"]
        assert [(e["path"], e["operation"]) for e in completed] == [("src/a.py", "edit"), ("tests/a.py", "create")]
        saved = next(message for message in result.messages if message["role"] == "tool")
        assert saved["_file_edits"] == completed
        (tmp_path / "src/a.py").write_text("value = 999\n")
        rows, _ = visible_chat_rows(result.messages)
        restored = next(row["tools"][0] for row in rows if row.get("tools"))
        assert "value = 2" in restored["file_edits"][0]["diff"]["text"]
        assert "999" not in str(restored["file_edits"])
        rendered = output.getvalue()
        assert "Edited 2 files (+2 -1)" in rendered
        assert "Edited src/a.py (+1 -1)" in rendered
        assert "Added tests/a.py (+1 -0)" in rendered
        assert rendered.count("+value = 2") == 1
        assert "assert 2 == 2" in rendered
    asyncio.run(run())


def test_cancelled_file_event_cannot_print_an_applied_change():
    stream = io.StringIO()
    printer = ActivityPrinter(Console(file=stream, width=100, force_terminal=False))
    printer.consume(file_edit_events=[{
        "call_id": "cancel", "tool": "write_file", "path": "not-created.py",
        "phase": "cancelled", "error": "Interrupted before execution.",
    }])
    assert "Cancelled" in stream.getvalue()
    assert "Added" not in stream.getvalue()
    assert "Interrupted before execution" in stream.getvalue()


def test_git_restore_reports_the_reverse_diff_and_unstaging_is_not_an_edit(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    target = tmp_path / "config.py"
    target.write_text("timeout = 30\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "config.py"], check=True)
    target.write_text("timeout = 1\n")
    trackers = prepare_file_edit_trackers(
        call_id="undo", tool_name="git", tool=None, workspace=tmp_path,
        params={"action": "restore", "paths": ["config.py"]},
    )
    subprocess.run(["git", "-C", str(tmp_path), "restore", "--", "config.py"], check=True)
    event = UiFileEdit.from_payload(build_file_edit_end_event(trackers[0]))
    assert event.kind == "restore"
    assert "+timeout = 30" in event.diff and "-timeout = 1" in event.diff
    assert not prepare_file_edit_trackers(
        call_id="unstage", tool_name="git", tool=None, workspace=tmp_path,
        params={"action": "restore", "paths": ["config.py"], "staged": True},
    )


@pytest.mark.parametrize("action", ["delete", "move", "copy"])
def test_real_file_manager_emits_deletion_and_destination_diffs(tmp_path, action):
    async def run():
        folder = tmp_path / "old"
        folder.mkdir()
        (folder / "a.txt").write_text("first\nsecond\n")
        (folder / "b.txt").write_text("third\n")
        tool = ManageFilesTool(workspace=tmp_path)
        params = {"action": action, "paths": ["old"], "recursive": True} if action == "delete" else {
            "action": action, "path": "old", "destination": "new",
        }
        trackers = prepare_file_edit_trackers(call_id="manage", tool_name="manage_files", tool=tool, workspace=tmp_path, params=params)
        result = await tool.execute(**params)
        assert not str(result).startswith("Error:"), result
        events = [UiFileEdit.from_payload(build_file_edit_end_event(tracker)) for tracker in trackers]
        deleted = [event for event in events if event.kind == "delete"]
        added = [event for event in events if event.kind == "create"]
        assert sum(event.removed for event in deleted) == (0 if action == "copy" else 3)
        assert sum(event.added for event in added) == (0 if action == "delete" else 3)
        if action != "delete":
            assert {event.path for event in added} == {"new/a.txt", "new/b.txt"}
        if action != "copy":
            assert not folder.exists()
            assert {event.path for event in deleted} == {"old/a.txt", "old/b.txt"}
    asyncio.run(run())


def test_message_cli_routes_structured_activity_and_preserves_literal_progress(tmp_path, monkeypatch, capsys):
    from navin.cli import commands
    from navin.config.schema import Config

    config = Config()
    config.agents.defaults.workspace = str(tmp_path)
    config.channels.send_tool_hints = True
    config.channels.send_progress = True

    async def process_direct(message, session, *, on_progress, **kwargs):
        await on_progress("Reading values[0] and [blue] literally.")
        await on_progress("ignored duplicate hint", tool_hint=True, tool_events=[{
            "call_id": "read", "name": "read_file", "phase": "end",
            "arguments": {"path": "src/config.py"}, "result": "timeout = 30",
        }])
        await on_progress("", file_edit_events=[{
            "call_id": "new", "tool": "write_file", "phase": "end", "operation": "create",
            "path": "new.py", "added": 1, "deleted": 0,
            "diff": {"text": "--- new.py\n+++ new.py\n@@ -0,0 +1 @@\n+values = [30]\n"},
        }])
        return SimpleNamespace(content="Activity complete.", metadata={})

    loop = SimpleNamespace(
        channels_config=config.channels, process_direct=process_direct,
        close_mcp=AsyncMock(), subagents=SimpleNamespace(get_running_count=lambda: 0),
    )
    monkeypatch.setattr(commands, "_load_runtime_config", lambda *_args: config)
    monkeypatch.setattr(commands, "sync_workspace_templates", lambda *_args: None)
    monkeypatch.setattr(commands, "_set_navin_logs", lambda *_args: None)
    monkeypatch.setattr(commands.AgentLoop, "from_config", lambda *_args, **_kwargs: loop)
    monkeypatch.setattr(commands, "_close_agent_subprocesses", AsyncMock())
    monkeypatch.setattr("navin.index.warmer.schedule_warm", lambda *_args: None)
    monkeypatch.setattr(commands, "consume_restart_notice_from_env", lambda: None)
    commands.agent(message="Show activity.", session_id="cli:activity", workspace=str(tmp_path), config=None, markdown=False, logs=False)
    rendered = capsys.readouterr().out
    assert "Explored" in rendered and "Read config.py" in rendered
    assert "Added new.py (+1 -0)" in rendered and "+values = [30]" in rendered
    assert "values[0] and [blue] literally" in rendered
    assert "ignored duplicate hint" not in rendered
    assert "Activity complete." in rendered
    assert "\x1b" not in rendered
