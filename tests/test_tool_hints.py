# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

from __future__ import annotations

import unittest
from types import SimpleNamespace

from navin.utils.tool_hints import (
    PREVIEW_ADD_BG,
    PREVIEW_CTX_BG,
    PREVIEW_DEL_BG,
    activity_label,
    clip_transcript,
    describe_explore_step,
    describe_tool_headline,
    describe_tool_line,
    edit_group_key,
    exec_flags,
    extract_line_diff,
    format_preview_markup_line,
    format_seconds,
    format_tool_detail,
    format_tool_hints,
    format_tool_preview_markup,
    format_turn_summary,
    humanize_shell_command,
    preview_rows,
    strip_session_notes,
    tool_cluster_kind,
    tool_verb,
)


class HumanizeShellTests(unittest.TestCase):
    def test_python_script_in_project_dir(self) -> None:
        text = humanize_shell_command(
            "cd ~/projects/deploy7/db-migration && .venv/bin/python migration-v2.py"
        )
        self.assertIn("in db-migration", text)
        self.assertIn("python migration-v2.py", text)
        self.assertNotIn("command=", text)
        self.assertNotIn("background=", text)

    def test_wait_then_tail_log(self) -> None:
        text = humanize_shell_command(
            "sleep 120 && tail -5 ~/projects/deploy7/db-migration/migration-v2-run2.log"
        )
        self.assertIn("wait 2 min", text)
        self.assertIn("tail -5", text)
        self.assertIn("migration-v2-run2.log", text)

    def test_sleep_semicolon_tail(self) -> None:
        text = humanize_shell_command(
            "sleep 240; tail -3 ~/projects/deploy7/db-migration/migration-v2.log"
        )
        self.assertIn("wait 4 min", text)
        self.assertIn("tail -3", text)


class ExecFlagsTests(unittest.TestCase):
    def test_hides_raw_kwargs(self) -> None:
        self.assertEqual(exec_flags({"background": True, "timeout": 300}), "background · 5 min")
        self.assertEqual(format_seconds(30), "30s")
        self.assertEqual(exec_flags({"command": "ls"}), "")


class DescribeHeadlineTests(unittest.TestCase):
    def test_exec_drops_tool_name_and_key_value_dump(self) -> None:
        title, flags = describe_tool_headline(
            "exec",
            {
                "command": "cd ~/projects/deploy7/db-migration && .venv/bin/python migration-v2.py",
                "background": True,
            },
        )
        self.assertIn("python migration-v2.py", title)
        self.assertEqual(flags, "background")
        self.assertNotIn("exec", title)
        self.assertNotIn("command=", title)

    def test_list_dir_is_ls(self) -> None:
        title, flags = describe_tool_headline("list_dir", {"path": "."})
        self.assertEqual(title, "ls .")
        self.assertEqual(flags, "")

    def test_format_tool_hints_exec(self) -> None:
        hint = format_tool_hints(
            [
                SimpleNamespace(
                    name="exec",
                    arguments={
                        "command": "sleep 30 && tail -5 /tmp/run.log",
                        "timeout": 30,
                    },
                )
            ]
        )
        self.assertTrue(hint.startswith("$ "))
        self.assertIn("wait 30s", hint)
        self.assertIn("tail -5", hint)
        self.assertIn("30s", hint)
        self.assertNotIn("command=", hint)
        self.assertNotIn("timeout=", hint)


