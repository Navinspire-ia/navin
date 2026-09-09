# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Leading-slash path handling and dead-end error messages across file tools.

Models routinely write `/src/app.py` meaning the top of the project rather than
the filesystem root. Rejecting that wastes a turn, and the rejection used to be
phrased as a hard policy boundary, which told the agent not to retry at all.
"""

import asyncio
import os
import tempfile
import unittest
from pathlib import Path

from navin.agent.tools import database, image_generation, shell, video_generation
from navin.agent.tools.apply_patch import ApplyPatchTool
from navin.agent.tools.code_index import CodeIndexTool
from navin.agent.tools.file_state import FileStates
from navin.agent.tools.filesystem import (
    EditFileTool,
    ListDirTool,
    ReadFileTool,
    WriteFileTool,
    _is_blocked_device,
)
from navin.agent.tools.metagraph import MetagraphTool
from navin.agent.tools.path_utils import project_rooted_path
from navin.agent.tools.quality import LintTool
from navin.agent.tools.search import FindFilesTool, GrepTool
from navin.agent.tools.shell import ExecTool
from navin.apps.cli import service as cli_service
from navin.apps.cli.service import CliAppError, CliAppManager
from navin.utils.path import normalize_relative_path


def _run(coro) -> str:
    result = asyncio.run(coro)
    return str(getattr(result, "content", result))


def _outside_dir(test: unittest.TestCase) -> Path:
    """A real directory outside the project, on any OS.

    These tests need somewhere that genuinely exists but is off-limits. Naming a
    system path like /etc looks convenient and is wrong twice over: Windows has
    no such directory, and macOS has no /etc/hostname, so the case under test
    quietly stops being exercised instead of failing loudly.
    """
    tmp = tempfile.TemporaryDirectory()
    test.addCleanup(tmp.cleanup)
    return Path(tmp.name).resolve()


class _WorkspaceTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()
        (self.root / "atlas").mkdir()
        (self.root / "atlas" / "notes.md").write_text("hello atlas\n", encoding="utf-8")
        (self.root / "atlas" / "code.py").write_text("def marker():\n    return 1\n", encoding="utf-8")
        self.outside = _outside_dir(self)
        self.outside_file = self.outside / "outside.txt"
        self.outside_file.write_text("outside content\n", encoding="utf-8")
        self.states = FileStates()

    def _kw(self, *, restricted: bool = True) -> dict:
        return {
            "workspace": self.root,
            "allowed_dir": self.root if restricted else None,
            "file_states": self.states,
        }


class LeadingSlashTest(_WorkspaceTest):
    def test_list_dir_reads_a_leading_slash_as_the_project_root(self) -> None:
        out = _run(ListDirTool(**self._kw()).execute(path="/atlas"))
        self.assertIn("notes.md", out)
        self.assertNotIn("Error", out)

    def test_read_file_reads_a_leading_slash_as_the_project_root(self) -> None:
        out = _run(ReadFileTool(**self._kw()).execute(path="/atlas/notes.md"))
        self.assertIn("hello atlas", out)

    def test_grep_accepts_a_leading_slash_scope(self) -> None:
        out = _run(GrepTool(**self._kw()).execute(pattern="marker", path="/atlas"))
        self.assertIn("code.py", out)

    def test_find_files_accepts_a_leading_slash_scope(self) -> None:
        out = _run(FindFilesTool(**self._kw()).execute(query="notes", path="/atlas"))
        self.assertIn("notes.md", out)

    def test_edit_file_accepts_a_leading_slash_path(self) -> None:
        _run(ReadFileTool(**self._kw()).execute(path="/atlas/notes.md"))
        out = _run(EditFileTool(**self._kw()).execute(
            path="/atlas/notes.md", old_text="hello atlas", new_text="bonjour atlas",
        ))
        self.assertIn("Successfully edited", out)
        self.assertEqual(
            (self.root / "atlas" / "notes.md").read_text(encoding="utf-8"), "bonjour atlas\n",
        )

    def test_write_file_accepts_a_leading_slash_path(self) -> None:
        out = _run(WriteFileTool(**self._kw()).execute(path="/atlas/new.txt", content="fresh\n"))
        self.assertNotIn("Error", out)
        self.assertEqual((self.root / "atlas" / "new.txt").read_text(encoding="utf-8"), "fresh\n")

    def test_apply_patch_accepts_a_leading_slash_path(self) -> None:
        _run(ReadFileTool(**self._kw()).execute(path="/atlas/code.py"))
        out = _run(ApplyPatchTool(workspace=self.root, file_states=self.states).execute(
            edits=[{
                "path": "/atlas/code.py",
                "action": "replace",
                "old_text": "return 1",
                "new_text": "return 2",
            }],
        ))
        self.assertIn("Patch applied", out)
        self.assertIn("return 2", (self.root / "atlas" / "code.py").read_text(encoding="utf-8"))

    def test_a_relative_path_still_works(self) -> None:
        out = _run(ListDirTool(**self._kw()).execute(path="atlas"))
        self.assertIn("notes.md", out)

    def test_a_real_absolute_path_inside_the_project_is_untouched(self) -> None:
        out = _run(ReadFileTool(**self._kw()).execute(path=str(self.root / "atlas" / "notes.md")))
        self.assertIn("hello atlas", out)


class BoundaryHonestyTest(_WorkspaceTest):
    def test_an_existing_forbidden_path_still_reports_the_boundary(self) -> None:
        # The rewrite must not disguise a real policy stop as a missing file:
        # the agent would otherwise assume the resource simply is not there.
        out = _run(ReadFileTool(**self._kw()).execute(path=str(self.outside_file)))
        self.assertIn("outside allowed directory", out)

    def test_a_forbidden_path_with_a_project_counterpart_prefers_the_project(self) -> None:
        (self.root / "etc").mkdir()
        (self.root / "etc" / "hostname").write_text("project copy\n", encoding="utf-8")
        out = _run(ReadFileTool(**self._kw()).execute(path="/etc/hostname"))
        self.assertIn("project copy", out)

    def test_the_rewrite_cannot_escape_the_project(self) -> None:
        out = _run(ReadFileTool(**self._kw()).execute(path="/../../etc/passwd"))
        self.assertIn("Error", out)
        self.assertNotIn("root:", out)


class UnconfinedReachTest(_WorkspaceTest):
    """Without a boundary the agent may reach the whole filesystem.

    The project-relative reading of a leading slash must not quietly capture a
    deliberate destination outside the project, which would send a file
    somewhere the caller never asked for.
    """

    def _kw(self, *, restricted: bool = False) -> dict:
        return super()._kw(restricted=restricted)

    def test_a_write_to_a_real_outside_directory_reaches_it(self) -> None:
        target = self.outside / "report.txt"
        _run(WriteFileTool(**self._kw()).execute(path=str(target), content="x\n"))
        self.assertTrue(target.is_file())
        self.assertEqual([], list(self.root.rglob("report.txt")))

    def test_a_read_of_an_outside_file_reaches_it(self) -> None:
        target = self.outside / "note.txt"
        target.write_text("outside content\n", encoding="utf-8")
        out = _run(ReadFileTool(**self._kw()).execute(path=str(target)))
        self.assertIn("outside content", out)

    def test_a_leading_slash_path_absent_everywhere_lands_in_the_project(self) -> None:
        # No such directory exists at the filesystem root, so this is a project
        # path written with a slash rather than a destination outside.
        _run(WriteFileTool(**self._kw()).execute(path="/docs/new/guide.md", content="x\n"))
        self.assertTrue((self.root / "docs" / "new" / "guide.md").is_file())

    def test_an_existing_project_directory_still_wins(self) -> None:
        out = _run(ListDirTool(**self._kw()).execute(path="/atlas"))
        self.assertIn("notes.md", out)

    def test_a_confined_write_cannot_escape(self) -> None:
        target = self.outside / "confined.txt"
        _run(WriteFileTool(**self._kw(restricted=True)).execute(path=str(target), content="x\n"))
        self.assertFalse(target.exists())


class ConfinedEscapeTest(_WorkspaceTest):
    """The project-relative rewrite must never widen what a boundary allows."""

    def setUp(self) -> None:
        super().setUp()
        self._outside = tempfile.TemporaryDirectory()
        self.addCleanup(self._outside.cleanup)
        self.secret = Path(self._outside.name).resolve() / "secret.txt"
        self.secret.write_text("SENSITIVE\n", encoding="utf-8")

    def _read(self, path: str) -> str:
        return _run(ReadFileTool(**self._kw()).execute(path=path))

    def test_every_escape_shape_is_refused(self) -> None:
        outside = str(self.secret)
        for attempt in (
            outside,
            "../" * 6 + outside.lstrip("/"),
            "/../../.." + outside,
            "atlas/../../" + self.secret.name,
            "~/.ssh/id_rsa",
            "/proc/self/environ",
            "atlas/./../../etc/passwd",
        ):
            with self.subTest(attempt=attempt):
                out = self._read(attempt)
                self.assertNotIn("SENSITIVE", out)
                self.assertNotIn("root:", out)

    def test_a_symlink_out_of_the_project_is_refused(self) -> None:
        link = self.root / "atlas" / "link"
        try:
            link.symlink_to(self.secret)
        except OSError as exc:  # Windows needs admin rights or developer mode
            self.skipTest(f"symlinks unavailable on this platform: {exc}")
        self.assertNotIn("SENSITIVE", self._read("atlas/link"))


class MissingPathMessageTest(_WorkspaceTest):
    def test_a_missing_directory_is_not_reported_as_a_policy_boundary(self) -> None:
        out = _run(ListDirTool(**self._kw()).execute(path="/nope"))
        self.assertIn("Directory not found", out)
        self.assertNotIn("hard policy boundary", out)

    def test_a_mistyped_file_recovers_by_reading_its_neighbour(self) -> None:
        # read_file now resolves an unambiguous near-miss itself instead of
        # only suggesting it; the substitution stays disclosed in the output.
        out = _run(ReadFileTool(**self._kw()).execute(path="/atlas/note.md"))
        self.assertIn("closest match", out)
        self.assertIn("notes.md", out)
        self.assertIn("hello atlas", out)

    def test_a_deep_missing_path_names_the_deepest_existing_directory(self) -> None:
        out = _run(ListDirTool(**self._kw()).execute(path="/atlas/deep/deeper"))
        self.assertIn("Deepest existing directory", out)
        self.assertIn("atlas", out)

    def test_the_project_root_is_named_rather_than_shown_as_a_dot(self) -> None:
        out = _run(ReadFileTool(**self._kw()).execute(path="/missing/file.txt"))
        self.assertIn("the project root", out)
        self.assertNotIn("directory: .", out)


class NormalizeRelativePathTest(unittest.TestCase):
    """`str.lstrip("./")` strips a character set, not a prefix.

    Every path in a hidden directory used to lose its leading dot, so tools
    reported a path the agent had never written.
    """

    def test_a_hidden_directory_keeps_its_dot(self) -> None:
        self.assertEqual(normalize_relative_path(".github/ci.yml"), ".github/ci.yml")

    def test_a_hidden_file_keeps_its_dot(self) -> None:
        self.assertEqual(normalize_relative_path(".env"), ".env")

    def test_a_dot_slash_prefix_is_dropped(self) -> None:
        self.assertEqual(normalize_relative_path("./src/app.py"), "src/app.py")

    def test_a_leading_slash_is_dropped(self) -> None:
        self.assertEqual(normalize_relative_path("/src/app.py"), "src/app.py")

    def test_a_dot_slash_prefix_before_a_hidden_directory_is_dropped(self) -> None:
        self.assertEqual(normalize_relative_path("./.github/ci.yml"), ".github/ci.yml")

    def test_a_leading_slash_before_a_hidden_directory_is_dropped(self) -> None:
        self.assertEqual(normalize_relative_path("/.github/ci.yml"), ".github/ci.yml")

    def test_repeated_prefixes_are_dropped(self) -> None:
        self.assertEqual(normalize_relative_path("/././src/app.py"), "src/app.py")

    def test_a_parent_reference_is_left_for_the_caller_to_reject(self) -> None:
        self.assertEqual(normalize_relative_path("../etc/passwd"), "../etc/passwd")

    def test_backslashes_are_normalized(self) -> None:
        self.assertEqual(normalize_relative_path("src\\app.py"), "src/app.py")

    def test_surrounding_whitespace_is_dropped(self) -> None:
        self.assertEqual(normalize_relative_path("  src/app.py  "), "src/app.py")

    def test_empty_input_is_empty(self) -> None:
        self.assertEqual(normalize_relative_path(None), "")
        self.assertEqual(normalize_relative_path("   "), "")


class HiddenDirectoryTest(_WorkspaceTest):
    """Tools that key on a project-relative path must handle dot directories."""

    def setUp(self) -> None:
        super().setUp()
        (self.root / ".github").mkdir()
        (self.root / ".github" / "ci.py").write_text("import os\n", encoding="utf-8")

    def _lint(self, path: str) -> str:
        return _run(LintTool(**self._kw()).execute(action="file", path=path))

    def test_lint_reaches_a_file_in_a_hidden_directory(self) -> None:
        self.assertNotIn("Error", self._lint(".github/ci.py"))

    def test_lint_accepts_the_dot_slash_form(self) -> None:
        self.assertNotIn("Error", self._lint("./.github/ci.py"))

    def test_lint_accepts_the_leading_slash_form(self) -> None:
        self.assertNotIn("Error", self._lint("/.github/ci.py"))

    def test_a_missing_hidden_file_is_reported_as_written(self) -> None:
        # The old normalization leaked "github/ci.py", a path the agent never
        # asked for, which is the worst possible hint.
        out = self._lint(".github/absent.py")
        self.assertIn(".github/absent.py", out)
        self.assertNotIn("File not found: github/", out)

    def test_code_index_reaches_a_hidden_directory(self) -> None:
        out = _run(CodeIndexTool(**self._kw()).execute(action="outline", path=".github/ci.py"))
        self.assertIn(".github/ci.py", out)
        self.assertNotIn("is not in the index", out)

    def test_metagraph_reaches_a_hidden_directory(self) -> None:
        out = _run(MetagraphTool(**self._kw()).execute(action="file", path=".github/ci.py"))
        self.assertIn(".github/ci.py", out)


class ExecWorkingDirTest(unittest.TestCase):
    """`working_dir` must be read against the project, not against navin's cwd."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()
        (self.root / "atlas").mkdir()
        self.outside = _outside_dir(self)

    def _pwd(self, restrict: bool, **kw) -> str:
        tool = ExecTool(working_dir=str(self.root), restrict_to_workspace=restrict)
        return _run(tool.execute(command="pwd", **kw))

    def test_a_relative_working_dir_lands_in_the_project(self) -> None:
        for restrict in (True, False):
            with self.subTest(restrict=restrict):
                self.assertIn(str(self.root / "atlas"), self._pwd(restrict, working_dir="atlas"))

    def test_a_leading_slash_working_dir_lands_in_the_project(self) -> None:
        for restrict in (True, False):
            with self.subTest(restrict=restrict):
                self.assertIn(str(self.root / "atlas"), self._pwd(restrict, working_dir="/atlas"))

    def test_a_dot_slash_working_dir_lands_in_the_project(self) -> None:
        self.assertIn(str(self.root / "atlas"), self._pwd(True, working_dir="./atlas"))

    def test_a_real_absolute_working_dir_inside_the_project_still_works(self) -> None:
        out = self._pwd(True, working_dir=str(self.root / "atlas"))
        self.assertIn(str(self.root / "atlas"), out)

    def test_no_working_dir_uses_the_project_root(self) -> None:
        self.assertIn(str(self.root), self._pwd(True))

    def test_a_missing_working_dir_says_it_is_project_relative(self) -> None:
        out = self._pwd(True, working_dir="nope")
        self.assertIn("working_dir not found", out)
        self.assertIn("project root", out)

    def test_an_outside_working_dir_is_still_refused_when_restricted(self) -> None:
        out = self._pwd(True, working_dir=str(self.outside))
        self.assertIn("outside the configured workspace", out)


