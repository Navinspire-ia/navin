"""manage_files: the destructive operations, on the record.

The point of this tool is not that it can delete a file - exec could already do
that. It is that the deletion leaves a baseline behind, so the review panel can
show it and a checkpoint can undo it. Most of these tests therefore assert on
what the recorder holds, not only on what the tree looks like afterwards.
"""

from __future__ import annotations

import asyncio
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from navin.agent.checkpoints import (
    CheckpointStore,
    TurnRecorder,
    bind_checkpoint_recorder,
    reset_checkpoint_recorder,
)
from navin.agent.review import PendingReviewStore
from navin.agent.tools.file_manage import ManageFilesTool
from navin.agent.tools.file_state import FileStates
from navin.session.manager import Session


def _run(coro):
    return asyncio.run(coro)


class _ManageTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        self.addCleanup(self._tmp.cleanup)
        self.states = FileStates()
        self.recorder = TurnRecorder()

    def _write(self, rel: str, body: str = "body\n") -> Path:
        target = self.root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
        return target

    def _tool(self) -> ManageFilesTool:
        return ManageFilesTool(
            workspace=self.root, allowed_dir=self.root, file_states=self.states
        )

    def _call(self, **kwargs) -> str:
        token = bind_checkpoint_recorder(self.recorder)
        try:
            return str(_run(self._tool().execute(**kwargs)))
        finally:
            reset_checkpoint_recorder(token)

    def _baseline(self, rel: str) -> bytes | None:
        return self.recorder.files[str((self.root / rel).resolve())]


class DeleteTest(_ManageTest):
    def test_a_file_is_removed_and_its_bytes_are_recorded(self) -> None:
        self._write("a.py", "keep me\n")
        out = self._call(action="delete", paths=["a.py"])
        self.assertIn("Deleted 1 file", out)
        self.assertFalse((self.root / "a.py").exists())
        self.assertEqual(self._baseline("a.py"), b"keep me\n")

    def test_several_paths_go_in_one_call(self) -> None:
        self._write("a.py")
        self._write("b.py")
        self._call(action="delete", paths=["a.py", "b.py"])
        self.assertFalse((self.root / "a.py").exists())
        self.assertFalse((self.root / "b.py").exists())
        self.assertEqual(len(self.recorder.files), 2)

    def test_a_directory_needs_recursive(self) -> None:
        self._write("pkg/mod.py")
        out = self._call(action="delete", paths=["pkg"])
        self.assertIn("recursive=true", out)
        self.assertTrue((self.root / "pkg/mod.py").is_file())
        self.assertEqual(self.recorder.files, {})

    def test_recursive_records_every_file_it_removes(self) -> None:
        self._write("pkg/mod.py", "one\n")
        self._write("pkg/sub/other.py", "two\n")
        out = self._call(action="delete", paths=["pkg"], recursive=True)
        self.assertIn("Deleted 2 file(s)", out)
        self.assertFalse((self.root / "pkg").exists())
        self.assertEqual(self._baseline("pkg/mod.py"), b"one\n")
        self.assertEqual(self._baseline("pkg/sub/other.py"), b"two\n")

    def test_a_missing_path_is_refused_before_anything_is_touched(self) -> None:
        self._write("a.py")
        out = self._call(action="delete", paths=["a.py", "gone.py"])
        self.assertIn("does not exist", out)
        self.assertTrue((self.root / "a.py").is_file())

    def test_deleting_the_project_root_is_refused(self) -> None:
        out = self._call(action="delete", paths=["."])
        self.assertIn("project root", out)
        self.assertTrue(self.root.is_dir())

    def test_git_internals_are_refused(self) -> None:
        self._write(".git/config", "[core]\n")
        out = self._call(action="delete", paths=[".git"], recursive=True)
        self.assertIn("refusing to touch .git", out)
        self.assertTrue((self.root / ".git/config").is_file())

    def test_session_state_at_the_root_is_refused(self) -> None:
        self._write("sessions/websocket_chat.jsonl", "{}\n")
        out = self._call(action="delete", paths=["sessions"], recursive=True)
        self.assertIn("refusing to touch sessions", out)

    def test_a_deeper_sessions_directory_is_ordinary(self) -> None:
        """An application is allowed to have its own sessions folder."""
        self._write("src/sessions/store.py")
        out = self._call(action="delete", paths=["src/sessions"], recursive=True)
        self.assertIn("Deleted", out)
        self.assertFalse((self.root / "src/sessions").exists())

    def test_a_wide_delete_is_refused_rather_than_untracked(self) -> None:
        for index in range(205):
            self._write(f"gen/f{index}.py")
        out = self._call(action="delete", paths=["gen"], recursive=True)
        self.assertIn("above the 200", out)
        self.assertTrue((self.root / "gen/f0.py").is_file())

    def test_the_read_state_forgets_a_deleted_file(self) -> None:
        target = self._write("a.py")
        self.states.record_read(target)
        self._call(action="delete", paths=["a.py"])
        self.assertIsNone(self.states.get(target))


