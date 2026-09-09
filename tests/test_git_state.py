# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""The agent used to work with no idea what the repository looked like.

Nothing told it which branch it was on, and nothing told it that a file it was
about to replace held work that existed only in the working tree. That is the
one loss no checkpoint here can undo, because the checkpoint store only knows
the states the agent itself created.

These tests drive real repositories rather than fixture strings: porcelain v2 is
a field-counting format, and an off-by-one in the field index is exactly the bug
a handwritten fixture would be written to agree with.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import mock

from navin.agent.tools.file_state import FileStates
from navin.agent.tools.filesystem import WriteFileTool
from navin.utils import wsl
from navin.utils.git_state import (
    RepoState,
    _status_argv,
    _use_native,
    clear_cache,
    git_state_context_provider,
    repo_state,
    summary_lines,
    uncommitted_note,
)


def _run(coro):
    return asyncio.run(coro)


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, check=False
    )


class _RepoTest(unittest.TestCase):
    def setUp(self) -> None:
        if shutil.which("git") is None:
            self.skipTest("git not installed")
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(clear_cache)
        self.root = Path(self._tmp.name).resolve()
        _git(self.root, "init", "-q", "-b", "main")
        _git(self.root, "config", "user.email", "t@example.com")
        _git(self.root, "config", "user.name", "Test")

    def _commit(self, message: str = "c") -> None:
        _git(self.root, "add", "-A")
        _git(self.root, "commit", "-qm", message)

    def _write(self, name: str, body: str) -> Path:
        target = self.root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
        return target

    def _state(self) -> RepoState:
        clear_cache()
        return repo_state(self.root)


