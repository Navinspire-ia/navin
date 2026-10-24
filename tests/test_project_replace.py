# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for the search glob filters and project-wide replace."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from navin.security.workspace_access import default_workspace_scope
from navin.webui.project_search import (
    ProjectSearchError,
    _glob_matches,
    expand_replacement,
    replace_payload,
    search_payload,
    split_globs,
)


class GlobFilterTest(unittest.TestCase):
    def test_a_bare_extension_matches_at_any_depth(self):
        self.assertTrue(_glob_matches("src/deep/a.ts", "*.ts"))
        self.assertTrue(_glob_matches("a.ts", "*.ts"))
        self.assertFalse(_glob_matches("src/a.js", "*.ts"))

    def test_a_bare_folder_name_means_everything_under_it(self):
        self.assertTrue(_glob_matches("tests/unit/a.py", "tests"))
        self.assertFalse(_glob_matches("src/a.py", "tests"))

    def test_an_explicit_double_star_works(self):
        self.assertTrue(_glob_matches("src/a/b/c.ts", "src/**"))
        self.assertFalse(_glob_matches("lib/a.ts", "src/**"))

    def test_splits_and_trims_a_comma_list(self):
        self.assertEqual(split_globs(" *.ts , *.tsx "), ["*.ts", "*.tsx"])
        self.assertEqual(split_globs(""), [])
        self.assertEqual(split_globs(None), [])


class ExpandReplacementTest(unittest.TestCase):
    def test_plain_text_keeps_a_dollar_literal(self):
        self.assertEqual(expand_replacement("cost: $1", regex=False), "cost: $1")

    def test_regex_mode_turns_dollar_one_into_a_group_reference(self):
        self.assertEqual(expand_replacement("$1", regex=True), "\\g<1>")
        self.assertEqual(expand_replacement("${2}x", regex=True), "\\g<2>x")

    def test_a_doubled_dollar_is_an_escaped_literal(self):
        self.assertEqual(expand_replacement("$$", regex=True), "$")

    def test_a_typed_backslash_stays_literal(self):
        self.assertEqual(expand_replacement("a\\b", regex=False), "a\\\\b")


class ReplaceTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        self.addCleanup(self._tmp.cleanup)

    def scope(self):
        return default_workspace_scope(self.root, True)

    def write(self, rel: str, text: str) -> Path:
        target = self.root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        return target

    def replace(self, query: str, replacement: str, **kwargs):
        return replace_payload(self.scope(), query, replacement, **kwargs)

    def test_replaces_across_every_matching_file(self):
        self.write("a.txt", "hello world")
        self.write("src/b.txt", "hello there")
        payload = self.replace("hello", "bonjour")
        self.assertEqual(payload["files_changed"], 2)
        self.assertEqual(payload["replacements"], 2)
        self.assertEqual((self.root / "a.txt").read_text(), "bonjour world")

    def test_counts_every_occurrence_in_one_file(self):
        self.write("a.txt", "x x x")
        payload = self.replace("x", "y")
        self.assertEqual(payload["replacements"], 3)
        self.assertEqual((self.root / "a.txt").read_text(), "y y y")

    def test_paths_narrow_the_work_to_one_file(self):
        self.write("a.txt", "hello")
        self.write("b.txt", "hello")
        self.replace("hello", "hi", paths=["a.txt"])
        self.assertEqual((self.root / "a.txt").read_text(), "hi")
        self.assertEqual((self.root / "b.txt").read_text(), "hello")

    def test_a_regex_group_reference_is_expanded(self):
        self.write("a.txt", "foo=1")
        self.replace(r"(\w+)=(\d+)", r"$2=$1", regex=True)
        self.assertEqual((self.root / "a.txt").read_text(), "1=foo")

    def test_case_insensitive_by_default(self):
        self.write("a.txt", "Hello hello")
        payload = self.replace("hello", "hi")
        self.assertEqual(payload["replacements"], 2)

    def test_case_sensitive_when_asked(self):
        self.write("a.txt", "Hello hello")
        payload = self.replace("hello", "hi", case_sensitive=True)
        self.assertEqual(payload["replacements"], 1)
        self.assertEqual((self.root / "a.txt").read_text(), "Hello hi")

    def test_crlf_line_endings_survive_a_replace(self):
        # Universal-newline translation on read would rewrite every line, so a
        # one-word change would land as a whole-file diff on a Windows checkout.
        target = self.root / "a.txt"
        target.write_bytes(b"one\r\nhello\r\ntwo\r\n")
        self.replace("hello", "hi")
        self.assertEqual(target.read_bytes(), b"one\r\nhi\r\ntwo\r\n")

    def test_include_limits_the_files_touched(self):
        self.write("a.ts", "hello")
        self.write("b.js", "hello")
        self.replace("hello", "hi", include="*.ts")
        self.assertEqual((self.root / "a.ts").read_text(), "hi")
        self.assertEqual((self.root / "b.js").read_text(), "hello")

    def test_exclude_spares_the_files_named(self):
        self.write("a.ts", "hello")
        self.write("b.js", "hello")
        self.replace("hello", "hi", exclude="*.js")
        self.assertEqual((self.root / "a.ts").read_text(), "hi")
        self.assertEqual((self.root / "b.js").read_text(), "hello")

    def test_refuses_a_path_outside_the_project(self):
        with tempfile.TemporaryDirectory() as outside:
            target = Path(outside) / "keep.txt"
            target.write_text("hello")
            with self.assertRaises(ProjectSearchError) as ctx:
                self.replace("hello", "hi", paths=[str(target)])
            self.assertEqual(ctx.exception.status, 403)
            self.assertEqual(target.read_text(), "hello")

    def test_refuses_an_empty_query(self):
        with self.assertRaises(ProjectSearchError):
            self.replace("  ", "x")

    def test_leaves_no_temporary_file_behind(self):
        self.write("a.txt", "hello")
        self.replace("hello", "hi")
        leftovers = [p.name for p in self.root.iterdir() if "tmp" in p.name]
        self.assertEqual(leftovers, [])


class SearchGlobTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        self.addCleanup(self._tmp.cleanup)
        (self.root / "a.ts").write_text("needle")
        (self.root / "b.js").write_text("needle")
        (self.root / "pkg").mkdir()
        (self.root / "pkg" / "c.ts").write_text("needle")

    def search(self, **kwargs):
        return search_payload(
            default_workspace_scope(self.root, True), "needle", **kwargs
        )

    def paths(self, payload) -> set[str]:
        return {f["path"] for f in payload["files"]}

    def test_finds_every_file_without_a_filter(self):
        self.assertEqual(self.paths(self.search()), {"a.ts", "b.js", "pkg/c.ts"})

    def test_include_narrows_the_results(self):
        self.assertEqual(self.paths(self.search(include="*.ts")), {"a.ts", "pkg/c.ts"})

    def test_exclude_removes_results(self):
        self.assertEqual(self.paths(self.search(exclude="*.ts")), {"b.js"})

    def test_a_folder_include_is_anchored_to_the_project(self):
        # A glob with a separator anchors to the start of the path it is matched
        # against, so this only works while that path stays relative to the root.
        self.assertEqual(self.paths(self.search(include="pkg/**")), {"pkg/c.ts"})

    def test_a_folder_exclude_is_anchored_to_the_project(self):
        self.assertEqual(self.paths(self.search(exclude="pkg/**")), {"a.ts", "b.js"})

    def test_results_are_reported_relative_to_the_project(self):
        for path in self.paths(self.search()):
            self.assertFalse(path.startswith(("/", "./")), path)


class WslSearchPanelTest(unittest.TestCase):
    """The Search panel on a \\\\wsl.localhost project, seen from Windows.

    The Windows ripgrep read every file over the 9P bridge (minutes on a large
    project); the search now runs inside the distribution.
    """

    unc = Path("\\\\wsl.localhost\\Ubuntu\\home\\me\\shop")
    hit = (
        '{"type":"match","data":{"path":{"text":"./src/a.ts"},"lines":{"text":"needle\\n"},'
        '"line_number":3,"submatches":[{"start":0}]}}\n'
    )

    def setUp(self) -> None:
        from unittest import mock

        from navin.utils import wsl

        for name, value in (("wsl_executable", "wsl.exe"), ("resolve_distro", "Ubuntu")):
            patcher = mock.patch.object(wsl, name, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _search(self, *results):
        import subprocess
        from unittest import mock

        from navin.webui import project_search

        done = [subprocess.CompletedProcess([], code, stdout=out, stderr="") for code, out in results]
        with mock.patch.object(project_search, "_run_rg", side_effect=done) as run, \
                mock.patch.object(project_search.shutil, "which", return_value="C:\\rg.exe"):
            rows = project_search._rg_search(
                self.unc, "needle", regex=False, case_sensitive=False, platform="win32",
            )
        return rows, [call.args[0] for call in run.call_args_list], run.call_args_list

    def test_runs_the_distribution_ripgrep_from_the_project_root(self):
        rows, argvs, calls = self._search((0, self.hit))
        self.assertEqual(len(argvs), 1)
        self.assertEqual(argvs[0][:6], ["wsl.exe", "-d", "Ubuntu", "--cd", "/home/me/shop", "--exec"])
        self.assertEqual(argvs[0][-3:], ["--", "needle", "."])
        self.assertIsNone(calls[0].kwargs["cwd"])  # wsl.exe cannot start in a UNC folder
        self.assertEqual(rows, [{"path": "src/a.ts", "line": 3, "col": 1, "text": "needle"}])

    def test_no_ripgrep_in_the_distribution_falls_back_to_the_windows_one(self):
        rows, argvs, calls = self._search((127, ""), (0, self.hit))
        self.assertEqual(argvs[1][0], "C:\\rg.exe")
        self.assertEqual(calls[1].kwargs["cwd"], str(self.unc))
        self.assertEqual([row["path"] for row in rows], ["src/a.ts"])

    def test_a_local_project_never_goes_through_wsl(self):
        from navin.webui import project_search

        self.assertIsNone(project_search._wsl_rg_prefix(Path("/home/me/shop"), platform="win32"))
        self.assertIsNone(project_search._wsl_rg_prefix(self.unc, platform="linux"))

    def test_reading_stops_once_the_kept_rows_are_in(self):
        # --max-count is per file: "import" over a large repo printed 2 GB.
        import sys

        from navin.webui import project_search

        line = '{"type":"match","data":{}}'
        script = f"import sys\nfor _ in range(10**7): sys.stdout.write({line!r} + '\\n')"
        out = project_search._run_rg([sys.executable, "-c", script], cwd=None)
        self.assertEqual(out.returncode, 0)
        self.assertEqual(out.stdout.count("\n"), project_search._MAX_RESULTS)


if __name__ == "__main__":
    unittest.main()