class MoveTest(_ManageTest):
    def test_a_rename_records_both_sides(self) -> None:
        self._write("old.py", "content\n")
        out = self._call(action="move", path="old.py", destination="new.py")
        self.assertIn("Moved", out)
        self.assertFalse((self.root / "old.py").exists())
        self.assertEqual((self.root / "new.py").read_text(encoding="utf-8"), "content\n")
        self.assertEqual(self._baseline("old.py"), b"content\n")
        self.assertIsNone(self._baseline("new.py"))

    def test_the_reply_says_references_are_not_updated(self) -> None:
        self._write("old.py")
        out = self._call(action="move", path="old.py", destination="new.py")
        self.assertIn("References to the old path are not updated", out)

    def test_an_existing_destination_directory_means_into_it(self) -> None:
        self._write("a.py")
        (self.root / "pkg").mkdir()
        self._call(action="move", path="a.py", destination="pkg")
        self.assertTrue((self.root / "pkg/a.py").is_file())

    def test_replacing_a_file_needs_overwrite(self) -> None:
        self._write("a.py", "source\n")
        self._write("b.py", "target\n")
        out = self._call(action="move", path="a.py", destination="b.py")
        self.assertIn("overwrite=true", out)
        self.assertEqual((self.root / "b.py").read_text(encoding="utf-8"), "target\n")

    def test_overwrite_records_what_it_replaced(self) -> None:
        self._write("a.py", "source\n")
        self._write("b.py", "target\n")
        self._call(action="move", path="a.py", destination="b.py", overwrite=True)
        self.assertEqual((self.root / "b.py").read_text(encoding="utf-8"), "source\n")
        self.assertEqual(self._baseline("b.py"), b"target\n")
        self.assertEqual(self._baseline("a.py"), b"source\n")

    def test_a_directory_moves_with_its_contents(self) -> None:
        self._write("pkg/mod.py", "one\n")
        self._write("pkg/sub/other.py", "two\n")
        self._call(action="move", path="pkg", destination="renamed")
        self.assertTrue((self.root / "renamed/sub/other.py").is_file())
        self.assertFalse((self.root / "pkg").exists())
        self.assertEqual(self._baseline("pkg/mod.py"), b"one\n")
        self.assertIsNone(self._baseline("renamed/mod.py"))

    def test_moving_a_directory_into_itself_is_refused(self) -> None:
        self._write("pkg/mod.py")
        out = self._call(action="move", path="pkg", destination="pkg/inner")
        self.assertIn("into itself", out)

    def test_a_move_onto_itself_is_refused(self) -> None:
        self._write("a.py")
        out = self._call(action="move", path="a.py", destination="a.py")
        self.assertIn("the same", out)