class ReadingTheTreeTest(_RepoTest):
    def test_a_clean_tree_reports_its_branch(self) -> None:
        self._write("a.txt", "one\n")
        self._commit()
        state = self._state()
        self.assertTrue(state.is_repo)
        self.assertEqual(state.branch, "main")
        self.assertFalse(state.dirty)

    def test_a_modified_file_is_unstaged(self) -> None:
        self._write("a.txt", "one\n")
        self._commit()
        self._write("a.txt", "two\n")
        self.assertEqual(self._state().unstaged, ("a.txt",))

    def test_a_staged_file_is_staged(self) -> None:
        self._write("a.txt", "one\n")
        self._commit()
        self._write("a.txt", "two\n")
        _git(self.root, "add", "a.txt")
        state = self._state()
        self.assertEqual(state.staged, ("a.txt",))
        self.assertEqual(state.unstaged, ())

    def test_a_file_both_staged_and_modified_lands_in_both(self) -> None:
        """Porcelain packs two statuses into one record; both must be read."""
        self._write("a.txt", "one\n")
        self._commit()
        self._write("a.txt", "two\n")
        _git(self.root, "add", "a.txt")
        self._write("a.txt", "three\n")
        state = self._state()
        self.assertEqual(state.staged, ("a.txt",))
        self.assertEqual(state.unstaged, ("a.txt",))

    def test_a_new_file_is_untracked(self) -> None:
        self._write("a.txt", "one\n")
        self._commit()
        self._write("b.txt", "new\n")
        self.assertEqual(self._state().untracked, ("b.txt",))

    def test_a_path_with_a_space_survives(self) -> None:
        """The path is the last field; naive splitting truncates it."""
        self._write("a.txt", "one\n")
        self._commit()
        self._write("with space.txt", "x\n")
        self.assertEqual(self._state().untracked, ("with space.txt",))

    def test_a_nested_tracked_path_keeps_its_directories(self) -> None:
        self._write("pkg/deep/mod.py", "x\n")
        self._commit()
        self._write("pkg/deep/mod.py", "y\n")
        self.assertEqual(self._state().unstaged, ("pkg/deep/mod.py",))

    def test_a_wholly_untracked_directory_is_reported_as_the_directory(self) -> None:
        """git collapses it, and so should we: an untracked build tree would
        otherwise arrive as thousands of entries."""
        self._write("a.txt", "one\n")
        self._commit()
        self._write("pkg/deep/mod.py", "x\n")
        self.assertEqual(self._state().untracked, ("pkg/",))

    def test_a_rename_reports_the_destination(self) -> None:
        """A rename record carries two paths; the new one is what gets written."""
        self._write("a.txt", "one\n")
        self._commit()
        _git(self.root, "mv", "a.txt", "b.txt")
        state = self._state()
        self.assertIn("b.txt", state.staged)
        self.assertNotIn("a.txt", state.staged)

    def test_a_rename_does_not_swallow_the_next_record(self) -> None:
        """The source path sits in its own field and must be stepped over."""
        self._write("a.txt", "one\n")
        self._write("keep.txt", "k\n")
        self._commit()
        _git(self.root, "mv", "a.txt", "b.txt")
        self._write("later.txt", "l\n")
        self.assertEqual(self._state().untracked, ("later.txt",))

    def test_a_conflict_is_reported_as_conflicted(self) -> None:
        self._write("c.txt", "base\n")
        self._commit("base")
        _git(self.root, "checkout", "-q", "-b", "other")
        self._write("c.txt", "theirs\n")
        self._commit("theirs")
        _git(self.root, "checkout", "-q", "main")
        self._write("c.txt", "ours\n")
        self._commit("ours")
        _git(self.root, "merge", "other")
        self.assertEqual(self._state().conflicted, ("c.txt",))

    def test_a_detached_head_says_so(self) -> None:
        self._write("a.txt", "one\n")
        self._commit("one")
        self._write("a.txt", "two\n")
        self._commit("two")
        _git(self.root, "checkout", "-q", "HEAD~1")
        state = self._state()
        self.assertTrue(state.detached)
        self.assertEqual(state.branch, "")

    def test_a_repository_with_no_commits_does_not_crash(self) -> None:
        state = self._state()
        self.assertTrue(state.is_repo)
        self.assertEqual(state.head, "")

    def test_a_directory_outside_git_is_not_a_repo(self) -> None:
        with TemporaryDirectory() as plain:
            clear_cache()
            self.assertFalse(repo_state(Path(plain)).is_repo)

    def test_untracked_files_are_not_tracked_changes(self) -> None:
        """An untracked file has no committed version to lose."""
        self._write("a.txt", "one\n")
        self._commit()
        self._write("b.txt", "new\n")
        self.assertEqual(self._state().tracked_changes, frozenset())


class SummaryTest(_RepoTest):
    def test_a_clean_tree_gets_one_line_naming_the_branch(self) -> None:
        self._write("a.txt", "one\n")
        self._commit()
        lines = summary_lines(self._state())
        self.assertEqual(len(lines), 1)
        self.assertIn("branch main", lines[0])
        self.assertIn("clean", lines[0])

    def test_a_dirty_tree_names_the_files_and_warns(self) -> None:
        self._write("a.txt", "one\n")
        self._commit()
        self._write("a.txt", "two\n")
        text = "\n".join(summary_lines(self._state()))
        self.assertIn("a.txt", text)
        self.assertIn("not committed", text)

    def test_untracked_files_alone_do_not_trigger_the_warning(self) -> None:
        """A warning that names nothing teaches the model to skip warnings."""
        self._write("a.txt", "one\n")
        self._commit()
        self._write("scratch.txt", "x\n")
        lines = summary_lines(self._state())
        self.assertEqual(len(lines), 1)
        self.assertIn("untracked", lines[0])
        self.assertNotIn("not committed", lines[0])

    def test_a_long_list_is_capped(self) -> None:
        self._write("a.txt", "one\n")
        self._commit()
        for index in range(12):
            self._write(f"f{index}.txt", "x\n")
        self._commit()
        for index in range(12):
            self._write(f"f{index}.txt", "changed\n")
        text = "\n".join(summary_lines(self._state()))
        self.assertIn("more", text)

    def test_a_plain_directory_produces_no_lines(self) -> None:
        with TemporaryDirectory() as plain:
            clear_cache()
            self.assertEqual(summary_lines(repo_state(Path(plain))), [])