class ExploreClusterTests(unittest.TestCase):
    def test_kind_splits_explore_from_edits_and_runs(self) -> None:
        self.assertEqual(tool_cluster_kind("read_file"), "explore")
        self.assertEqual(tool_cluster_kind("grep"), "explore")
        self.assertEqual(tool_cluster_kind("list_dir"), "explore")
        self.assertEqual(tool_cluster_kind("metagraph"), "explore")
        self.assertEqual(tool_cluster_kind("code_index"), "explore")
        self.assertEqual(tool_cluster_kind("edit_file"), "edit")
        self.assertEqual(tool_cluster_kind("exec"), "")

    def test_index_tools_show_as_explored_steps(self) -> None:
        self.assertEqual(tool_verb("metagraph"), "graph")
        self.assertEqual(tool_verb("code_index"), "index")
        self.assertEqual(
            describe_explore_step(
                "metagraph", {"action": "find", "query": "checkpoint", "kind": "back"},
            ),
            "Graph checkpoint",
        )
        self.assertEqual(
            describe_explore_step(
                "code_index", {"action": "references", "name": "AgentLoop"},
            ),
            "Index AgentLoop",
        )

    def test_run_journal_hidden_except_git(self) -> None:
        # Non-git command runs show the head only, no output journal.
        rows = preview_rows(
            "exec", {"command": "pytest tests/test_tool_hints.py"},
            result="5 passed in 1.2s",
        )
        self.assertEqual(rows, [])
        # Git commands keep their output journal.
        rows = preview_rows(
            "exec", {"command": "git status"},
            result="On branch prod-v2\nnothing to commit",
        )
        self.assertEqual(
            [row[2] for row in rows],
            ["On branch prod-v2", "nothing to commit"],
        )
        # Failures stay visible whatever the command.
        rows = preview_rows(
            "exec", {"command": "pytest -q"}, error="boom: 1 failed",
        )
        self.assertEqual([row[1] for row in rows], ["error"])

    def test_metagraph_result_previews_as_compact_graph(self) -> None:
        rows = preview_rows(
            "metagraph",
            {"action": "path", "from": "a.py", "to": "c.py"},
            result=(
                "Path (2 hop(s)):\n"
                "  a.py\n"
                "  b.py\n"
                "  c.py"
            ),
        )
        self.assertEqual(rows, [(None, "ctx", "a.py → b.py → c.py")])

    def test_metagraph_hubs_preview_stays_compact(self) -> None:
        rows = preview_rows(
            "metagraph",
            {"action": "hubs"},
            result=(
                "Main hubs (most connected):\n"
                "  navin/tui/app.py [back] (in=40, out=12)\n"
                "  navin/agent/loop.py [back] (in=33, out=9)"
            ),
        )
        self.assertEqual(
            [row[2] for row in rows],
            [
                "Main hubs (most connected):",
                "navin/tui/app.py [back] (in=40, out=12)",
                "navin/agent/loop.py [back] (in=33, out=9)",
            ],
        )

    def test_steps_look_like_cursor(self) -> None:
        self.assertEqual(
            describe_explore_step("read_file", {"path": "webui/src/hooks/useAccount.ts"}),
            "Read useAccount.ts",
        )
        self.assertEqual(
            describe_explore_step(
                "grep",
                {
                    "pattern": "export class NavinClient",
                    "path": "webui/src/lib/navin-client.ts",
                },
            ),
            "Search export class NavinClient in navin-client.ts",
        )
        self.assertEqual(describe_explore_step("list_dir", {"path": "."}), "List .")


