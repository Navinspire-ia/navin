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
            # Finished: totals in the body, exit status on the outcome line;
            # the warning count is in the full output, one click away.
            assert "6 passed" in preview and "(x40)" not in preview
            assert "@pytest.mark.asyncio" not in preview
            assert "exit 0" in str(row.query_one(".tool-summary", Static).content)
            # No progress was reported: no bar on the finished row.
            assert "100%" not in row._head_text() and row._head_text().startswith("✓ ")
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
            assert "6 passed" in str(body.content)
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


@pytest.mark.parametrize("line,kind", [
    # Source code printed by grep -n / -A, cat or sed is content, not a failure.
    ("466-        except Exception:  # noqa: BLE001 - extensions never break the prompt", "ctx"),
    ("navin/agent/context.py:466:        except ValueError:", "ctx"),
    ("    except OSError:", "ctx"),
    ("# raises KeyError: when missing", "ctx"),
    # Real failures, including located compiler and type-checker errors, stay red.
    ("ValueError: invalid literal for int()", "failure"),
    ("E       AssertionError: unexpected value", "failure"),
    ("app.py:12: error: Incompatible types in assignment", "failure"),
    ("src/a.ts(3,5): error TS2322: Type 'string' is not assignable", "failure"),
])
def test_source_lines_naming_exceptions_are_not_failures(line, kind):
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
            # A failing command: a red cross, no "Failed:" label, the exit
            # code on the outcome line and its last output line.
            assert bad.phase == "error" and "Failed" not in bad._head_text()
            assert bad._head_text().startswith("× ")
            assert "exit 1 · 1 line" in str(bad.query_one(".tool-summary", Static).content)
            body = str(bad.query_one(".tool-body", Static).content)
            assert "test_example" in body and "Exit code" not in body
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
            # Head, outcome line, then the totals: the warning wall stays out.
            assert "exit 0" in rendered and "@pytest.mark.asyncio" not in rendered
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


def test_cli_marks_failed_steps_with_a_cross_but_never_dumps_errors():
    """A bad edit call, a refused git commit and a failing `ls` probe: a red
    cross and a short reason each, never the word "Failed" or a raw dump."""
    stream = io.StringIO()
    printer = ActivityPrinter(Console(file=stream, width=100, force_terminal=True))
    printer.consume(tool_events=[
        {"call_id": "patch", "name": "apply_patch", "phase": "error", "arguments": {"edits": [{}]},
         "error": "Invalid parameters for tool 'apply_patch': missing required edits[0].path"},
        {"call_id": "git", "name": "git", "phase": "error", "arguments": {"action": "commit"},
         "error": "nothing is staged."},
        {"call_id": "ls", "name": "exec", "phase": "end", "arguments": {"command": "ls .navin/tmp-note.txt"},
         "result": "ls: cannot access '.navin/tmp-note.txt': No such file or directory\nExit code: 2"},
    ])
    rendered = Text.from_ansi(stream.getvalue()).plain
    assert "Failed" not in rendered and rendered.count("×") == 3
    assert "Invalid parameters" not in rendered and "missing required" not in rendered
    assert "invalid arguments" in rendered and "nothing is staged" in rendered
    assert "Ran ls" in rendered and "exit 2" in rendered


def test_ran_rows_stay_short_and_redraws_do_not_add_noise():
    from navin.tui.widgets import ToolCall, terminal_lines
    from navin.utils.tool_hints import activity_label

    command = (
        "cd /home/a/project && TMPDIR=/tmp/x timeout 300 .venv/bin/python -m pytest "
        "tests/test_tui_full_width.py -q -p no:cacheprovider 2>&1 | tail -15"
    )
    assert activity_label("exec", {"command": command}) == (
        "Ran in project · python -m pytest tests/test_tui_full_width.py -q -p no:cacheprovider"
    )
    label = activity_label("exec", {"command": command})
    assert label.startswith("Ran ") and "\n" not in label and "[details]" not in label
    assert "/home/a/project" not in label and ".venv/bin" not in label
    assert activity_label("exec", {"command": "cat > a.py <<EOF\nx\nEOF"}).endswith("lines")

    assert terminal_lines("a\r\nb\n10%\r60%\r100%\nc\x07\x1b[2K\n") == ["a", "b", "100%", "c"]

    card = ToolCall("c1", "exec", {"command": "pytest -q"})
    card.apply(phase="output", output="....  [ 50%]\n")
    card.apply(phase="output", output="....  [100%]\n")
    # Test runner percentages are output, not a jumping progress bar.
    assert card.percent is None
    assert card.output_lines == ["....  [ 50%]", "....  [100%]"]


