# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for the default write path: apply_patch, review, checkpoints.

These three carry every edit the agent makes and every way a user takes one
back, so the cases that matter are the unhappy ones: a patch that fails
halfway must leave no file half-written, a rejected change must return the
file to exactly what it was, and a restore must not depend on which
checkpoint happened to record the file.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import tempfile
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from navin.agent.checkpoints import (
    CheckpointError,
    CheckpointStore,
    TurnRecorder,
    bind_checkpoint_recorder,
    reset_checkpoint_recorder,
)
from navin.agent.review import PendingReviewStore
from navin.agent.tools import browser as browser_mod
from navin.agent.tools.apply_patch import ApplyPatchTool
from navin.agent.tools.exec_session import ExecSessionManager
from navin.agent.tools.file_state import FileStates
from navin.agent.tools.filesystem import EditFileTool, WriteFileTool
from navin.cli import commands as cli_commands
from navin.session.manager import Session


def _run(coro):
    return asyncio.run(coro)


def _write(root: Path, rel: str, body: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body.encode("utf-8"))
    return path


def _fail_once_on(name: str, message: str = "disk full"):
    """Break the first write to `name`, then get out of the way.

    The rollback restores backups through the same `write_bytes`, so a fake that
    kept raising would break the recovery it is meant to observe.
    """
    real = Path.write_bytes
    fired = False

    def write_bytes(self_path, *args, **kwargs):
        nonlocal fired
        if not fired and self_path.name == name:
            fired = True
            raise OSError(message)
        return real(self_path, *args, **kwargs)

    return write_bytes


class _WorkspaceTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)


# ---------------------------------------------------------------------------
# apply_patch
# ---------------------------------------------------------------------------


class ExecSessionShutdownTest(unittest.TestCase):
    """Live sessions must be killed before the loop closes.

    A surviving subprocess transport makes the garbage collector print an
    "Event loop is closed" traceback after the agent's last word.
    """

    def test_shutdown_kills_live_sessions(self) -> None:
        async def scenario() -> tuple[int, int, int]:
            manager = ExecSessionManager()
            await manager.start(
                command="cat",
                cwd=tempfile.gettempdir(),
                env=dict(os.environ),
                timeout=60,
                shell_program=None,
                login=False,
                yield_time_ms=200,
                max_output_chars=2000,
            )
            before = len(await manager.list())
            killed = await manager.shutdown()
            return before, killed, len(await manager.list())

        before, killed, after = asyncio.run(scenario())
        self.assertEqual(before, 1)
        self.assertEqual(killed, 1)
        self.assertEqual(after, 0)

    def test_shutdown_is_safe_with_nothing_running(self) -> None:
        self.assertEqual(asyncio.run(ExecSessionManager().shutdown()), 0)


class BrowserShutdownTest(unittest.TestCase):
    """Browser sessions the agent never closed must be closed for it.

    Uses a stand-in session so the test does not launch Chromium.
    """

    def setUp(self) -> None:
        self._saved = dict(browser_mod._SESSIONS)
        browser_mod._SESSIONS.clear()
        self.addCleanup(lambda: browser_mod._SESSIONS.update(self._saved))

    def test_open_sessions_are_closed(self) -> None:
        closed: list[str] = []

        class _Fake:
            def __init__(self, name: str) -> None:
                self.name = name

            async def close(self) -> None:
                closed.append(self.name)

        browser_mod._SESSIONS["a"] = _Fake("a")
        browser_mod._SESSIONS["b"] = _Fake("b")
        count = asyncio.run(browser_mod.shutdown_browser_sessions())
        self.assertEqual(count, 2)
        self.assertEqual(sorted(closed), ["a", "b"])
        self.assertEqual(browser_mod._SESSIONS, {})

    def test_a_failing_close_does_not_stop_the_others(self) -> None:
        closed: list[str] = []

        class _Broken:
            async def close(self) -> None:
                raise RuntimeError("driver already gone")

        class _Fine:
            async def close(self) -> None:
                closed.append("fine")

        browser_mod._SESSIONS["broken"] = _Broken()
        browser_mod._SESSIONS["fine"] = _Fine()
        self.assertEqual(asyncio.run(browser_mod.shutdown_browser_sessions()), 2)
        self.assertEqual(closed, ["fine"])

    def test_shutdown_is_safe_with_nothing_open(self) -> None:
        self.assertEqual(asyncio.run(browser_mod.shutdown_browser_sessions()), 0)


class OrphanedSubagentNoticeTest(unittest.TestCase):
    """A one-shot run must say when a subagent's report can never arrive."""

    class _Loop:
        def __init__(self, running: int) -> None:
            self.subagents = mock.Mock()
            self.subagents.get_running_count.return_value = running

    def _notice(self, loop) -> str:
        with mock.patch.object(cli_commands, "console") as fake:
            cli_commands._warn_about_orphaned_subagents(loop)
        return " ".join(str(call.args[0]) for call in fake.print.call_args_list)

    def test_running_subagents_are_reported(self) -> None:
        self.assertIn("2 subagent(s)", self._notice(self._Loop(2)))

    def test_nothing_is_printed_when_none_are_running(self) -> None:
        self.assertEqual(self._notice(self._Loop(0)), "")

    def test_a_broken_manager_does_not_break_shutdown(self) -> None:
        loop = mock.Mock()
        loop.subagents.get_running_count.side_effect = RuntimeError("gone")
        self.assertEqual(self._notice(loop), "")


