# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Noisy commands stay readable without losing diagnostics or copyable logs."""

import asyncio
import io
from unittest.mock import patch

import pytest
from rich.cells import cell_len
from rich.console import Console
from rich.text import Text
from textual.widgets import Button, Markdown, Static
from textual.widgets._markdown import MarkdownParagraph

from navin.cli.activity import ActivityPrinter
from navin.cli.markdown import ResponseMarkdown
from navin.tui.widgets import ToolCall
from navin.utils.command_output import compact_command_rows, is_git_command, output_kind
from tests.test_tui_activity import ActivityHost


def warning_output(count=40):
    return "\n".join(
        f"backend/tests/test_security.py:{index}\n"
        f"  /home/user/project/backend/tests/test_security.py:{index}: "
        "PytestUnknownMarkWarning: Unknown pytest.mark.asyncio - is this a typo? "
        " You can register custom marks to avoid this warning - for details, see "
        "https://docs.pytest.org/en/stable/how-to/mark.html\n"
        "    @pytest.mark.asyncio"
        for index in range(count)
    )


@pytest.mark.parametrize("theme,width", [("navin", 120), ("navin-light", 48)])
def test_warning_wall_collapses_and_full_output_remains_accessible(theme, width):
    async def run():
        app = ActivityHost(theme)
        output = warning_output() + "\n6 passed, 40 warnings in 1.2s\nExit code: 0"
        async with app.run_test(size=(width, 30)) as pilot:
            await app.block.tool_event("check", "exec", "start", {"command": "pytest -q"}, None, None, None)
            row = app.block.query_one(ToolCall)
            row.toggle()
            for chunk in [output[:83], output[83:]]:
                row.apply(phase="output", output=chunk)
            row.apply(phase="end", result=output)
            await pilot.pause()
            body = row.query_one(".tool-body", Static)
            preview = str(body.content)
            assert body.region.height <= 6
            assert "(x40)" in preview and "6 passed" in preview
            assert "@pytest.mark.asyncio" not in preview
            assert "Exit code: 0" in preview and "100%" in row._head_text()
            assert all(cell_len(line) <= body.size.width for line in preview.splitlines())
            more = row.query_one(".tool-more", Button)
            assert more.display
            await pilot.click(more)
            assert "@pytest.mark.asyncio" in str(body.content)
            assert output.splitlines()[1].strip() in row.copy_text()
            row.focus()
            await pilot.press("f")
            assert "@pytest.mark.asyncio" not in str(body.content)
            await app.block.finish(latency_ms=1, model=None, preset=None)
            assert "(x40)" in str(body.content)
    asyncio.run(run())


def test_failure_survives_later_log_noise_and_exit_zero_is_not_required():
    rows = [(1, "ctx", "src/app.ts(3,8): error TS2322: Type string is not assignable")]
    rows += [(index + 2, "ctx", f"processing file {index}") for index in range(100)]
    rows += [(103, "ctx", "Exit code: 2")]
    compact = compact_command_rows(rows)
    assert len(compact) == 6
    assert any(kind == "failure" and "TS2322" in line for _, kind, line in compact)
    assert compact[-1][1:] == ("failure", "Exit code: 2")


def test_live_summary_only_classifies_new_lines_and_drops_stale_cached_output():
    from navin.utils.command_output import _command_line

    rows = [(index, "ctx", f"log {index}") for index in range(5000)]
    cache = {}
    with patch("navin.utils.command_output._command_line", wraps=_command_line) as classify:
        compact_command_rows(rows, cache=cache)
        classify.reset_mock()
        rows = rows[1:] + [(5000, "ctx", "AssertionError: unexpected value")]
        compact = compact_command_rows(rows, cache=cache)
        assert classify.call_count == 1
        assert compact[-1][1:] == ("failure", "AssertionError: unexpected value")
        compact_command_rows([(1, "ctx", "6 passed")], cache=cache)
        assert len(cache) == 1


def test_finishing_a_long_turn_does_not_reformat_completed_command_output():
    async def run():
        app = ActivityHost()
        async with app.run_test(size=(100, 30)) as pilot:
            await app.block.tool_event("check", "exec", "end", {"command": "pytest"}, warning_output(), None, None)
            await pilot.pause()
            row = app.block.query_one(ToolCall)
            assert not row._command_summary_cache
            with patch("navin.tui.widgets.preview_rows") as parse:
                await app.block.finish(latency_ms=1, model=None, preset=None)
                row._refresh_body()
                parse.assert_not_called()
            assert "(x40)" in str(row.query_one(".tool-body", Static).content)
    asyncio.run(run())