class UncommittedNoteTest(_RepoTest):
    def setUp(self) -> None:
        super().setUp()
        self._write("tracked.py", "committed work\n")
        self._commit()

    def test_a_file_with_uncommitted_changes_is_flagged(self) -> None:
        self._write("tracked.py", "work in progress\n")
        clear_cache()
        note = uncommitted_note(self.root, self.root / "tracked.py")
        self.assertIn("tracked.py", note)
        self.assertIn("uncommitted", note)

    def test_a_committed_file_is_not(self) -> None:
        clear_cache()
        self.assertEqual(uncommitted_note(self.root, self.root / "tracked.py"), "")

    def test_an_untracked_file_is_not(self) -> None:
        self._write("scratch.py", "x\n")
        clear_cache()
        self.assertEqual(uncommitted_note(self.root, self.root / "scratch.py"), "")

    def test_a_sibling_file_is_not_flagged_by_association(self) -> None:
        """The dirty file is not the file being written."""
        self._write("other.py", "y\n")
        self._commit()
        self._write("other.py", "changed\n")
        clear_cache()
        self.assertEqual(uncommitted_note(self.root, self.root / "tracked.py"), "")

    def test_a_path_outside_the_repository_is_not_flagged(self) -> None:
        with TemporaryDirectory() as outside:
            clear_cache()
            note = uncommitted_note(self.root, Path(outside) / "x.py")
            self.assertEqual(note, "")


class OverwriteGuardTest(_RepoTest):
    """write_file replaces whole files and had no check of any kind."""

    def setUp(self) -> None:
        super().setUp()
        self._write("tracked.py", "committed\n")
        self._commit()

    def _tool(self) -> WriteFileTool:
        return WriteFileTool(
            workspace=self.root, allowed_dir=self.root, file_states=FileStates()
        )

    def test_overwriting_uncommitted_work_warns_about_git(self) -> None:
        self._write("tracked.py", "the user's unsaved work\n")
        clear_cache()
        out = _run(self._tool().execute(path="tracked.py", content="clobbered\n"))
        self.assertIn("uncommitted", out)
        self.assertIn("tracked.py", out)

    def test_the_write_still_happens(self) -> None:
        """This is a warning, not a veto: the agent is often asked to do this."""
        self._write("tracked.py", "unsaved\n")
        clear_cache()
        _run(self._tool().execute(path="tracked.py", content="clobbered\n"))
        self.assertEqual(
            (self.root / "tracked.py").read_text(encoding="utf-8"), "clobbered\n"
        )

    def test_a_new_file_warns_about_nothing(self) -> None:
        clear_cache()
        out = _run(self._tool().execute(path="brand_new.py", content="x\n"))
        self.assertNotIn("Warning", out)

    def test_a_clean_file_gets_only_the_read_warning(self) -> None:
        clear_cache()
        out = _run(self._tool().execute(path="tracked.py", content="x\n"))
        self.assertIn("has not been read", out)
        self.assertNotIn("uncommitted", out)

    def test_a_file_already_read_and_clean_is_silent(self) -> None:
        tool = self._tool()
        tool._file_states.record_read(self.root / "tracked.py")
        clear_cache()
        out = _run(tool.execute(path="tracked.py", content="x\n"))
        self.assertNotIn("Warning", out)