class QuietToolLineTests(unittest.TestCase):
    def test_done_row_keeps_the_file_on_the_same_line(self) -> None:
        self.assertEqual(tool_verb("read_file"), "read")
        self.assertEqual(tool_verb("edit_file"), "edit")
        self.assertEqual(tool_verb("write_file"), "create")
        self.assertEqual(
            describe_tool_line(
                "read_file",
                {"path": "~/projects/deploy7/db-migration/migrate_main_to_rel_v2.py"},
                done=True,
            ),
            "read  migrate_main_to_rel_v2.py",
        )

    def test_running_row_keeps_the_file_name_only(self) -> None:
        self.assertEqual(
            describe_tool_line(
                "read_file",
                {"path": "~/projects/deploy7/db-migration/migrate_main_to_rel_v2.py"},
                done=False,
            ),
            "read  migrate_main_to_rel_v2.py",
        )

    def test_detail_is_not_json(self) -> None:
        text = format_tool_detail(
            "ask_user",
            {
                "question": "On continue ?",
                "options": [{"id": "a", "label": "Oui"}],
            },
        )
        self.assertEqual(text, "On continue ?")
        self.assertNotIn("{", text)
        self.assertNotIn("args:", text)
        self.assertNotIn('"question"', text)

        grep = format_tool_detail(
            "grep",
            {"path": "~/projects/deploy7/db-migration/migrate_main_to_rel_v2.py", "pattern": "TODO"},
            result={"matches": [1, 2, 3]},
        )
        self.assertIn("migrate_main_to_rel_v2.py", grep)
        self.assertIn("TODO", grep)
        self.assertNotIn("{", grep)

        run = describe_tool_line(
            "exec",
            {"command": "cd ~/projects/deploy7/db-migration && python migrate.py"},
        )
        self.assertTrue(run.startswith("run  "))
        self.assertIn("python migrate.py", run)
        self.assertNotIn("<<", run)
        self.assertNotEqual(describe_tool_line("exec", {"command": ""}), "run  \"")
        self.assertNotEqual(describe_tool_line("exec", {"command": "\""}), "run  \"")
        empty = describe_tool_line("exec", {"command": "   "})
        self.assertTrue(empty.startswith("run"))
        self.assertNotIn('run  "', empty)
        quoted = describe_tool_line("exec", {"command": "cd ~/db-migration && :"})
        self.assertIn("run", quoted)
        self.assertGreater(len(quoted), 4)

        # Non-git runs hide the output journal; git runs keep it.
        detail = format_tool_detail(
            "exec",
            {"command": "pytest -q"},
            result="PASS - no lint errors, tests green\n2 passed",
        )
        self.assertEqual(detail, "")
        long_out = "\n".join(f"line {i} ALTER TABLE users" for i in range(40))
        full = format_tool_detail(
            "exec",
            {"command": "psql -f migrate.sql"},
            result=long_out,
        )
        self.assertEqual(full, "")
        git_log = format_tool_detail(
            "exec", {"command": "git log --oneline -2"}, result="abc123 fix\ndef456 feat",
        )
        self.assertIn("abc123 fix", git_log)

    def test_edit_line_shows_plus_and_minus(self) -> None:
        line = describe_tool_line(
            "edit_file",
            {"path": "migrate_main_to_rel_v2.py"},
            added=38,
            removed=14,
        )
        self.assertEqual(line, "edit  +38 -14  migrate_main_to_rel_v2.py")
        self.assertEqual(
            describe_tool_line(
                "edit_file",
                {"path": "tests/test_tool_parameter_recovery.py"},
                added=44,
                removed=4,
            ),
            "edit  +44 -4  tests/test_tool_parameter_recovery.py",
        )
        self.assertEqual(extract_line_diff(" - replace foo.py (+38/-14)"), (38, 14))
        self.assertEqual(extract_line_diff({"added": 12, "deleted": 3}), (12, 3))
        self.assertEqual(
            format_turn_summary(
                [
                    ("edit_file", 38, 14),
                    ("edit_file", 0, 0),
                    ("write_file", 0, 0),
                    ("apply_patch", 0, 0),
                    ("read_file", 0, 0),
                    ("exec", 0, 0),
                ]
            ),
            "Edited 4 files, explored 1 file, ran 1 command +38 -14",
        )

    def test_run_matches_edit_line_shape(self) -> None:
        heredoc = ".venv/bin/python - <<'PY'\nimport json\nfrom pathlib import Path\nprint(1)\nPY\n"
        line = describe_tool_line("exec", {"command": heredoc})
        self.assertEqual(line, "run  python")
        self.assertEqual(
            describe_tool_line("exec", {"command": "git status --short"}),
            "run  git status",
        )
        self.assertEqual(
            describe_tool_line(
                "exec",
                {"command": "pytest tests/test_ask_mode.py tests/test_plan_mode.py -q"},
            ),
            "run  pytest 2 files",
        )
        empty = format_tool_detail("exec", {"command": "true"}, result="(no output)")
        self.assertEqual(empty, "")
        script = format_tool_detail("exec", {"command": heredoc}, result="(no output)")
        self.assertEqual(script, "")
        self.assertNotIn("(no output)", format_tool_detail("exec", {"command": "git status"}, result="(no output)"))
        diff = format_tool_detail(
            "exec",
            {"command": "git diff"},
            result=(
                "@@ -8,2 +8,3 @@\n"
                " import asyncio\n"
                "+import queue\n"
                " from pathlib import Path\n"
            ),
        )
        self.assertIn("   8  import asyncio", diff)
        self.assertIn("   9 +import queue", diff)
        file_diff = format_tool_detail(
            "edit_file",
            {"path": "tests/test_tool_parameter_recovery.py"},
            diff_text=(
                "@@ -8,2 +8,3 @@\n"
                " import asyncio\n"
                "+import queue\n"
                " from pathlib import Path\n"
            ),
        )
        self.assertIn("   8  import asyncio", file_diff)
        self.assertIn("   9 +import queue", file_diff)
        self.assertNotIn("test_tool_parameter_recovery.py", file_diff.splitlines()[0])
        painted = format_preview_markup_line(9, "add", "import queue", width=24)
        self.assertIn(f"on {PREVIEW_ADD_BG}", painted)
        from rich.text import Text

        self.assertIn("9 +import queue", Text.from_markup(painted).plain)
        self.assertIn(f"on {PREVIEW_DEL_BG}", format_preview_markup_line(8, "del", "old"))
        self.assertIn(f"on {PREVIEW_CTX_BG}", format_preview_markup_line(8, "ctx", "keep"))
        colored = format_tool_preview_markup(
            "edit_file",
            {"path": "foo.py"},
            diff_text="@@ -8,1 +8,2 @@\n-old\n+new\n",
        )
        self.assertIn(f"on {PREVIEW_ADD_BG}", colored)
        self.assertIn(f"on {PREVIEW_DEL_BG}", colored)
        self.assertIn("8 -old", Text.from_markup(colored).plain)
        self.assertIn("8 +new", Text.from_markup(colored).plain)

    def test_run_keeps_the_real_command(self) -> None:
        line = describe_tool_line(
            "exec",
            {
                "command": (
                    'grep -n "pg_restore: error" "$LOGFILE" | grep -v already '
                    "| tail -20"
                )
            },
        )
        self.assertTrue(line.startswith("run  "))
        self.assertIn("pg_restore: error", line)
        self.assertIn("$LOGFILE", line)
        self.assertNotEqual(line.strip(), 'run  "')

    def test_same_file_edits_share_one_key(self) -> None:
        first = edit_group_key("edit_file", {"path": "migrate_main_to_rel_v2.py"})
        other = edit_group_key("read_file", {"path": "migrate_main_to_rel_v2.py"})
        self.assertTrue(first)
        self.assertEqual(
            first,
            edit_group_key("edit", {"file_path": "./migrate_main_to_rel_v2.py"}),
        )
        self.assertNotEqual(first, other)

    def test_clip_keeps_5000_lines_then_stops(self) -> None:
        blob = "\n".join(f"line {i}" for i in range(5200))
        out = clip_transcript(blob)
        self.assertIn("line 0", out)
        self.assertIn("line 4999", out)
        self.assertNotIn("line 5199", out)
        self.assertIn("200 more lines", out)