class CliAppWorkingDirTest(unittest.TestCase):
    """The CLI-app runner resolves its own cwd and needs the same reading."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()
        (self.root / "atlas").mkdir()
        self.outside = _outside_dir(self)
        self.manager = CliAppManager(workspace=self.root)

    def _cwd(self, working_dir, *, restrict: bool = True) -> str:
        return str(self.manager._resolve_cwd(working_dir, restrict_to_workspace=restrict))

    def test_a_relative_working_dir_lands_in_the_project(self) -> None:
        self.assertEqual(self._cwd("atlas"), str(self.root / "atlas"))

    def test_a_leading_slash_working_dir_lands_in_the_project(self) -> None:
        self.assertEqual(self._cwd("/atlas"), str(self.root / "atlas"))

    def test_no_working_dir_uses_the_project_root(self) -> None:
        self.assertEqual(self._cwd(None), str(self.root))

    def test_a_missing_working_dir_is_refused_with_a_hint(self) -> None:
        with self.assertRaises(CliAppError) as ctx:
            self._cwd("nope")
        self.assertIn("project root", str(ctx.exception))

    def test_an_outside_working_dir_is_still_refused_when_restricted(self) -> None:
        with self.assertRaises(CliAppError) as ctx:
            self._cwd(str(self.outside))
        self.assertIn("outside the configured workspace", str(ctx.exception))


class SharedHelperTest(_WorkspaceTest):
    """Tools that resolve paths directly must use the same reading.

    They bypass the filesystem base class, so the convention has to be applied
    at their own call site or a leading slash silently means something else
    there than everywhere else.
    """

    def test_an_existing_project_path_is_rewritten(self) -> None:
        self.assertEqual(
            project_rooted_path("/atlas/notes.md", self.root, [self.root]),
            str(self.root / "atlas" / "notes.md"),
        )

    def test_a_missing_path_is_still_read_against_the_project(self) -> None:
        self.assertEqual(
            project_rooted_path("/atlas/absent.md", self.root, [self.root]),
            str(self.root / "atlas" / "absent.md"),
        )

    def test_a_relative_path_is_left_alone(self) -> None:
        self.assertEqual(project_rooted_path("atlas/notes.md", self.root, [self.root]), "atlas/notes.md")

    def test_an_existing_permitted_absolute_path_is_left_alone(self) -> None:
        literal = str(self.root / "atlas" / "notes.md")
        self.assertEqual(project_rooted_path(literal, self.root, [self.root]), literal)

    def test_an_existing_forbidden_path_is_left_alone_to_fail(self) -> None:
        literal = str(self.outside_file)
        self.assertEqual(project_rooted_path(literal, self.root, [self.root]), literal)

    def test_without_a_workspace_nothing_is_rewritten(self) -> None:
        self.assertEqual(project_rooted_path("/atlas", None, []), "/atlas")

    def test_a_bare_slash_is_left_alone(self) -> None:
        self.assertEqual(project_rooted_path("/", self.root, [self.root]), "/")

    def test_every_direct_caller_uses_the_helper(self) -> None:
        for module in (database, image_generation, video_generation, shell, cli_service):
            source = Path(module.__file__).read_text(encoding="utf-8")
            with self.subTest(module=module.__name__):
                self.assertIn("project_rooted_path(", source)


class FindFilesQueryHintTest(_WorkspaceTest):
    def test_a_wildcard_in_query_explains_the_glob_parameter(self) -> None:
        out = _run(FindFilesTool(**self._kw()).execute(query="*.md", path="/atlas"))
        self.assertIn("glob=", out)
        self.assertIn("substring", out)

    def test_the_same_pattern_works_as_a_glob(self) -> None:
        out = _run(FindFilesTool(**self._kw()).execute(glob="*.md", path="/atlas"))
        self.assertIn("notes.md", out)

    def test_a_genuinely_empty_result_stays_terse(self) -> None:
        out = _run(FindFilesTool(**self._kw()).execute(query="absent", path="/atlas"))
        self.assertEqual(out.strip(), "No files found")

    def test_a_glob_that_matches_nothing_stays_terse(self) -> None:
        out = _run(FindFilesTool(**self._kw()).execute(glob="*.rs", path="/atlas"))
        self.assertEqual(out.strip(), "No files found")


class UnknownActionTest(_WorkspaceTest):
    """An unrecognized action must name the ones that work.

    Schema validation catches this first on the normal agent path, but the
    dispatch is still reachable from subagents, MCP clients and direct calls,
    and a bare "unknown action" leaves the caller with nowhere to go.
    """

    def test_the_valid_actions_are_listed(self) -> None:
        out = _run(CodeIndexTool(**self._kw()).execute(action="symbol", name="marker"))
        self.assertIn("callers", out)
        self.assertIn("outline", out)

    def test_a_near_miss_is_suggested(self) -> None:
        out = _run(LintTool(**self._kw()).execute(action="filee", path="atlas/code.py"))
        self.assertIn("Did you mean action=file?", out)

    def test_the_result_is_flagged_as_an_error(self) -> None:
        result = asyncio.run(MetagraphTool(**self._kw()).execute(action="galaxy"))
        self.assertTrue(getattr(result, "is_error", False))

    def test_the_listed_values_come_from_the_declared_schema(self) -> None:
        tool = MetagraphTool(**self._kw())
        declared = tool.parameters["properties"]["action"]["enum"]
        out = _run(tool.execute(action="nope"))
        for action in declared:
            self.assertIn(action, out)

    def test_an_unknown_patch_action_lists_the_valid_ones(self) -> None:
        out = _run(
            ApplyPatchTool(workspace=self.root, allowed_dir=self.root, file_states=self.states).execute(
                edits=[{"path": "atlas/code.py", "action": "append", "new_text": "x"}]
            )
        )
        self.assertIn("replace", out)
        self.assertIn("add", out)


class ReadWindowProgressTest(_WorkspaceTest):
    """A first line wider than the whole character budget must not stall.

    The window used to come back empty with a "use offset=N to continue" that
    pointed at the same N, and the agent replayed the identical call forever.
    """

    def setUp(self) -> None:
        super().setUp()
        wide = "x" * (ReadFileTool._MAX_CHARS + 500)
        (self.root / "wide.txt").write_text(
            wide + "\nafter the wide line\n", encoding="utf-8"
        )

    def test_the_oversized_line_arrives_truncated_and_says_so(self) -> None:
        out = _run(ReadFileTool(**self._kw()).execute(path="wide.txt"))
        self.assertIn("line truncated", out)
        self.assertIn("chars omitted", out)

    def test_the_read_moves_past_the_oversized_line_in_one_call(self) -> None:
        # The per-line cap keeps the wide line to 2 000 chars, so the rest of
        # the file fits in the same window instead of needing a second read.
        out = _run(ReadFileTool(**self._kw()).execute(path="wide.txt"))
        self.assertIn("after the wide line", out)
        self.assertIn("End of file - 2 lines total", out)
        self.assertLess(len(out), ReadFileTool._MAX_LINE_CHARS + 200)

    def test_a_window_full_of_wide_lines_still_advances(self) -> None:
        # 128k budget / ~2k per clipped line: the window stops early and the
        # suggested offset points past what was shown, never at itself.
        lines = ["y" * (ReadFileTool._MAX_LINE_CHARS + 10)] * 100
        (self.root / "wide_many.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
        out = _run(ReadFileTool(**self._kw()).execute(path="wide_many.txt"))
        shown = out.count("(line truncated")
        self.assertGreater(shown, 0)
        if "to continue" in out:
            self.assertIn(f"Use offset={shown + 1} to continue", out)
        else:
            self.assertEqual(shown, 100)


class RecursiveListUnderNoisyAncestorTest(unittest.TestCase):
    """The ignore list judges names below the listed directory only.

    Filtering on the absolute path made a workspace living under an ancestor
    named build, dist or venv list as empty - a false answer, not a filter.
    """

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve() / "build" / "proj"
        (self.root / "src").mkdir(parents=True)
        (self.root / "src" / "app.py").write_text("x = 1\n", encoding="utf-8")

    def _list(self) -> str:
        tool = ListDirTool(
            workspace=self.root, allowed_dir=self.root, file_states=FileStates()
        )
        return _run(tool.execute(path=".", recursive=True))

    def test_a_workspace_under_a_directory_named_build_still_lists(self) -> None:
        out = self._list()
        self.assertIn("app.py", out)
        self.assertNotIn("is empty", out)

    def test_noise_directories_inside_the_workspace_stay_hidden(self) -> None:
        (self.root / "node_modules").mkdir()
        (self.root / "node_modules" / "lib.js").write_text("x\n", encoding="utf-8")
        out = self._list()
        self.assertNotIn("lib.js", out)


class DeviceReadBlockTest(unittest.TestCase):
    """Only what can actually hang a read is refused under /dev.

    /dev/shm is an ordinary tmpfs and /dev/null reads as instant EOF; blocking
    the whole /dev/ prefix turned both into false "could hang" refusals.
    """

    def test_dev_null_is_not_called_a_hazard(self) -> None:
        self.assertFalse(_is_blocked_device("/dev/null"))

    def test_the_named_infinite_devices_stay_blocked(self) -> None:
        self.assertTrue(_is_blocked_device("/dev/zero"))
        self.assertTrue(_is_blocked_device("/dev/urandom"))

    @unittest.skipUnless(os.path.exists("/dev/ptmx"), "no /dev/ptmx on this host")
    def test_a_character_device_outside_the_list_stays_blocked(self) -> None:
        self.assertTrue(_is_blocked_device("/dev/ptmx"))

    @unittest.skipUnless(os.path.isdir("/dev/shm"), "no /dev/shm on this host")
    def test_a_regular_file_under_dev_shm_reads_normally(self) -> None:
        with tempfile.NamedTemporaryFile(
            "w", dir="/dev/shm", suffix=".txt", delete=False
        ) as handle:
            handle.write("shm content\n")
        self.addCleanup(os.unlink, handle.name)
        self.assertFalse(_is_blocked_device(handle.name))
        tool = ReadFileTool(
            workspace=Path(tempfile.gettempdir()), file_states=FileStates()
        )
        out = _run(tool.execute(path=handle.name))
        self.assertIn("shm content", out)


if __name__ == "__main__":
    unittest.main()