class ApplyPatchTest(_WorkspaceTest):
    def setUp(self) -> None:
        super().setUp()
        self.states = FileStates()
        self.tool = ApplyPatchTool(workspace=self.root, file_states=self.states)

    def _apply(self, edits: list[dict], **kwargs) -> str:
        return _run(self.tool.execute(edits=edits, **kwargs))

    def _read(self, rel: str) -> str:
        return (self.root / rel).read_text(encoding="utf-8")

    def test_add_creates_a_new_file(self) -> None:
        out = self._apply([{"path": "a.py", "action": "add", "new_text": "x = 1\n"}])
        self.assertIn("Patch applied", out)
        self.assertIn("- add a.py", out)
        self.assertEqual(self._read("a.py"), "x = 1\n")

    def test_add_creates_missing_parent_directories(self) -> None:
        self._apply([{"path": "deep/nested/a.py", "action": "add", "new_text": "x = 1\n"}])
        self.assertTrue((self.root / "deep/nested/a.py").is_file())

    def test_add_appends_to_an_existing_file(self) -> None:
        _write(self.root, "a.py", "first\n")
        self.states.record_read(self.root / "a.py")
        out = self._apply([{"path": "a.py", "action": "add", "new_text": "second\n"}])
        self.assertIn("- update a.py", out)
        self.assertEqual(self._read("a.py"), "first\nsecond\n")

    def test_replace_swaps_the_matched_text(self) -> None:
        _write(self.root, "a.py", "value = 1\nother = 2\n")
        self.states.record_read(self.root / "a.py")
        self._apply([
            {"path": "a.py", "action": "replace", "old_text": "value = 1", "new_text": "value = 9"},
        ])
        self.assertEqual(self._read("a.py"), "value = 9\nother = 2\n")

    def test_replace_with_empty_new_text_deletes_the_lines(self) -> None:
        _write(self.root, "a.py", "keep\ndrop\n")
        self.states.record_read(self.root / "a.py")
        self._apply([
            {"path": "a.py", "action": "replace", "old_text": "drop\n", "new_text": ""},
        ])
        self.assertEqual(self._read("a.py"), "keep\n")

    def test_missing_context_is_refused(self) -> None:
        _write(self.root, "a.py", "value = 1\n")
        out = self._apply([
            {"path": "a.py", "action": "replace", "old_text": "absent", "new_text": "x"},
        ])
        self.assertIn("old_text not found in a.py", out)
        self.assertEqual(self._read("a.py"), "value = 1\n")

    def test_ambiguous_context_is_refused_and_the_matches_are_located(self) -> None:
        _write(self.root, "a.py", "dup\ndup\n")
        out = self._apply([
            {"path": "a.py", "action": "replace", "old_text": "dup", "new_text": "x"},
        ])
        self.assertIn("appears 2 times", out)
        self.assertIn("line 1, line 2", out)
        self.assertEqual(self._read("a.py"), "dup\ndup\n")

    def test_occurrence_picks_between_repeated_matches(self) -> None:
        _write(self.root, "a.py", "dup\ndup\n")
        self.states.record_read(self.root / "a.py")
        out = self._apply([
            {
                "path": "a.py",
                "action": "replace",
                "old_text": "dup",
                "new_text": "x",
                "occurrence": 2,
            },
        ])
        self.assertIn("Patch applied", out)
        self.assertEqual(self._read("a.py"), "dup\nx\n")

    def test_occurrence_out_of_range_is_refused(self) -> None:
        _write(self.root, "a.py", "dup\ndup\n")
        out = self._apply([
            {
                "path": "a.py",
                "action": "replace",
                "old_text": "dup",
                "new_text": "x",
                "occurrence": 5,
            },
        ])
        self.assertIn("occurrence 5 is out of range", out)
        self.assertEqual(self._read("a.py"), "dup\ndup\n")

    def test_a_near_miss_names_the_line_it_found(self) -> None:
        _write(self.root, "a.py", "def f():\n    return 1\n")
        out = self._apply([
            {
                "path": "a.py",
                "action": "replace",
                "old_text": "def f():\n  return 1",
                "new_text": "def f():\n  return 2",
            },
        ])
        self.assertIn("near match", out)
        self.assertIn("line 1", out)
        self.assertEqual(self._read("a.py"), "def f():\n    return 1\n")

    def test_a_missing_final_newline_is_preserved(self) -> None:
        _write(self.root, "a.py", "value = 1")
        self.states.record_read(self.root / "a.py")
        self._apply([
            {"path": "a.py", "action": "replace", "old_text": "1", "new_text": "9"},
        ])
        self.assertEqual(self._read("a.py"), "value = 9")

    def test_appending_to_an_unterminated_file_keeps_it_unterminated(self) -> None:
        _write(self.root, "a.py", "first")
        self.states.record_read(self.root / "a.py")
        self._apply([{"path": "a.py", "action": "add", "new_text": "second"}])
        self.assertEqual(self._read("a.py"), "first\nsecond")

    def test_replace_on_a_missing_file_is_refused(self) -> None:
        out = self._apply([
            {"path": "gone.py", "action": "replace", "old_text": "a", "new_text": "b"},
        ])
        self.assertIn("file to update does not exist", out)

    def test_binary_file_is_refused(self) -> None:
        (self.root / "blob.bin").write_bytes(b"\x00\xff\xfe")
        out = self._apply([
            {"path": "blob.bin", "action": "replace", "old_text": "a", "new_text": "b"},
        ])
        self.assertIn("not text", out)

    def test_unknown_action_is_refused(self) -> None:
        out = self._apply([{"path": "a.py", "action": "move", "new_text": "x"}])
        self.assertIn("unknown action 'move'", out)
        self.assertIn("replace, add", out)

    def test_empty_edit_list_is_refused(self) -> None:
        self.assertIn("must provide edits", _run(self.tool.execute(edits=[])))

    def test_malformed_edits_are_refused(self) -> None:
        cases = [
            ([{"action": "add", "new_text": "x"}], "path required for edit"),
            ([{"path": "a.py"}], "action required for edit"),
            ([{"path": "a.py", "action": "add"}], "new_text required for add"),
            ([{"path": "a.py", "action": "replace", "new_text": "x"}], "old_text required"),
            (["not an object"], "each edit must be an object"),
            ([{"path": "", "action": "add", "new_text": "x"}], "path cannot be empty"),
        ]
        for edits, expected in cases:
            with self.subTest(expected=expected):
                self.assertIn(expected, self._apply(edits))

    def test_escaping_the_workspace_is_refused(self) -> None:
        tool = ApplyPatchTool(workspace=self.root, allowed_dir=self.root)
        out = _run(tool.execute(edits=[
            {"path": "../outside.py", "action": "add", "new_text": "x = 1\n"},
        ]))
        self.assertTrue(out.startswith("Error"), out)
        self.assertFalse((self.root.parent / "outside.py").exists())

    def test_chained_edits_to_one_file_see_each_other(self) -> None:
        _write(self.root, "a.py", "one\n")
        self.states.record_read(self.root / "a.py")
        self._apply([
            {"path": "a.py", "action": "replace", "old_text": "one", "new_text": "two"},
            {"path": "a.py", "action": "replace", "old_text": "two", "new_text": "three"},
        ])
        self.assertEqual(self._read("a.py"), "three\n")

    def test_crlf_line_endings_are_preserved(self) -> None:
        (self.root / "a.py").write_bytes(b"one\r\ntwo\r\n")
        self.states.record_read(self.root / "a.py")
        self._apply([
            {"path": "a.py", "action": "replace", "old_text": "one", "new_text": "ONE"},
        ])
        self.assertEqual((self.root / "a.py").read_bytes(), b"ONE\r\ntwo\r\n")

    def test_dry_run_reports_without_writing(self) -> None:
        _write(self.root, "a.py", "value = 1\n")
        self.states.record_read(self.root / "a.py")
        out = self._apply(
            [{"path": "a.py", "action": "replace", "old_text": "1", "new_text": "9"}],
            dry_run=True,
        )
        self.assertIn("Patch dry-run succeeded", out)
        self.assertEqual(self._read("a.py"), "value = 1\n")

    def test_dry_run_still_reports_a_bad_patch(self) -> None:
        _write(self.root, "a.py", "value = 1\n")
        out = self._apply(
            [{"path": "a.py", "action": "replace", "old_text": "absent", "new_text": "x"}],
            dry_run=True,
        )
        self.assertIn("old_text not found", out)

    def test_success_records_the_write_for_freshness(self) -> None:
        self._apply([{"path": "a.py", "action": "add", "new_text": "x = 1\n"}])
        self.assertIsNotNone(self.states.get(self.root / "a.py"))

    def test_editing_an_unread_file_warns(self) -> None:
        """apply_patch is the default editing tool, so it owes the same warning."""
        _write(self.root, "a.py", "value = 1\n")
        out = self._apply([
            {"path": "a.py", "action": "replace", "old_text": "1", "new_text": "9"},
        ])
        self.assertIn("has not been read yet", out)
        self.assertIn("Patch applied", out)
        self.assertEqual(self._read("a.py"), "value = 9\n")

    def test_appending_to_a_changed_file_warns(self) -> None:
        """An append has no old_text to anchor it, so staleness must be flagged."""
        target = _write(self.root, "a.py", "value = 1\n")
        self.states.record_read(target)
        target.write_bytes(b"value = 1\nsomeone else wrote this\n")
        out = self._apply([{"path": "a.py", "action": "add", "new_text": "mine\n"}])
        self.assertIn("modified since last read", out)

    def test_creating_a_file_does_not_warn(self) -> None:
        out = self._apply([{"path": "new.py", "action": "add", "new_text": "x = 1\n"}])
        self.assertNotIn("Warning", out)

    def test_a_fresh_read_does_not_warn(self) -> None:
        target = _write(self.root, "a.py", "value = 1\n")
        self.states.record_read(target)
        out = self._apply([
            {"path": "a.py", "action": "replace", "old_text": "1", "new_text": "9"},
        ])
        self.assertNotIn("Warning", out)

    def test_a_failing_write_rolls_every_file_back(self) -> None:
        """The whole point of batching edits: no half-applied multi-file change."""
        _write(self.root, "a.py", "a original\n")
        _write(self.root, "b.py", "b original\n")
        self.states.record_read(self.root / "a.py")
        self.states.record_read(self.root / "b.py")

        with mock.patch.object(Path, "write_bytes", _fail_once_on("b.py")):
            out = self._apply([
                {"path": "a.py", "action": "replace", "old_text": "original", "new_text": "patched"},
                {"path": "b.py", "action": "replace", "old_text": "original", "new_text": "patched"},
            ])

        self.assertIn("Error applying patch", out)
        self.assertIn("disk full", out)
        self.assertEqual(self._read("a.py"), "a original\n")
        self.assertEqual(self._read("b.py"), "b original\n")

    def test_a_failing_write_removes_files_it_had_created(self) -> None:
        _write(self.root, "existing.py", "kept\n")
        self.states.record_read(self.root / "existing.py")
        with mock.patch.object(Path, "write_bytes", _fail_once_on("second.py")):
            self._apply([
                {"path": "created.py", "action": "add", "new_text": "new\n"},
                {"path": "second.py", "action": "add", "new_text": "new\n"},
            ])

        self.assertFalse((self.root / "created.py").exists())
        self.assertEqual(self._read("existing.py"), "kept\n")

    def test_a_failing_write_does_not_record_state(self) -> None:
        _write(self.root, "a.py", "original\n")
        self.states.record_read(self.root / "a.py")
        before = self.states.get(self.root / "a.py")
        with mock.patch.object(Path, "write_bytes", _fail_once_on("a.py", "nope")):
            self._apply([
                {"path": "a.py", "action": "replace", "old_text": "original", "new_text": "x"},
            ])
        self.assertEqual(self.states.get(self.root / "a.py"), before)