class CopyTest(_ManageTest):
    def test_a_copy_leaves_the_source_alone(self) -> None:
        self._write("a.py", "content\n")
        self._call(action="copy", path="a.py", destination="b.py")
        self.assertEqual((self.root / "a.py").read_text(encoding="utf-8"), "content\n")
        self.assertEqual((self.root / "b.py").read_text(encoding="utf-8"), "content\n")

    def test_the_source_is_not_recorded_because_it_does_not_change(self) -> None:
        self._write("a.py", "content\n")
        self._call(action="copy", path="a.py", destination="b.py")
        self.assertNotIn(str((self.root / "a.py").resolve()), self.recorder.files)
        self.assertIsNone(self._baseline("b.py"))

    def test_a_directory_copy_is_recursive(self) -> None:
        self._write("pkg/mod.py")
        self._call(action="copy", path="pkg", destination="copy")
        self.assertTrue((self.root / "copy/mod.py").is_file())


class EmptyDirectoryCollisionTest(_ManageTest):
    """A destination that exists is a collision even when it holds no files.

    The overwrite guard used to look only at the files a destination contains,
    so an empty directory slipped through it and shutil.move nested the source
    inside (pkg/a.py/a.py) instead of replacing it.
    """

    def test_moving_onto_an_empty_directory_of_the_same_name_needs_overwrite(self) -> None:
        self._write("a.py", "source\n")
        (self.root / "pkg" / "a.py").mkdir(parents=True)
        out = self._call(action="move", path="a.py", destination="pkg")
        self.assertIn("overwrite=true", out)
        self.assertTrue((self.root / "a.py").is_file())
        self.assertTrue((self.root / "pkg" / "a.py").is_dir())

    def test_overwrite_replaces_the_empty_directory_rather_than_nesting(self) -> None:
        self._write("a.py", "source\n")
        (self.root / "pkg" / "a.py").mkdir(parents=True)
        self._call(action="move", path="a.py", destination="pkg", overwrite=True)
        self.assertEqual(
            (self.root / "pkg" / "a.py").read_text(encoding="utf-8"), "source\n"
        )
        self.assertFalse((self.root / "a.py").exists())

    def test_copy_onto_an_empty_directory_needs_overwrite_too(self) -> None:
        self._write("a.py", "source\n")
        (self.root / "pkg" / "a.py").mkdir(parents=True)
        out = self._call(action="copy", path="a.py", destination="pkg")
        self.assertIn("overwrite=true", out)
        self.assertTrue((self.root / "pkg" / "a.py").is_dir())
        self.assertFalse((self.root / "pkg" / "a.py" / "a.py").exists())


class TransferDiagnosticsTest(_ManageTest):
    """A move or copy answers with what the linters think of the destination.

    The linters themselves are exercised elsewhere; here they are stubbed so
    the tests pin down when the report is requested and for which paths.
    """

    _PROBE = "navin.agent.tools.edit_feedback.diagnostics_after_write"

    def test_a_move_reports_on_the_destination_path(self) -> None:
        self._write("a.py", "def broken(:\n")
        with mock.patch(self._PROBE, return_value="b.py:1:12 syntax error") as probe:
            out = self._call(action="move", path="a.py", destination="b.py")
        self.assertIn("Moved", out)
        self.assertIn("b.py:1:12 syntax error", out)
        (paths,) = probe.call_args.args
        self.assertEqual([p.name for p in paths], ["b.py"])

    def test_a_copy_reports_on_the_destination_path(self) -> None:
        self._write("a.py", "def broken(:\n")
        with mock.patch(self._PROBE, return_value="b.py:1:12 syntax error") as probe:
            out = self._call(action="copy", path="a.py", destination="b.py")
        self.assertIn("Copied", out)
        self.assertIn("b.py:1:12 syntax error", out)
        (paths,) = probe.call_args.args
        self.assertEqual([p.name for p in paths], ["b.py"])

    def test_a_clean_move_reads_as_it_always_did(self) -> None:
        self._write("a.py", "x = 1\n")
        with mock.patch(self._PROBE, return_value=""):
            out = self._call(action="move", path="a.py", destination="b.py")
        self.assertTrue(out.rstrip().endswith("search for them."))

    def test_delete_does_not_ask_the_linters_anything(self) -> None:
        self._write("a.py", "x = 1\n")
        with mock.patch(self._PROBE, return_value="noise") as probe:
            self._call(action="delete", paths=["a.py"])
        probe.assert_not_called()