if __name__ == "__main__":
    unittest.main()


def test_run_output_statuses_get_colors() -> None:
    from rich.text import Text

    from navin.utils.tool_hints import activity_palette, highlight_output_statuses

    line = "tests/test_a.py::T::test_x PASSED [ 18%]\ntests/test_b.py::test_y FAILED\n1 skipped"
    rendered = highlight_output_statuses(Text(line))
    palette = activity_palette(True)

    def style_of(word: str) -> str:
        start = line.index(word)
        return " ".join(str(s.style) for s in rendered.spans if s.start <= start < s.end)

    assert palette["green"] in style_of("PASSED")
    assert palette["coral"] in style_of("FAILED")
    assert palette["mustard"] in style_of("skipped")
    assert palette["blue"] in style_of("tests/test_a.py")
    assert palette["muted"] in style_of("[ 18%]")
    assert rendered.plain == line


def test_huge_single_line_preview_is_cut_instead_of_freezing_the_terminal():
    """A 200 kB minified line took seconds to wrap and highlight on the UI thread."""
    import time

    from navin.utils.tool_hints import PREVIEW_MAX_WRAPPED_ROWS, format_tool_preview_markup

    line = '{"k":"' + "x" * 200_000 + '"}'
    start = time.monotonic()
    markup = format_tool_preview_markup(
        "exec", {"command": "cat a.json"}, rows=[(1, "ctx", line)], width=100, include_run_output=True,
    )
    assert time.monotonic() - start < 1.0
    assert len(markup.splitlines()) <= PREVIEW_MAX_WRAPPED_ROWS + 1
    assert "..." in markup
    short = format_tool_preview_markup("exec", {"command": "x"}, rows=[(1, "ctx", "ok done")], width=100)
    assert "ok done" in short and "..." not in short


def test_heredoc_command_labels_never_recurse_forever():
    from navin.utils.tool_hints import _command_label, short_run_target

    for command in ("<<EOF\nx\nEOF", "rg foo <<EOF\nEOF", "grep x << y", "<< "):
        assert isinstance(short_run_target(command), str)
        assert isinstance(_command_label(command), str)


def test_leading_zero_width_text_does_not_freeze_width_helpers():
    """Regression: rich 14 split_graphemes looped forever on a leading
    zero-width character and froze the whole CLI (10000s+ event loop stall)."""
    import os
    import threading

    from rich.cells import cell_len, chop_cells, set_cell_size
    from rich.console import Console
    from rich.text import Text

    from navin.utils.tool_hints import format_preview_markup_line

    # A daemon thread with a join timeout works on every OS and runner
    # (signal.alarm is POSIX only, sys.executable may be a frozen binary):
    # a hung rich call leaves the thread alive and fails the test.
    outcome: dict[str, object] = {}

    def exercise() -> None:
        try:
            console = Console(width=30, file=open(os.devnull, "w", encoding="utf-8"))
            for text in ["\u200babcdefgh", "\ufe0fabc", "\u0301", "\u200d\u200dx"]:
                set_cell_size(text, 3)
                chop_cells(text, 2)
                cell_len(text)
                for width in (1, 2, 5):
                    Text(text).wrap(console, width)
                    console.print(Text(text), width=width)
            outcome["line"] = format_preview_markup_line(1, "ctx", "\u200bhello", width=20)
        except BaseException as exc:  # surfaced by the assertions below
            outcome["error"] = exc

    worker = threading.Thread(target=exercise, daemon=True)
    worker.start()
    worker.join(timeout=15)
    assert not worker.is_alive(), "rich width helpers hung on zero-width text"
    assert "error" not in outcome, outcome.get("error")
    assert "hello" in str(outcome["line"])