class ApplyPatchCheckpointBridgeTest(_WorkspaceTest):
    """apply_patch is what feeds the recorder that review and restore rely on."""

    def test_before_snapshots_reach_the_bound_recorder(self) -> None:
        target = _write(self.root, "a.py", "before\n")
        tool = ApplyPatchTool(workspace=self.root, file_states=FileStates())
        recorder = TurnRecorder()
        token = bind_checkpoint_recorder(recorder)
        try:
            _run(tool.execute(edits=[
                {"path": "a.py", "action": "replace", "old_text": "before", "new_text": "after"},
                {"path": "new.py", "action": "add", "new_text": "fresh\n"},
            ]))
        finally:
            reset_checkpoint_recorder(token)

        self.assertEqual(recorder.files[str(target.resolve())], b"before\n")
        self.assertIsNone(recorder.files[str((self.root / "new.py").resolve())])

    def test_no_recorder_bound_is_not_an_error(self) -> None:
        tool = ApplyPatchTool(workspace=self.root, file_states=FileStates())
        out = _run(tool.execute(edits=[
            {"path": "a.py", "action": "add", "new_text": "x\n"},
        ]))
        self.assertIn("Patch applied", out)


class ByteFidelityTest(_WorkspaceTest):
    """What the tools write must be what was asked, and nothing else.

    Every silent normalisation shows up in review as noise around the real
    change, and can break a file whose bytes are the point: a CRLF checkout, a
    fixture without a final newline, a snapshot with meaningful padding.
    """

    def setUp(self) -> None:
        super().setUp()
        self.states = FileStates()

    def _write_tool(self) -> WriteFileTool:
        return WriteFileTool(workspace=self.root, file_states=self.states)

    def _edit_tool(self) -> EditFileTool:
        return EditFileTool(workspace=self.root, file_states=self.states)

    def test_write_file_keeps_a_crlf_file_on_crlf(self) -> None:
        target = self.root / "a.py"
        target.write_bytes(b"one\r\ntwo\r\n")
        self.states.record_read(target)
        _run(self._write_tool().execute(path="a.py", content="one\nTWO\n"))
        self.assertEqual(target.read_bytes(), b"one\r\nTWO\r\n")

    def test_write_file_leaves_an_lf_file_alone(self) -> None:
        target = self.root / "a.py"
        target.write_bytes(b"one\ntwo\n")
        self.states.record_read(target)
        _run(self._write_tool().execute(path="a.py", content="one\nTWO\n"))
        self.assertEqual(target.read_bytes(), b"one\nTWO\n")

    def test_write_file_does_not_double_carriage_returns(self) -> None:
        """Content that already carries CRLF must not be converted twice."""
        target = self.root / "a.py"
        target.write_bytes(b"one\r\n")
        self.states.record_read(target)
        _run(self._write_tool().execute(path="a.py", content="one\r\ntwo\r\n"))
        self.assertEqual(target.read_bytes(), b"one\r\ntwo\r\n")

    def test_edit_file_keeps_significant_trailing_whitespace(self) -> None:
        target = self.root / "fixture.txt"
        target.write_bytes(b"before\n")
        self.states.record_read(target)
        _run(self._edit_tool().execute(
            path="fixture.txt", old_text="before", new_text="padded   "
        ))
        self.assertEqual(target.read_bytes(), b"padded   \n")

    def test_edit_file_still_blanks_an_indentation_only_line(self) -> None:
        target = self.root / "a.py"
        target.write_bytes(b"x = 1\n")
        self.states.record_read(target)
        _run(self._edit_tool().execute(
            path="a.py", old_text="x = 1", new_text="x = 1\n    \ny = 2"
        ))
        self.assertEqual(target.read_bytes(), b"x = 1\n\ny = 2\n")