class MkdirTest(_ManageTest):
    def test_parents_are_created(self) -> None:
        out = self._call(action="mkdir", path="a/b/c")
        self.assertIn("Created directory", out)
        self.assertTrue((self.root / "a/b/c").is_dir())

    def test_an_existing_directory_is_not_an_error(self) -> None:
        (self.root / "a").mkdir()
        self.assertIn("already exists", self._call(action="mkdir", path="a"))

    def test_an_existing_file_in_the_way_is_refused(self) -> None:
        self._write("a")
        self.assertIn("not a directory", self._call(action="mkdir", path="a"))


class ReviewIntegrationTest(_ManageTest):
    """The whole point: a deletion the user can see and undo.

    Everything above checks that a baseline was recorded. This checks that the
    baseline is worth something, by running it through the store the review
    panel actually reads.
    """

    KEY = "websocket:chat-1"

    def test_a_deletion_is_listed_as_deleted_and_rejecting_brings_it_back(self) -> None:
        target = self._write("a.py", "original\n")
        self._call(action="delete", paths=["a.py"])

        review = PendingReviewStore(self.root)
        review.merge_turn(self.KEY, dict(self.recorder.files))
        changes = review.list_changes(self.KEY)
        self.assertEqual([change["status"] for change in changes], ["deleted"])

        restored, deleted = review.reject(self.KEY)
        self.assertEqual((restored, deleted), (1, 0))
        self.assertEqual(target.read_bytes(), b"original\n")

    def test_a_rename_is_two_changes_that_can_be_rejected_together(self) -> None:
        self._write("old.py", "content\n")
        self._call(action="move", path="old.py", destination="new.py")

        review = PendingReviewStore(self.root)
        review.merge_turn(self.KEY, dict(self.recorder.files))
        statuses = {
            Path(change["path"]).name: change["status"]
            for change in review.list_changes(self.KEY)
        }
        self.assertEqual(statuses, {"old.py": "deleted", "new.py": "created"})

        review.reject(self.KEY)
        self.assertEqual((self.root / "old.py").read_bytes(), b"content\n")
        self.assertFalse((self.root / "new.py").exists())

    def test_a_checkpoint_restores_a_deleted_tree(self) -> None:
        self._write("pkg/mod.py", "one\n")
        self._write("pkg/sub/other.py", "two\n")
        checkpoints = CheckpointStore(self.root)

        self._call(action="delete", paths=["pkg"], recursive=True)
        meta = checkpoints.save(
            Session(key=self.KEY, messages=[]), files=dict(self.recorder.files)
        )
        checkpoints.restore_files(self.KEY, meta["name"])

        self.assertEqual((self.root / "pkg/mod.py").read_bytes(), b"one\n")
        self.assertEqual((self.root / "pkg/sub/other.py").read_bytes(), b"two\n")


class ContractTest(_ManageTest):
    def test_an_unknown_action_lists_the_valid_ones(self) -> None:
        out = self._call(action="rename", path="a.py", destination="b.py")
        self.assertIn("delete, move, copy, mkdir", out)

    def test_delete_without_paths_is_refused(self) -> None:
        self.assertIn("at least one path", self._call(action="delete"))

    def test_move_without_a_destination_is_refused(self) -> None:
        self._write("a.py")
        self.assertIn("needs destination", self._call(action="move", path="a.py"))

    def test_a_path_outside_the_workspace_is_refused(self) -> None:
        outside = Path(self._tmp.name).parent / "elsewhere.py"
        out = self._call(action="delete", paths=[str(outside)])
        self.assertIn("Error", out)
        self.assertNotIn("Deleted", out)

    def test_the_tool_declares_itself_a_writer(self) -> None:
        """Read-only tools batch in parallel; these operations must not."""
        self.assertFalse(self._tool().read_only)

    def test_no_recorder_bound_is_not_an_error(self) -> None:
        self._write("a.py")
        out = str(_run(self._tool().execute(action="delete", paths=["a.py"])))
        self.assertIn("Deleted", out)


if __name__ == "__main__":
    unittest.main()