_STREAM_PIECES = [
    "case {n} passed\n", "partial {n} ", " {n}%\r", "\r", "\r\n", "\n", "\n\n", "   \n",
    "\x1b[31mFAILED\x1b[0m test_{n}\n", "\x1b[2K", "\x07", "(no output)\n",
    "PytestUnknownMarkWarning: Unknown pytest.mark.slow - is this a typo?\n",
    "DeprecationWarning: old api {n}\n", "AssertionError: bad {n}\n", "{n} passed, 2 warnings in 1s\n",
    "Exit code: 1\n", "a​b́ ️‍ zero {n}\n", "‍\n", "\t\x0c form {n} sep\n",
    "==== {n} ====\n", "x" * 70,
]


def _stream_chunk(rng, index, diffs):
    roll = rng.random()
    if diffs and roll < 0.03:
        return "@@ -1,2 +1,2 @@\n"
    if roll < 0.1:
        # Past MAX_TRANSCRIPT_CHARS the tail keeps the head of one line.
        return "y" * rng.randint(40_000, 120_000) + rng.choice(["\n", " ", "\r"])
    return "".join(rng.choice(_STREAM_PIECES).format(n=index + offset) for offset in range(rng.randint(1, 6)))


@pytest.mark.parametrize("seed", range(8))
def test_incremental_live_preview_matches_a_full_recompute(seed, monkeypatch):
    """Random appends (progress redraws, ANSI, long and zero-width lines,
    trimming): the cached rows, summary and retained lines equal the
    from-scratch pass after every chunk."""
    import random

    import navin.tui.widgets as widgets
    from navin.tui.widgets import terminal_lines
    from navin.utils.command_output import CompactRowsCache
    from navin.utils.tool_hints import StreamRowsCache, preview_rows

    rng = random.Random(seed)
    monkeypatch.setattr(widgets, "MAX_TRANSCRIPT_LINES", rng.choice([25, 60, 5000]))
    name, arguments = rng.choice([("exec", {"command": "pytest -q"}), ("test_run", {}), ("exec", {"command": "git log"})])
    card = ToolCall("live", name, arguments)
    rows_cache, summary_cache = StreamRowsCache(), CompactRowsCache()
    raw = ""
    for index in range(120):
        chunk = _stream_chunk(rng, index, diffs=seed % 4 == 3)
        raw += chunk
        card.apply(phase="output", output=chunk)
        if rng.random() < 0.3:
            continue  # several chunks between two paints
        lines = card.output_lines
        assert lines == terminal_lines(raw)[-widgets.MAX_TRANSCRIPT_LINES:]
        expected = preview_rows(name, arguments, output_lines=lines, include_run_output=True)
        rows = preview_rows(name, arguments, output_lines=lines, include_run_output=True, cache=rows_cache)
        assert rows == expected
        limit = rng.choice([3, 6, 16])
        assert compact_command_rows(rows, limit=limit, cache=summary_cache) == compact_command_rows(expected, limit=limit)