class EditAmbiguityTest(_WorkspaceTest):
    """An ambiguous old_text edits nothing, so the reply must carry is_error.

    A plain string here read as success to everything keyed on the flag -
    fail_on_tool_error, retries, stats - while the file stayed untouched.
    apply_patch already refuses the same case as an error.
    """

    def setUp(self) -> None:
        super().setUp()
        self.states = FileStates()

    def test_multiple_matches_come_back_as_an_error_result(self) -> None:
        target = _write(self.root, "a.py", "dup\ndup\n")
        self.states.record_read(target)
        result = asyncio.run(
            EditFileTool(workspace=self.root, file_states=self.states).execute(
                path="a.py", old_text="dup", new_text="x"
            )
        )
        self.assertTrue(getattr(result, "is_error", False))
        self.assertIn("appears 2 times", str(result))
        self.assertEqual(target.read_text(encoding="utf-8"), "dup\ndup\n")


# ---------------------------------------------------------------------------
# PendingReviewStore
# ---------------------------------------------------------------------------


class PendingReviewTest(_WorkspaceTest):
    KEY = "websocket:chat-1"

    def setUp(self) -> None:
        super().setUp()
        self.store = PendingReviewStore(self.root)

    def _paths(self) -> list[str]:
        return [row["path"] for row in self.store.list_changes(self.KEY)]

    def test_a_modified_file_becomes_pending(self) -> None:
        target = _write(self.root, "a.py", "after\n")
        self.store.merge_turn(self.KEY, {str(target): b"before\n"})
        rows = self.store.list_changes(self.KEY)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "modified")

    def test_a_created_file_is_marked_created(self) -> None:
        target = _write(self.root, "a.py", "fresh\n")
        self.store.merge_turn(self.KEY, {str(target): None})
        self.assertEqual(self.store.list_changes(self.KEY)[0]["status"], "created")

    def test_a_deleted_file_is_marked_deleted(self) -> None:
        target = self.root / "a.py"
        self.store.merge_turn(self.KEY, {str(target): b"was here\n"})
        self.assertEqual(self.store.list_changes(self.KEY)[0]["status"], "deleted")

    def test_an_unchanged_file_is_not_tracked(self) -> None:
        target = _write(self.root, "a.py", "same\n")
        self.store.merge_turn(self.KEY, {str(target): b"same\n"})
        self.assertEqual(self._paths(), [])

    def test_state_is_persisted_across_instances(self) -> None:
        target = _write(self.root, "a.py", "after\n")
        self.store.merge_turn(self.KEY, {str(target): b"before\n"})
        self.assertEqual(len(PendingReviewStore(self.root).list_changes(self.KEY)), 1)

    def test_review_state_is_git_ignored(self) -> None:
        target = _write(self.root, "a.py", "after\n")
        self.store.merge_turn(self.KEY, {str(target): b"before\n"})
        marker = self.root / ".pending-review" / ".gitignore"
        self.assertTrue(marker.is_file())
        self.assertIn("*", marker.read_text(encoding="utf-8"))

    def test_sessions_do_not_see_each_other(self) -> None:
        target = _write(self.root, "a.py", "after\n")
        self.store.merge_turn(self.KEY, {str(target): b"before\n"})
        self.assertEqual(self.store.list_changes("websocket:other"), [])

    def test_the_oldest_baseline_survives_later_turns(self) -> None:
        """Two turns editing one file must still rewind to the original."""
        target = _write(self.root, "a.py", "v2\n")
        self.store.merge_turn(self.KEY, {str(target): b"v1\n"})
        target.write_bytes(b"v3\n")
        self.store.merge_turn(self.KEY, {str(target): b"v2\n"})

        self.store.reject(self.KEY)
        self.assertEqual(target.read_bytes(), b"v1\n")

    def test_returning_to_the_baseline_drops_the_entry(self) -> None:
        target = _write(self.root, "a.py", "after\n")
        self.store.merge_turn(self.KEY, {str(target): b"before\n"})
        target.write_bytes(b"before\n")
        self.store.merge_turn(self.KEY, {str(target): b"after\n"})
        self.assertEqual(self._paths(), [])

    def test_file_payload_exposes_both_sides(self) -> None:
        target = _write(self.root, "a.py", "after\n")
        self.store.merge_turn(self.KEY, {str(target): b"before\n"})
        payload = self.store.file_payload(self.KEY, str(target.resolve()))
        self.assertIsNotNone(payload)
        self.assertIn("before", payload["baseline"])
        self.assertIn("after", payload["current"])

    def test_file_payload_is_none_for_an_untracked_path(self) -> None:
        self.assertIsNone(self.store.file_payload(self.KEY, str(self.root / "nope.py")))

    def test_accept_keeps_the_file_and_forgets_the_baseline(self) -> None:
        target = _write(self.root, "a.py", "after\n")
        self.store.merge_turn(self.KEY, {str(target): b"before\n"})
        self.assertEqual(self.store.accept(self.KEY), 1)
        self.assertEqual(target.read_bytes(), b"after\n")
        self.assertEqual(self._paths(), [])

    def test_accept_of_one_path_leaves_the_others_pending(self) -> None:
        first = _write(self.root, "a.py", "after\n")
        second = _write(self.root, "b.py", "after\n")
        self.store.merge_turn(self.KEY, {str(first): b"before\n", str(second): b"before\n"})
        self.assertEqual(self.store.accept(self.KEY, str(first.resolve())), 1)
        self.assertEqual(self._paths(), [str(second.resolve())])

    def test_accept_of_an_unknown_path_counts_nothing(self) -> None:
        self.assertEqual(self.store.accept(self.KEY, "/nowhere/a.py"), 0)

    def test_reject_restores_the_original_bytes(self) -> None:
        target = _write(self.root, "a.py", "after\n")
        self.store.merge_turn(self.KEY, {str(target): b"before\n"})
        self.assertEqual(self.store.reject(self.KEY), (1, 0))
        self.assertEqual(target.read_bytes(), b"before\n")
        self.assertEqual(self._paths(), [])

    def test_reject_deletes_a_file_the_agent_created(self) -> None:
        target = _write(self.root, "a.py", "fresh\n")
        self.store.merge_turn(self.KEY, {str(target): None})
        self.assertEqual(self.store.reject(self.KEY), (0, 1))
        self.assertFalse(target.exists())

    def test_reject_recreates_a_file_the_agent_deleted(self) -> None:
        target = self.root / "a.py"
        self.store.merge_turn(self.KEY, {str(target): b"was here\n"})
        self.assertEqual(self.store.reject(self.KEY), (1, 0))
        self.assertEqual(target.read_bytes(), b"was here\n")

    def test_reject_of_one_path_leaves_the_others_alone(self) -> None:
        first = _write(self.root, "a.py", "after\n")
        second = _write(self.root, "b.py", "after\n")
        self.store.merge_turn(self.KEY, {str(first): b"before\n", str(second): b"before\n"})
        self.store.reject(self.KEY, str(first.resolve()))
        self.assertEqual(first.read_bytes(), b"before\n")
        self.assertEqual(second.read_bytes(), b"after\n")

    def test_an_unreadable_baseline_does_not_wedge_the_review(self) -> None:
        """A corrupt entry must not make the pending state impossible to clear."""
        target = _write(self.root, "a.py", "after\n")
        self.store.merge_turn(self.KEY, {str(target): b"before\n"})
        state = self.store._path(self.KEY)
        data = json.loads(state.read_text(encoding="utf-8"))
        for entry in data["files"].values():
            entry["b64"] = "!!! not base64 !!!"
        state.write_text(json.dumps(data), encoding="utf-8")

        self.assertEqual(self.store.reject(self.KEY), (0, 0))
        self.assertEqual(self._paths(), [])

    def test_corrupt_state_file_reads_as_empty(self) -> None:
        target = _write(self.root, "a.py", "after\n")
        self.store.merge_turn(self.KEY, {str(target): b"before\n"})
        self.store._path(self.KEY).write_text("{not json", encoding="utf-8")
        self.assertEqual(self.store.list_changes(self.KEY), [])

    def test_an_oversized_baseline_is_not_tracked(self) -> None:
        target = _write(self.root, "big.bin", "after\n")
        self.store.merge_turn(self.KEY, {str(target): b"x" * (1024 * 1024 + 1)})
        self.assertEqual(self._paths(), [])

    def test_tracking_is_capped(self) -> None:
        files = {}
        for index in range(520):
            target = _write(self.root, f"f{index}.py", "after\n")
            files[str(target)] = b"before\n"
        self.store.merge_turn(self.KEY, files)
        self.assertEqual(len(self._paths()), 500)