def test_pathological_tool_output_paints_in_bounded_time():
    """One paint must stay bounded whatever the tool printed: a multi-MB line,
    zero-width runs, leading U+200B / U+0301 / U+FE0F, a trailing ZWJ and 50k
    lines used to hold the UI thread for tens of seconds."""
    import threading
    import time

    from rich.text import Text

    from navin.utils.tool_hints import (
        MAX_TRANSCRIPT_LINES,
        PREVIEW_MAX_PAINT_ROWS,
        format_tool_preview_markup,
        preview_rows,
    )

    lines = ["​́️" + "x​́" * 2000 + f" {index}‍" for index in range(50_000)]
    lines[0] = "​" * 100_000 + "a" * 1_000_000 + "‍"
    lines[-1] = "​́️" + "​" * 50_000 + "‍"
    outcome: dict[str, object] = {}

    def exercise() -> None:
        try:
            started = time.perf_counter()
            rows = preview_rows(
                "exec", {"command": "cat blob"}, output_lines=lines,
                limit=MAX_TRANSCRIPT_LINES, include_run_output=True,
            )
            full = format_tool_preview_markup(
                "exec", {"command": "cat blob"}, rows=rows, limit=MAX_TRANSCRIPT_LINES,
                width=200, include_run_output=True,
            )
            outcome["streamed"] = time.perf_counter() - started
            short = [(index, "ctx", "​́️x​́" * 8 + "‍") for index in range(50_000)]
            started = time.perf_counter()
            direct = format_tool_preview_markup(
                "exec", {"command": "x"}, rows=short, limit=len(short), width=120, include_run_output=True,
            )
            outcome["direct"] = time.perf_counter() - started
            Text.from_markup(full)
            outcome["direct_rows"] = direct.count("\n") + 1
            outcome["tail"] = direct.splitlines()[-1]
        except BaseException as exc:  # surfaced by the assertions below
            outcome["error"] = exc

    worker = threading.Thread(target=exercise, daemon=True)
    worker.start()
    worker.join(timeout=30)
    assert not worker.is_alive(), "preview painting hung on pathological output"
    assert "error" not in outcome, outcome.get("error")
    assert outcome["streamed"] < 1.0, outcome
    assert outcome["direct"] < 1.0, outcome
    assert outcome["direct_rows"] <= PREVIEW_MAX_PAINT_ROWS + 1
    assert "not shown" in str(outcome["tail"])


def test_a_running_command_follow_up_reads_as_what_was_done_not_write_stdin():
    poll = {"session_id": "exec-1", "chars": "", "yield_time_ms": 5000}
    assert activity_label("write_stdin", poll, phase="start") == "Checking running command"
    assert activity_label("write_stdin", poll) == "Checked running command"
    assert activity_label("write_stdin", {"session_id": "exec-1", "chars": "y\n"}) == "Sent input to running command"
    assert activity_label("write_stdin", {"session_id": "exec-1", "terminate": True}) == "Stopped running command"
    assert activity_label("list_exec_sessions", {}) == "Listed running commands"
    assert "stdin" not in activity_label("write_stdin", poll).lower()


def test_session_bookkeeping_never_reaches_the_command_preview():
    result = (
        "compiling\nSTDERR:\nwarning: unused\nProcess running. session_id: exec-1\n"
        "You will be told when it finishes; keep working meanwhile, or write_stdin with "
        "yield_time_ms to wait for it.\nElapsed: 3.1s"
    )
    shown = [text for _n, _k, text in preview_rows("write_stdin", {"session_id": "exec-1"}, result=result, limit=10)]
    assert shown == ["compiling", "warning: unused"]
    assert strip_session_notes("(no output yet)\nElapsed: 0.5s") == ""
    # The command's own lines stay, even when they look alike.
    assert strip_session_notes("STDERR: is a word here\nExit code: 1") == "STDERR: is a word here\nExit code: 1"