class EncodingPreservedTest(_RepoTest):
    """write_file wrote UTF-8 unconditionally, converting files it replaced."""

    def _tool(self) -> WriteFileTool:
        return WriteFileTool(
            workspace=self.root, allowed_dir=self.root, file_states=FileStates()
        )

    def _rewrite(self, name: str, raw: bytes, replacement: str) -> bytes:
        target = self.root / name
        target.write_bytes(raw)
        tool = self._tool()
        tool._file_states.record_read(target)
        clear_cache()
        _run(tool.execute(path=name, content=replacement))
        return target.read_bytes()

    def test_a_cp1252_file_stays_cp1252(self) -> None:
        out = self._rewrite("latin.txt", "café\n".encode("cp1252"), "thé\n")
        self.assertEqual(out, "thé\n".encode("cp1252"))

    def test_a_utf8_bom_is_kept(self) -> None:
        out = self._rewrite("bom.txt", "\ufeffhello\n".encode("utf-8"), "goodbye\n")
        self.assertTrue(out.startswith(b"\xef\xbb\xbf"))

    def test_a_utf16_file_stays_utf16(self) -> None:
        out = self._rewrite("wide.txt", "hello\n".encode("utf-16"), "goodbye\n")
        self.assertEqual(out.decode("utf-16"), "goodbye\n")

    def test_a_new_file_is_plain_utf8(self) -> None:
        clear_cache()
        _run(self._tool().execute(path="fresh.txt", content="café\n"))
        self.assertEqual(
            (self.root / "fresh.txt").read_bytes(), "café\n".encode("utf-8")
        )


class WslProjectRoutingTest(unittest.TestCase):
    """A WSL project driven from Windows froze every agent turn.

    The desktop gateway on Windows sees such a project as
    ``\\\\wsl.localhost\\...``. libgit2 walked that whole tree through the 9p
    redirector - minutes per status, no timeout - before the first model token
    of every turn, while the web gateway inside the distribution answered in
    milliseconds. The status must instead run inside the distribution, and the
    per-turn provider must never wait without a bound.
    """

    UNC = "\\\\wsl.localhost\\Ubuntu\\home\\aymen\\projects\\demo"

    def test_native_is_skipped_for_a_wsl_unc_project(self) -> None:
        self.assertFalse(_use_native(Path(self.UNC)))

    def test_native_stays_available_for_a_local_project(self) -> None:
        self.assertTrue(_use_native(Path("/home/aymen/projects/demo")))

    def test_status_runs_inside_the_distribution(self) -> None:
        with (
            mock.patch.object(wsl, "wsl_executable", return_value="wsl.exe"),
            mock.patch.object(wsl, "resolve_distro", side_effect=lambda name: name),
        ):
            argv = _status_argv(Path(self.UNC), platform="win32")
        assert argv is not None
        self.assertEqual(argv[0], "wsl.exe")
        self.assertIn("git", argv)
        self.assertIn("--porcelain=v2", argv)
        self.assertLess(argv.index("git"), argv.index("status"))

    def test_a_local_project_keeps_plain_git(self) -> None:
        if shutil.which("git") is None:
            self.skipTest("git not installed")
        argv = _status_argv(Path("/some/project"), platform="linux")
        assert argv is not None
        self.assertTrue(argv[0].endswith("git"))
        self.assertIn("-C", argv)

    def test_the_provider_gives_up_rather_than_hold_the_turn(self) -> None:
        request = SimpleNamespace(workspace=Path("/some/project"))

        def slow_read(root: Path) -> RepoState:
            time.sleep(0.5)
            return RepoState(is_repo=True, branch="main")

        with (
            mock.patch("navin.utils.git_state.repo_state", side_effect=slow_read),
            mock.patch("navin.utils.git_state._PROVIDER_TIMEOUT_S", 0.05),
        ):
            result = _run(git_state_context_provider(request))
        self.assertIsNone(result)


class CacheTest(_RepoTest):
    def test_a_refresh_sees_a_change_the_cache_would_hide(self) -> None:
        self._write("a.txt", "one\n")
        self._commit()
        self.assertFalse(repo_state(self.root).dirty)
        self._write("a.txt", "two\n")
        self.assertTrue(repo_state(self.root, refresh=True).dirty)

    def test_the_cache_answers_repeat_calls(self) -> None:
        """Every turn asks once; the write path may ask again in the same turn."""
        self._write("a.txt", "one\n")
        self._commit()
        clear_cache()
        self.assertIs(repo_state(self.root), repo_state(self.root))


if __name__ == "__main__":
    unittest.main()