@pytest.mark.parametrize("line,kind", [
    ("ℹ pass 6", "success"), ("ℹ fail 0", "success"), ("ℹ fail 2", "failure"),
    ("TSC_OK", "success"), ("2 failed, 6 passed in 1s", "failure"),
    ("6 passed, 3 warnings in 1s", "warning"), ("0 errors", "info"),
])
def test_command_status_colors_do_not_turn_zero_failures_red(line, kind):
    assert output_kind(line) == kind


@pytest.mark.parametrize("command,expected", [
    ("git status --short", True), ("cd frontend && git diff --stat", True),
    ("env LANG=C /usr/bin/git -C repo log -3", True),
    ("printf 'git status'", False), ("echo github", False),
])
def test_git_detection_handles_wrappers_without_matching_printed_text(command, expected):
    assert is_git_command("exec", {"command": command}) is expected


@pytest.mark.parametrize("name,arguments", [
    ("exec", {"command": "cd frontend && git status --short"}),
    ("git", {"action": "status"}),
])
def test_git_keeps_more_detail_than_other_commands(name, arguments):
    async def run():
        app = ActivityHost()
        output = "\n".join(f" M src/file_{index:02d}.py" for index in range(25))
        async with app.run_test(size=(100, 30)) as pilot:
            await app.block.tool_event("git", name, "end", arguments, output, None, None)
            await pilot.pause()
            row = app.block.query_one(ToolCall)
            body = str(row.query_one(".tool-body", Static).content)
            assert "file_00.py" in body and "file_15.py" in body
            assert "file_16.py" not in body
            assert "file_24.py" in row.copy_text()
    asyncio.run(run())


def test_nonzero_exit_and_background_session_have_honest_headings():
    async def run():
        app = ActivityHost()
        async with app.run_test(size=(100, 30)):
            for call, result in [("bad", "FAILED test_example\nExit code: 1"), ("running", "Session ID: abc\nProcess running")]:
                await app.block.tool_event(call, "exec", "end", {"command": "pytest"}, result, None, None)
            bad, running = app.block.query(ToolCall)
            assert bad.phase == "error" and "Failed:" in bad._head_text()
            assert "100%" not in running._head_text()
    asyncio.run(run())


def test_interactive_printer_summarizes_stream_once_but_pipe_keeps_raw_output(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    output = warning_output() + "\n6 passed, 40 warnings\nExit code: 0"
    for terminal in (True, False):
        stream = io.StringIO()
        printer = ActivityPrinter(Console(file=stream, width=100, force_terminal=terminal, color_system="truecolor" if terminal else None))
        for event in [
            {"phase": "start", "arguments": {"command": "pytest -q"}},
            {"phase": "output", "output": output},
            {"phase": "end", "result": output},
        ]:
            printer.consume(tool_events=[{"call_id": "test", "name": "exec", **event}])
        rendered = Text.from_ansi(stream.getvalue()).plain
        assert rendered.count("6 passed") == 1
        if terminal:
            assert "(x40)" in rendered and "@pytest.mark.asyncio" not in rendered
            assert len(rendered.splitlines()) < 12
            assert "\x1b[" in stream.getvalue()
        else:
            assert rendered.count("@pytest.mark.asyncio") == 40
            assert "\x1b[" not in stream.getvalue()


def test_final_response_accents_preserve_the_message_in_both_renderers(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    message = "**Terminé.**\n\n- Tests validés.\n- Fichier `src/app.ts` mis à jour."
    async def run():
        app = ActivityHost()
        async with app.run_test(size=(100, 24)) as pilot:
            await app.block.set_text(message)
            await app.block.finish(latency_ms=1, model=None, preset=None)
            await pilot.pause()
            paragraph = app.block.query_one(MarkdownParagraph)
            assert paragraph.get_component_styles("strong").text_style.bold
            assert app.block.query_one(Markdown).source == message
            assert app.block.copy_text() == message
            assert "Terminé." in app.export_screenshot()
    asyncio.run(run())
    stream = io.StringIO()
    console = Console(file=stream, force_terminal=True, color_system="standard")
    console.print(ResponseMarkdown(message))
    assert "\x1b[1;94m" in stream.getvalue()
    assert "Terminé." in Text.from_ansi(stream.getvalue()).plain