def test_incremental_preview_recovers_from_rewritten_history():
    """Not an append (a snapshot, a restored card, an edited middle line):
    the caches fall back to the full pass and still agree with it."""
    import random

    from navin.utils.command_output import CompactRowsCache
    from navin.utils.tool_hints import StreamRowsCache, preview_rows

    rng = random.Random(7)
    lines = [f"line {index}" for index in range(40)]
    rows_cache, summary_cache = StreamRowsCache(), CompactRowsCache()
    for step in range(300):
        action = rng.random()
        if action < 0.4:
            lines = lines + [rng.choice(["ok", "FAILED x", "UserWarning: w", "", "6 passed", f"n {step}", "ok"])]
        elif action < 0.55:
            lines = lines[rng.randint(1, 5):]
        elif action < 0.7 and lines:
            lines = lines[:-1] + [lines[-1] + " more"]
        elif action < 0.8 and lines:
            middle = rng.randrange(len(lines))
            lines = lines[:middle] + ["UserWarning: changed"] + lines[middle + 1:]
        elif action < 0.85:
            lines = []
        else:
            lines = lines[:rng.randint(0, len(lines))]
        expected = preview_rows("exec", {"command": "make"}, output_lines=lines, include_run_output=True)
        rows = preview_rows("exec", {"command": "make"}, output_lines=lines, include_run_output=True, cache=rows_cache)
        assert rows == expected
        assert compact_command_rows(rows, cache=summary_cache) == compact_command_rows(expected)


def test_live_paint_filters_and_classifies_only_new_lines():
    from navin.utils import command_output, tool_hints
    from navin.utils.command_output import CompactRowsCache
    from navin.utils.tool_hints import StreamRowsCache, preview_rows

    card = ToolCall("live", "exec", {"command": "pytest"})
    card.apply(phase="output", output="".join(f"case {index} passed\n" for index in range(5000)))
    rows_cache, summary_cache = StreamRowsCache(), CompactRowsCache()

    def paint():
        rows = preview_rows("exec", {"command": "pytest"}, output_lines=card.output_lines,
                            include_run_output=True, cache=rows_cache)
        return compact_command_rows(rows, cache=summary_cache)

    paint()
    with patch.object(tool_hints, "_stream_line_rows", wraps=tool_hints._stream_line_rows) as split, \
            patch.object(command_output, "_command_line", wraps=command_output._command_line) as classify:
        card.apply(phase="output", output="AssertionError: unexpected value\n")
        summary = paint()
    assert split.call_count == 1 and classify.call_count == 1
    assert summary[-1][1:] == ("failure", "AssertionError: unexpected value")
    assert len(card.output_lines) == 5000


def test_incremental_summary_matches_full_pass_with_new_leading_rows():
    """A finished command puts its error above the streamed log: the
    summary cache takes the new leading rows without reclassifying it."""
    import random

    from navin.utils.command_output import CompactRowsCache

    rng = random.Random(11)
    texts = ["ok", "FAILED t", "UserWarning: w", "DeprecationWarning: d", "", "6 passed", "Exit code: 1", "noise"]
    rows = [(index, "ctx", rng.choice(texts)) for index in range(30)]
    cache = CompactRowsCache()
    for _ in range(400):
        action = rng.random()
        if action < 0.35:
            rows = rows + [(None, "ctx", rng.choice(texts))]
        elif action < 0.5:
            rows = [(None, rng.choice(["error", "ctx"]), rng.choice(texts)) for _ in range(rng.randint(1, 4))] + rows
        elif action < 0.65:
            rows = rows[rng.randint(1, 4):]
        elif action < 0.75 and rows:
            rows = rows[:-1] + [(None, "ctx", rng.choice(texts))]
        elif action < 0.8:
            rows = []
        else:
            rows = rows[:rng.randint(0, len(rows))]
        limit = rng.choice([2, 6])
        assert compact_command_rows(rows, limit=limit, cache=cache) == compact_command_rows(rows, limit=limit)


def test_cancelling_a_long_live_command_does_not_reclassify_its_log():
    from navin.utils import command_output

    async def run():
        app = ActivityHost()
        async with app.run_test(size=(100, 30)) as pilot:
            await app.block.tool_event("live", "exec", "start", {"command": "pytest"}, None, None, None)
            row = app.block.query_one(ToolCall)
            row.apply(phase="output", output="".join(f"case {index} passed\n" for index in range(3000)))
            await pilot.pause(0.3)
            row._refresh_body()
            assert row._command_summary_cache is not None and len(row._command_summary_cache) == 3000
            with patch.object(command_output, "_command_line", wraps=command_output._command_line) as classify:
                row.apply(phase="cancelled", error="Interrupted before a result was received.")
            assert classify.call_count < 10
            assert row._command_summary_cache is None and row._stream_rows_cache is None
    asyncio.run(run())