# ---------------------------------------------------------------------------
# CheckpointStore
# ---------------------------------------------------------------------------


class CheckpointTest(_WorkspaceTest):
    KEY = "websocket:chat-1"

    def setUp(self) -> None:
        super().setUp()
        self.store = CheckpointStore(self.root)

    def _session(self, messages: list[dict] | None = None) -> Session:
        return Session(
            key=self.KEY,
            messages=messages if messages is not None else [{"role": "user", "content": "hi"}],
        )

    def test_save_returns_metadata_and_writes_a_file(self) -> None:
        meta = self.store.save(self._session(), note="before refactor")
        self.assertIn("before-refactor", meta["name"])
        self.assertEqual(meta["message_count"], 1)
        self.assertTrue(self.store._path(self.KEY, meta["name"]).is_file())

    def test_checkpoints_are_git_ignored(self) -> None:
        self.store.save(self._session())
        marker = self.root / ".navin" / "checkpoints" / ".gitignore"
        self.assertTrue(marker.is_file())

    def test_the_legacy_visible_folder_is_migrated(self) -> None:
        root = self.root / "legacy"
        (root / "checkpoints").mkdir(parents=True)
        (root / "checkpoints" / "old.json").write_text("{}", encoding="utf-8")
        CheckpointStore(root)
        self.assertTrue((root / ".navin" / "checkpoints" / "old.json").is_file())
        self.assertFalse((root / "checkpoints").exists())

    def test_the_legacy_dot_folder_is_migrated(self) -> None:
        root = self.root / "legacy-dot"
        (root / ".checkpoints").mkdir(parents=True)
        (root / ".checkpoints" / "old.json").write_text("{}", encoding="utf-8")
        CheckpointStore(root)
        self.assertTrue((root / ".navin" / "checkpoints" / "old.json").is_file())
        self.assertFalse((root / ".checkpoints").exists())

    def test_list_is_newest_first(self) -> None:
        first = self.store.save(self._session(), note="one")
        second = self.store.save(self._session(), note="two")
        names = [row["name"] for row in self.store.list(self.KEY)]
        self.assertEqual(names.index(second["name"]), 0)
        self.assertIn(first["name"], names)

    def test_two_saves_in_the_same_second_do_not_collide(self) -> None:
        first = self.store.save(self._session(), note="same")
        second = self.store.save(self._session(), note="same")
        self.assertNotEqual(first["name"], second["name"])
        self.assertEqual(len(self.store.list(self.KEY)), 2)

    def test_restore_chat_replaces_the_messages(self) -> None:
        session = self._session([{"role": "user", "content": "first"}])
        meta = self.store.save(session)
        session.messages = [{"role": "user", "content": "first"}, {"role": "user", "content": "second"}]
        self.assertEqual(self.store.restore_chat(session, meta["name"]), 1)
        self.assertEqual(session.messages, [{"role": "user", "content": "first"}])

    def test_restore_chat_clamps_a_bad_consolidation_marker(self) -> None:
        session = self._session([{"role": "user", "content": "a"}])
        meta = self.store.save(session)
        path = self.store._path(self.KEY, meta["name"])
        data = json.loads(path.read_text(encoding="utf-8"))
        data["last_consolidated"] = 99
        path.write_text(json.dumps(data), encoding="utf-8")
        self.store.restore_chat(session, meta["name"])
        self.assertEqual(session.last_consolidated, 0)

    def test_restore_files_rewinds_to_the_snapshot(self) -> None:
        target = _write(self.root, "a.py", "v1\n")
        meta = self.store.save(self._session(), files={str(target): b"v1\n"})
        target.write_bytes(b"v2\n")
        self.assertEqual(self.store.restore_files(self.KEY, meta["name"]), (1, 0, []))
        self.assertEqual(target.read_bytes(), b"v1\n")

    def test_restore_files_deletes_what_did_not_exist_yet(self) -> None:
        target = _write(self.root, "a.py", "created later\n")
        meta = self.store.save(self._session(), files={str(target): None})
        self.assertEqual(self.store.restore_files(self.KEY, meta["name"]), (0, 1, []))
        self.assertFalse(target.exists())

    def test_restore_files_uses_the_earliest_snapshot_after_the_target(self) -> None:
        """A file touched two turns later must still rewind to the target state."""
        target = _write(self.root, "a.py", "v1\n")
        first = self.store.save(self._session(), note="aaa", files={str(target): b"v1\n"})
        target.write_bytes(b"v2\n")
        self.store.save(self._session(), note="bbb", files={str(target): b"v2\n"})
        target.write_bytes(b"v3\n")

        self.store.restore_files(self.KEY, first["name"])
        self.assertEqual(target.read_bytes(), b"v1\n")

    def test_restore_files_reaches_a_file_first_touched_later(self) -> None:
        first = self.store.save(self._session(), note="aaa")
        later = _write(self.root, "b.py", "made later\n")
        self.store.save(self._session(), note="bbb", files={str(later): None})

        self.assertEqual(self.store.restore_files(self.KEY, first["name"]), (0, 1, []))
        self.assertFalse(later.exists())

    def test_attach_files_adds_snapshots_after_the_fact(self) -> None:
        target = _write(self.root, "a.py", "v1\n")
        meta = self.store.save(self._session())
        self.store.attach_files(self.KEY, meta["name"], {str(target): b"v0\n"})
        target.write_bytes(b"v2\n")
        self.store.restore_files(self.KEY, meta["name"])
        self.assertEqual(target.read_bytes(), b"v0\n")

    def test_skipped_files_are_stored_and_counted(self) -> None:
        meta = self.store.save(self._session(), skipped=["big.bin", "big.bin", "huge.iso"])
        data = self.store.load(self.KEY, meta["name"])
        self.assertEqual(data["skipped_files"], ["big.bin", "huge.iso"])
        row = self.store.list(self.KEY)[0]
        self.assertEqual(row["skipped_count"], 2)

    def test_attach_files_merges_the_skipped_manifest(self) -> None:
        meta = self.store.save(self._session(), skipped=["a.bin"])
        self.store.attach_files(self.KEY, meta["name"], {}, skipped=["b.bin", "a.bin"])
        data = self.store.load(self.KEY, meta["name"])
        self.assertEqual(data["skipped_files"], ["a.bin", "b.bin"])

    def test_restore_reports_files_it_could_not_rewind(self) -> None:
        target = _write(self.root, "a.py", "v1\n")
        meta = self.store.save(
            self._session(),
            files={str(target): b"v1\n"},
            skipped=["giant.bin"],
        )
        target.write_bytes(b"v2\n")
        restored, deleted, unrestorable = self.store.restore_files(self.KEY, meta["name"])
        self.assertEqual((restored, deleted), (1, 0))
        self.assertEqual(unrestorable, ["giant.bin"])

    def test_a_skipped_file_captured_by_a_later_turn_is_not_reported(self) -> None:
        """If a later checkpoint holds the pre-state, the rewind is complete."""
        target = _write(self.root, "a.py", "v1\n")
        first = self.store.save(self._session(), note="aaa", skipped=[str(target)])
        self.store.save(self._session(), note="bbb", files={str(target): b"v1\n"})
        target.write_bytes(b"v2\n")

        restored, _deleted, unrestorable = self.store.restore_files(self.KEY, first["name"])
        self.assertEqual(restored, 1)
        self.assertEqual(unrestorable, [])

    def test_delete_removes_the_checkpoint(self) -> None:
        meta = self.store.save(self._session())
        self.store.delete(self.KEY, meta["name"])
        self.assertEqual(self.store.list(self.KEY), [])

    def test_unknown_checkpoint_is_an_error(self) -> None:
        for call in (
            lambda: self.store.load(self.KEY, "20200101-000000"),
            lambda: self.store.restore_files(self.KEY, "20200101-000000"),
        ):
            with self.subTest(call=call), self.assertRaises(CheckpointError) as caught:
                call()
            self.assertIn("not found", str(caught.exception))

    def test_an_empty_name_is_an_error(self) -> None:
        with self.assertRaises(CheckpointError) as caught:
            self.store.load(self.KEY, "///")
        self.assertIn("invalid checkpoint name", str(caught.exception))

    def test_a_corrupt_checkpoint_is_an_error(self) -> None:
        meta = self.store.save(self._session())
        self.store._path(self.KEY, meta["name"]).write_text("{not json", encoding="utf-8")
        with self.assertRaises(CheckpointError) as caught:
            self.store.load(self.KEY, meta["name"])
        self.assertIn("unreadable", str(caught.exception))

    def test_a_checkpoint_without_messages_is_corrupt(self) -> None:
        meta = self.store.save(self._session())
        self.store._path(self.KEY, meta["name"]).write_text('{"messages": 1}', encoding="utf-8")
        with self.assertRaises(CheckpointError) as caught:
            self.store.load(self.KEY, meta["name"])
        self.assertIn("corrupt", str(caught.exception))

    def test_sessions_are_isolated(self) -> None:
        self.store.save(self._session())
        self.assertEqual(self.store.list("websocket:other"), [])

    def test_retention_caps_the_stored_count(self) -> None:
        folder = self.store.dir / "websocket-chat-1"
        folder.mkdir(parents=True, exist_ok=True)
        for index in range(105):
            payload = {"name": f"2020010{index:04d}", "messages": [], "files": {}}
            (folder / f"2020010{index:04d}.json").write_text(
                json.dumps(payload), encoding="utf-8"
            )
        self.store.save(self._session())
        remaining = list(folder.glob("*.json"))
        self.assertEqual(len(remaining), 100)

    def test_retention_keeps_the_newest(self) -> None:
        folder = self.store.dir / "websocket-chat-1"
        folder.mkdir(parents=True, exist_ok=True)
        for index in range(105):
            (folder / f"2020010{index:04d}.json").write_text(
                json.dumps({"messages": [], "files": {}}), encoding="utf-8"
            )
        newest = self.store.save(self._session())
        self.assertTrue(self.store._path(self.KEY, newest["name"]).is_file())


