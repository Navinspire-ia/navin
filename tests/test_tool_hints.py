# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

from __future__ import annotations

import unittest
from types import SimpleNamespace

from navin.utils.tool_hints import (
    clip_transcript,
    describe_tool_headline,
    describe_tool_line,
    edit_group_key,
    exec_flags,
    extract_line_diff,
    format_seconds,
    format_tool_detail,
    format_tool_hints,
    format_turn_summary,
    humanize_shell_command,
    tool_target,
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
        self.assertNotEqual(describe_tool_line("exec", {"command": ""}), "run  \"")
        self.assertNotEqual(describe_tool_line("exec", {"command": "\""}), "run  \"")
        empty = describe_tool_line("exec", {"command": "   "})
        self.assertTrue(empty.startswith("run"))
        self.assertNotIn('run  "', empty)
        quoted = describe_tool_line("exec", {"command": "cd ~/db-migration && :"})
        self.assertIn("run", quoted)
        self.assertGreater(len(quoted), 4)

        detail = format_tool_detail(
            "exec",
            {"command": "pytest -q"},
            result="PASS - no lint errors, tests green\n2 passed",
        )
        self.assertIn("pytest", detail)
        self.assertIn("PASS", detail)
        self.assertIn("2 passed", detail)

        long_out = "\n".join(f"line {i} ALTER TABLE users" for i in range(40))
        full = format_tool_detail(
            "exec",
            {"command": "psql -f migrate.sql"},
            result=long_out,
        )
        self.assertIn("line 0 ALTER TABLE users", full)
        self.assertIn("line 39 ALTER TABLE users", full)
        self.assertIn("psql -f migrate.sql", full)

    def test_edit_line_shows_plus_and_minus(self) -> None:
        line = describe_tool_line(
            "edit_file",
            {"path": "migrate_main_to_rel_v2.py"},
            added=38,
            removed=14,
        )
        self.assertEqual(line, "edit  +38 -14  migrate_main_to_rel_v2.py")
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