class TurnRecorderTest(_WorkspaceTest):
    def test_the_first_snapshot_of_a_file_wins(self) -> None:
        target = _write(self.root, "a.py", "v1\n")
        recorder = TurnRecorder()
        recorder.record(target)
        target.write_bytes(b"v2\n")
        recorder.record(target)
        self.assertEqual(recorder.files[str(target.resolve())], b"v1\n")

    def test_a_missing_file_records_as_absent(self) -> None:
        recorder = TurnRecorder()
        recorder.record(self.root / "gone.py")
        self.assertIsNone(recorder.files[str((self.root / "gone.py").resolve())])

    def test_an_oversized_file_is_skipped(self) -> None:
        target = self.root / "big.bin"
        target.write_bytes(b"x" * (1024 * 1024 + 1))
        recorder = TurnRecorder()
        recorder.record(target)
        self.assertEqual(recorder.files, {})
        self.assertTrue(recorder.skipped)

    def test_the_file_count_is_capped(self) -> None:
        recorder = TurnRecorder()
        for index in range(210):
            recorder.record(_write(self.root, f"f{index}.py", "x\n"))
        self.assertEqual(len(recorder.files), 200)
        self.assertTrue(recorder.skipped)


class ReviewAndCheckpointAgreementTest(_WorkspaceTest):
    """Both stores consume the same recorder output, so they must agree."""

    KEY = "websocket:chat-1"

    def test_one_recorder_feeds_both_stores_to_the_same_state(self) -> None:
        target = _write(self.root, "a.py", "original\n")
        tool = ApplyPatchTool(workspace=self.root, file_states=FileStates())
        recorder = TurnRecorder()
        token = bind_checkpoint_recorder(recorder)
        try:
            _run(tool.execute(edits=[
                {"path": "a.py", "action": "replace", "old_text": "original", "new_text": "patched"},
            ]))
        finally:
            reset_checkpoint_recorder(token)
        self.assertEqual(target.read_bytes(), b"patched\n")

        checkpoints = CheckpointStore(self.root)
        meta = checkpoints.save(
            Session(key=self.KEY, messages=[]), files=dict(recorder.files),
        )
        review = PendingReviewStore(self.root)
        review.merge_turn(self.KEY, dict(recorder.files))

        review.reject(self.KEY)
        after_review = target.read_bytes()
        target.write_bytes(b"patched\n")
        checkpoints.restore_files(self.KEY, meta["name"])
        self.assertEqual(after_review, target.read_bytes())
        self.assertEqual(after_review, b"original\n")

    def test_a_snapshot_survives_base64_round_tripping(self) -> None:
        payload = b"\xc3\xa9 accents and \x00 a null byte\n"
        encoded = base64.b64encode(payload).decode("ascii")
        self.assertEqual(base64.b64decode(encoded), payload)


if __name__ == "__main__":
    unittest.main()
