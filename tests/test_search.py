# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for the agent search tools (navin.agent.tools.search).

Two things matter here and both are easy to regress: the ripgrep and Python
backends must agree on which files match, and content results must come back
ranked so a declaration outranks a passing mention.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import textwrap
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from navin.agent.tools import search as search_mod
from navin.agent.tools.search import FindFilesTool, GrepTool

_HAS_RG = search_mod._rg_binary() is not None
_HAS_GIT = shutil.which("git") is not None
_HAS_NATIVE = search_mod._native_module() is not None


def _run(coro):
    return asyncio.run(coro)


def _write(root: Path, rel: str, body: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(body).lstrip("\n"), encoding="utf-8")
    return path


class GlobMatchingTest(unittest.TestCase):
    """`**` must span directories; before Python 3.13 PurePosixPath.match
    treated it as a single `*`, so the pattern the tool schemas themselves
    document never matched nested files."""

    def test_glob_semantics_match_the_documented_patterns(self) -> None:
        cases = (
            ("tests/**/test_*.py", "tests/test_a.py", True),
            ("tests/**/test_*.py", "tests/a/b/test_x.py", True),
            ("tests/**/test_*.py", "tests/a/b/x_helper.py", False),
            ("src/*.py", "src/x.py", True),
            ("src/*.py", "src/sub/x.py", False),
            ("**/*.py", "a.py", True),
            ("**/*.py", "a/b/c.py", True),
            ("src/**", "src/a/b.txt", True),
            ("src/util.p?", "src/util.py", True),
            ("src/util.p?", "src/util/py", False),
            ("*.py", "x.py", True),
            ("*.py", "x.md", False),
            ("**/*.{json,rs,toml}", "desktop/src-tauri/tauri.conf.json", True),
            ("**/*.{json,rs,toml}", "desktop/src-tauri/src/main.rs", True),
            ("**/*.{json,rs,toml}", "desktop/src-tauri/Cargo.toml", True),
            ("**/*.{json,rs,toml}", "desktop/README.md", False),
            ("*.{ts,tsx}", "app.tsx", True),
            ("*.{ts,tsx}", "app.css", False),
            ("src/**/*.{py,pyi}", "src/a/b/mod.pyi", True),
            ("src/**/*.{py,pyi}", "src/a/b/mod.rs", False),
            ("**/{a,b}/*.py", "x/b/c.py", True),
            ("**/{a,b}/*.py", "x/c/c.py", False),
            ("*.{js,{ts,tsx}}", "app.tsx", True),
            ("*.{py,}", "x.py", True),
            ("*.{py,}", "x.", True),
            ("x{.py", "x{.py", True),
            ("*.{p[yi],rs}", "mod.pi", True),
            ("*.{p[yi],rs}", "mod.pz", False),
        )
        for pattern, rel_path, expected in cases:
            with self.subTest(pattern=pattern, rel_path=rel_path):
                name = rel_path.rsplit("/", 1)[-1]
                self.assertIs(
                    search_mod._match_glob(rel_path, name, pattern), expected
                )

    def test_find_files_matches_recursive_globs_end_to_end(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, "tests/test_top.py", "x\n")
            _write(root, "tests/a/b/test_deep.py", "x\n")
            _write(root, "tests/a/b/helper.py", "x\n")
            out = _run(
                FindFilesTool(workspace=root).execute(glob="tests/**/test_*.py")
            )
            self.assertIn("tests/test_top.py", out)
            self.assertIn("tests/a/b/test_deep.py", out)
            self.assertNotIn("helper.py", out)

    def test_grep_brace_glob_matches_the_extensions_it_names(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, "src/a.json", '{"needle": 1}\n')
            _write(root, "src/b.rs", "let needle = 1;\n")
            _write(root, "src/c.toml", "needle = 1\n")
            _write(root, "src/d.py", "needle = 1\n")
            out = _run(
                GrepTool(workspace=root).execute(
                    pattern="needle", glob="**/*.{json,rs,toml}"
                )
            )
            self.assertIn("src/a.json", out)
            self.assertIn("src/b.rs", out)
            self.assertIn("src/c.toml", out)
            self.assertNotIn("src/d.py", out)

    def test_grep_include_filter_matches_recursive_globs(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, "tests/a/b/test_deep.py", "needle\n")
            _write(root, "src/other.py", "needle\n")
            out = _run(
                GrepTool(workspace=root).execute(
                    pattern="needle", glob="tests/**/test_*.py"
                )
            )
            self.assertIn("tests/a/b/test_deep.py", out)
            self.assertNotIn("src/other.py", out)


class DefinitionHeuristicTest(unittest.TestCase):
    def test_declarations_are_recognized(self) -> None:
        for line in (
            "def handle_rate_limit(request):",
            "  async def check(self):",
            "class RateLimiter:",
            "export function throttle() {",
            "const RATE_LIMIT = 100;",
            "type Limiter = {",
            "func RateLimit(w http.ResponseWriter) {",
            "pub fn rate_limit() {",
            "RATE_LIMIT = 100",
            "CREATE TABLE rate_limits (",
            "interface Limiter {",
            "impl RateLimit for Server {",
        ):
            with self.subTest(line=line):
                self.assertTrue(search_mod._is_definition_line(line))

    def test_uses_and_prose_are_not_declarations(self) -> None:
        for line in (
            "    self.rate_limit_check()",
            "# see the rate limit docs",
            "    return rate_limit",
            "        raise RateLimitError()",
            "  if rate_limit > 0:",
            "the rate limit is enforced upstream",
            "@app.route('/limits')",
        ):
            with self.subTest(line=line):
                self.assertFalse(search_mod._is_definition_line(line))


class RelevanceTest(unittest.TestCase):
    def _hit(self, rel_path: str, matches: list[tuple[int, str]]) -> search_mod._FileHit:
        return search_mod._FileHit(
            path=Path("/tmp") / rel_path,
            display_path=rel_path,
            rel_path=rel_path,
            mtime=0.0,
            matches=matches,
        )

    def test_declaration_outranks_a_use(self) -> None:
        declaration = self._hit("app/limits.py", [(3, "def rate_limit(req):")])
        usage = self._hit("app/views.py", [(9, "    rate_limit(req)")])
        self.assertGreater(declaration.relevance(), usage.relevance())

    def test_source_outranks_its_test(self) -> None:
        source = self._hit("app/limits.py", [(3, "def rate_limit(req):")])
        test = self._hit("tests/test_limits.py", [(3, "def rate_limit(req):")])
        self.assertGreater(source.relevance(), test.relevance())

    def test_code_outranks_documentation(self) -> None:
        code = self._hit("a.py", [(1, "    rate_limit()")])
        docs = self._hit("a.md", [(1, "    rate_limit()")])
        self.assertGreater(code.relevance(), docs.relevance())

    def test_generated_output_sinks_below_source(self) -> None:
        source = self._hit("src/a.ts", [(1, "    rate_limit()")])
        generated = self._hit("dist/a.min.js", [(1, "    rate_limit()")])
        self.assertGreater(source.relevance(), generated.relevance())

    def test_denser_file_outranks_a_single_mention(self) -> None:
        dense = self._hit("a.py", [(1, "  x()"), (2, "  x()"), (3, "  x()")])
        sparse = self._hit("b.py", [(1, "  x()")])
        self.assertGreater(dense.relevance(), sparse.relevance())

    def test_shallow_path_outranks_a_deep_one(self) -> None:
        shallow = self._hit("a.py", [(1, "  x()")])
        deep = self._hit("a/b/c/d/e.py", [(1, "  x()")])
        self.assertGreater(shallow.relevance(), deep.relevance())


class _GrepFixture(unittest.TestCase):
    """A workspace where the interesting hit is *not* alphabetically first."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

        _write(self.root, "app/limiter.py", """
            def rate_limit(request):
                return True
        """)
        _write(self.root, "app/views.py", """
            from app.limiter import rate_limit

            def index(request):
                return rate_limit(request)
        """)
        _write(self.root, "docs/notes.md", """
            The rate_limit helper is documented here.
        """)
        _write(self.root, "tests/test_limiter.py", """
            def test_rate_limit():
                assert rate_limit(None)
        """)
        _write(self.root, "node_modules/pkg/index.js", """
            exports.rate_limit = function () {};
        """)
        self.tool = GrepTool(workspace=self.root)

    def _grep(self, **kwargs) -> str:
        kwargs.setdefault("pattern", "rate_limit")
        return _run(self.tool.execute(**kwargs))

    def _grep_fresh(self, **kwargs) -> str:
        """Search as a new session would, without the repeated-search note."""
        self.tool._file_states.clear()
        return self._grep(**kwargs)


class GrepRepeatTest(_GrepFixture):
    def test_the_same_search_with_the_same_result_says_so(self) -> None:
        first = self._grep(output_mode="content")
        self.assertNotIn("Same search as an earlier call", first)
        again = self._grep(output_mode="content")
        self.assertIn("Same search as an earlier call in this session, same result", again)
        self.assertIn("app/limiter.py", again)

    def test_a_changed_result_or_another_search_gets_no_note(self) -> None:
        self._grep()
        _write(self.root, "app/extra.py", "rate_limit = 3\n")
        self.assertNotIn("Same search", self._grep())
        self.assertNotIn("Same search", self._grep(glob="*.py"))
        self.assertNotIn("Same search", self._grep(pattern="no_such_symbol_xyz"))
        # A repeated empty search is the loop this note is for.
        self.assertIn("Same search", self._grep(pattern="no_such_symbol_xyz"))


class GrepRankingTest(_GrepFixture):
    def test_definition_file_is_listed_first(self) -> None:
        out = self._grep()
        listed = [line for line in out.splitlines() if line.strip()]
        self.assertEqual(listed[0], "app/limiter.py")

    def test_tests_and_docs_rank_below_source(self) -> None:
        listed = self._grep().splitlines()
        self.assertLess(listed.index("app/limiter.py"), listed.index("docs/notes.md"))
        self.assertLess(
            listed.index("app/views.py"), listed.index("tests/test_limiter.py")
        )

    def test_content_mode_leads_with_the_declaration(self) -> None:
        out = self._grep(output_mode="content")
        first = out.splitlines()[0]
        self.assertTrue(first.startswith("app/limiter.py:"), first)

    def test_content_blocks_stay_grouped_by_file(self) -> None:
        """Every block for one file must be contiguous, never interleaved."""
        out = self._grep(output_mode="content")
        files = [
            line.rsplit(":", 1)[0]
            for line in out.splitlines()
            if line and not line.startswith((">", " ", "("))
        ]
        self.assertGreater(len(files), len(set(files)), "need a repeated file")
        runs = [name for idx, name in enumerate(files) if idx == 0 or files[idx - 1] != name]
        self.assertEqual(len(runs), len(set(runs)), files)

    def test_sort_path_is_alphabetical(self) -> None:
        listed = [
            line for line in self._grep(sort="path").splitlines() if "/" in line
        ]
        self.assertEqual(listed, sorted(listed))

    def test_invalid_sort_is_rejected(self) -> None:
        self.assertIn("sort must be", self._grep(sort="sideways"))


class GrepBehaviourTest(_GrepFixture):
    def test_missing_filename_finds_symbols_in_its_project_directory(self):
        _write(self.root, "navin/config/paths.py", "def get_runtime_subdir(name): pass\n")
        tool = GrepTool(workspace=self.root, allowed_dir=self.root)
        result = _run(tool.execute(path="navin/runtime_paths.py", pattern="def get_runtime_subdir"))
        self.assertFalse(getattr(result, "is_error", False), result)
        self.assertIn("navin/runtime_paths.py does not exist", result)
        self.assertIn("Searched navin", result)
        self.assertIn("navin/config/paths.py", result)

    def test_missing_filename_without_matches_remains_an_error(self):
        tool = GrepTool(workspace=self.root, allowed_dir=self.root)
        result = _run(tool.execute(path="unknown.py", pattern="no_such_symbol"))
        self.assertTrue(result.is_error)
        self.assertIn("not found: unknown.py", result)

    def test_missing_directory_does_not_widen_search(self):
        _write(self.root, "present.py", "wanted_symbol\n")
        tool = GrepTool(workspace=self.root, allowed_dir=self.root)
        result = _run(tool.execute(path="absent", pattern="wanted_symbol"))
        self.assertTrue(result.is_error)

    def test_missing_file_fallback_respects_glob_and_workspace(self):
        _write(self.root, "present.txt", "wanted_symbol\n")
        tool = GrepTool(workspace=self.root, allowed_dir=self.root)
        result = _run(tool.execute(path="unknown.py", pattern="wanted_symbol", glob="*.py"))
        self.assertTrue(result.is_error)
        result = _run(tool.execute(path="../unknown.py", pattern="wanted_symbol"))
        self.assertTrue(result.is_error)

    def test_dependency_directories_are_skipped(self) -> None:
        self.assertNotIn("node_modules", self._grep())

    def test_content_mode_without_context_reports_the_matching_line(self) -> None:
        out = self._grep(output_mode="content", glob="app/limiter.py")
        self.assertIn("> 1| def rate_limit(request):", out)

    def test_content_mode_with_context_includes_neighbours(self) -> None:
        out = self._grep(
            output_mode="content", glob="app/views.py", context_after=1
        )
        self.assertIn("from app.limiter import rate_limit", out)
        self.assertRegex(out, r"\n  2\| ")

    def test_count_mode_totals_every_match(self) -> None:
        out = self._grep(output_mode="count")
        self.assertIn("app/views.py: 2", out)
        self.assertIn("(total matches: 6 in 4 files)", out)

    def test_type_filter_limits_the_file_set(self) -> None:
        out = self._grep(type="md")
        self.assertEqual(out.strip(), "docs/notes.md")

    def test_no_match_is_reported_plainly(self) -> None:
        self.assertIn("No matches found", self._grep(pattern="zzz_absent_zzz"))

    def test_no_match_names_the_next_step(self) -> None:
        out = self._grep(pattern="zzz_absent_zzz", glob="*.py")
        self.assertIn("next step:", out)
        self.assertIn("case_insensitive=true", out)
        self.assertIn("drop glob/type", out)

    def test_no_match_in_one_file_says_case_insensitive_would_hit(self) -> None:
        out = self._grep(pattern="RATE_LIMIT", path="app/limiter.py", output_mode="content")
        self.assertIn("No matches found", out)
        self.assertIn("it DOES match with case_insensitive=true", out)
        self.assertIn("widen path to the parent directory", out)

    def test_no_match_flags_regex_metacharacters(self) -> None:
        out = self._grep(pattern="rate_limit(None, x)")
        self.assertIn("fixed_strings=true", out)

    def test_head_limit_paginates_and_says_so(self) -> None:
        out = self._grep(head_limit=1)
        self.assertEqual(out.splitlines()[0], "app/limiter.py")
        self.assertIn("pagination: limit=1", out)

    def test_fixed_strings_disables_regex_metacharacters(self) -> None:
        _write(self.root, "app/literal.py", "value = a.b\n")
        self.assertIn("app/literal.py", self._grep(pattern="a.b", fixed_strings=True))
        out = self._grep(pattern="a[b", fixed_strings=True)
        self.assertNotIn("invalid regex", out)

    def test_invalid_regex_is_reported(self) -> None:
        self.assertIn("invalid regex", self._grep(pattern="a[b"))

    def test_case_insensitive_matches_other_casing(self) -> None:
        _write(self.root, "app/upper.py", "RATE_LIMIT = 1\n")
        self.assertIn(
            "app/upper.py", self._grep(pattern="rate_LIMIT", case_insensitive=True)
        )


class BackendParityTest(_GrepFixture):
    """The two backends must not disagree on results the caller can see."""

    def _both_backends(self, **kwargs) -> tuple[str, str]:
        with mock.patch.object(search_mod, "_native_module", return_value=None):
            with_rg = self._grep(**kwargs)
            with mock.patch.object(search_mod, "_rg_binary", return_value=None):
                without_rg = self._grep_fresh(**kwargs)
        return with_rg, without_rg

    @unittest.skipUnless(_HAS_RG, "ripgrep not installed")
    def test_file_lists_are_identical(self) -> None:
        with_rg, without_rg = self._both_backends()
        self.assertEqual(with_rg, without_rg)

    @unittest.skipUnless(_HAS_RG, "ripgrep not installed")
    def test_content_output_is_identical(self) -> None:
        with_rg, without_rg = self._both_backends(output_mode="content")
        self.assertEqual(with_rg, without_rg)

    @unittest.skipUnless(_HAS_RG, "ripgrep not installed")
    def test_counts_are_identical(self) -> None:
        with_rg, without_rg = self._both_backends(output_mode="count")
        self.assertEqual(with_rg, without_rg)

    def test_fallback_alone_still_finds_the_definition(self) -> None:
        with mock.patch.object(search_mod, "_rg_binary", return_value=None):
            self.assertIn("app/limiter.py", self._grep())

    @unittest.skipUnless(_HAS_NATIVE, "navin_core not installed")
    def test_native_agrees_with_the_python_fallback(self) -> None:
        for mode in ("files_with_matches", "content", "count"):
            with self.subTest(mode=mode):
                native = self._grep_fresh(output_mode=mode)
                with (
                    mock.patch.object(search_mod, "_native_module", return_value=None),
                    mock.patch.object(search_mod, "_rg_binary", return_value=None),
                ):
                    pure = self._grep_fresh(output_mode=mode)
                self.assertEqual(native, pure)

    @unittest.skipUnless(_HAS_NATIVE, "navin_core not installed")
    def test_pattern_the_native_engine_cannot_compile_falls_back(self) -> None:
        # Lookahead is valid in Python's re but rejected by the Rust engine.
        self.assertIsNone(
            search_mod._native_scan(
                self.root,
                r"rate_limit(?=\()",
                fixed_strings=False,
                case_insensitive=False,
                include_ignored=False,
                max_file_bytes=2_000_000,
            )
        )
        out = self._grep(pattern=r"rate_limit(?=\()")
        self.assertIn("app/limiter.py", out)
        self.assertNotIn("Error", out)

    @unittest.skipUnless(_HAS_RG, "ripgrep not installed")
    def test_pattern_ripgrep_cannot_compile_falls_back(self) -> None:
        # Lookahead is valid in Python's re but rejected by ripgrep's engine.
        # The tool must answer from the fallback, not surface ripgrep's error.
        self.assertIsNone(
            search_mod._rg_scan(
                self.root,
                r"rate_limit(?=\()",
                fixed_strings=False,
                case_insensitive=False,
                include_ignored=False,
                max_file_bytes=2_000_000,
            )
        )
        out = self._grep(pattern=r"rate_limit(?=\()")
        self.assertIn("app/limiter.py", out)
        self.assertNotIn("Error", out)


class MultilineTest(_GrepFixture):
    """A pattern may span lines; the match is reported where it starts."""

    _PATTERN = r"def index\(request\):\n\s+return rate_limit"

    def test_a_spanning_pattern_finds_nothing_line_by_line(self) -> None:
        self.assertIn("No matches", self._grep(pattern=self._PATTERN, output_mode="content"))

    def test_multiline_reports_the_first_line_of_the_match(self) -> None:
        out = self._grep(pattern=self._PATTERN, output_mode="content", multiline=True)
        self.assertIn("app/views.py:3", out)
        self.assertIn("> 3| def index(request):", out)
        # One block per match: the second line of the match is not a hit of its own.
        self.assertEqual(out.count("app/views.py:"), 1)

    def test_dot_crosses_newlines_in_multiline_mode(self) -> None:
        out = self._grep(pattern=r"from app.*?index", output_mode="count", multiline=True)
        self.assertIn("app/views.py: 1", out)

    @unittest.skipUnless(_HAS_RG, "ripgrep not installed")
    def test_the_python_fallback_agrees_with_ripgrep(self) -> None:
        with mock.patch.object(search_mod, "_native_module", return_value=None):
            with_rg = self._grep(pattern=self._PATTERN, output_mode="content", multiline=True)
            with mock.patch.object(search_mod, "_rg_binary", return_value=None):
                pure = self._grep_fresh(pattern=self._PATTERN, output_mode="content", multiline=True)
        self.assertEqual(with_rg, pure)

    def test_context_after_shows_the_rest_of_the_match(self) -> None:
        out = self._grep(
            pattern=self._PATTERN, output_mode="content", multiline=True, context_after=1,
        )
        self.assertIn("> 3| def index(request):", out)
        self.assertIn("  4|     return rate_limit(request)", out)


@unittest.skipUnless(_HAS_RG and _HAS_GIT, "needs ripgrep and git")
class GitignoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        subprocess.run(
            ["git", "init", "-q"], cwd=self.root, check=True, capture_output=True,
        )
        _write(self.root, ".gitignore", "secret_build/\n*.generated.ts\n")
        _write(self.root, "src/app.ts", "export const token = 1;\n")
        _write(self.root, "secret_build/app.ts", "export const token = 2;\n")
        _write(self.root, "src/schema.generated.ts", "export const token = 3;\n")
        self.tool = GrepTool(workspace=self.root)

    def test_gitignored_paths_are_excluded_by_default(self) -> None:
        out = _run(self.tool.execute(pattern="token"))
        self.assertIn("src/app.ts", out)
        self.assertNotIn("secret_build", out)
        self.assertNotIn("generated", out)

    def test_include_ignored_brings_them_back(self) -> None:
        out = _run(self.tool.execute(pattern="token", include_ignored=True))
        self.assertIn("secret_build/app.ts", out)
        self.assertIn("src/schema.generated.ts", out)


@unittest.skipUnless(_HAS_GIT, "git not installed")
class FallbackGitignoreTest(unittest.TestCase):
    """The Python fallback must honour .gitignore like the ripgrep backend.

    A pattern ripgrep cannot compile (lookahead) silently falls back; the
    fallback returning hits from ignored files would widen the search the
    moment the pattern got fancier, which is exactly when nobody is looking.
    """

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        subprocess.run(
            ["git", "init", "-q"], cwd=self.root, check=True, capture_output=True,
        )
        _write(self.root, ".gitignore", "secret_build/\n")
        _write(self.root, "src/app.ts", "export const token = 1;\n")
        _write(self.root, "secret_build/app.ts", "export const token = 2;\n")
        self.tool = GrepTool(workspace=self.root)

    def test_a_lookahead_pattern_does_not_search_ignored_files(self) -> None:
        # Lookahead is rejected by ripgrep's engine, so this answer comes from
        # the fallback whether or not rg is installed.
        out = _run(self.tool.execute(pattern=r"token(?= =)"))
        self.assertIn("src/app.ts", out)
        self.assertNotIn("secret_build", out)

    def test_include_ignored_still_reaches_them_in_the_fallback(self) -> None:
        with mock.patch.object(search_mod, "_rg_binary", return_value=None):
            out = _run(self.tool.execute(pattern="token", include_ignored=True))
        self.assertIn("secret_build/app.ts", out)


class FallbackBudgetTest(unittest.TestCase):
    """Without a fast backend the Python scan answers in bounded time.

    On the Windows desktop (no native module, no ripgrep, a WSL project read
    through the 9P bridge) one grep call ran for ten minutes. It now stops at
    the budget and says the answer is partial instead of "No matches".
    """

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        _write(self.root, "src/app.ts", "export const token = 1;\n")
        self.tool = GrepTool(workspace=self.root)
        for name in ("_native_module", "_rg_binary"):
            patcher = mock.patch.object(search_mod, name, return_value=None)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_an_exhausted_budget_reports_a_partial_search(self) -> None:
        with mock.patch.object(search_mod, "_FALLBACK_BUDGET_S", 0.0):
            out = _run(self.tool.execute(patterns=["token", "const"]))
        self.assertEqual(out.count("(partial: the search stopped"), 2)
        self.assertNotIn("src/app.ts", out)

    def test_within_budget_the_answer_is_complete_and_unflagged(self) -> None:
        out = _run(self.tool.execute(pattern="token"))
        self.assertIn("src/app.ts", out)
        self.assertNotIn("partial", out)


class WslRipgrepTest(unittest.TestCase):
    """A WSL project seen from Windows is searched by the distribution's rg."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.linux_root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        _write(self.linux_root, "backend/ocr.py", "DUPLICATE_INVOICE = 1\n")
        self.unc = Path("\\\\wsl.localhost\\Ubuntu" + str(self.linux_root).replace("/", "\\"))
        from navin.utils import wsl

        for name, value in (("wsl_executable", "wsl.exe"), ("resolve_distro", "Ubuntu")):
            patcher = mock.patch.object(wsl, name, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _scan(self, **kwargs):
        return search_mod._wsl_rg_scan(
            self.unc, "DUPLICATE_INVOICE", fixed_strings=False, case_insensitive=False,
            include_ignored=False, max_file_bytes=2_000_000, platform="win32", **kwargs,
        )

    def test_argv_runs_rg_inside_the_distribution_on_its_linux_path(self) -> None:
        done = subprocess.CompletedProcess([], 1, stdout="", stderr="")
        with mock.patch.object(search_mod.subprocess, "run", return_value=done) as run:
            self.assertEqual(self._scan(), {})
        argv = run.call_args.args[0]
        self.assertEqual(argv[:4], ["wsl.exe", "-d", "Ubuntu", "--exec"])
        self.assertEqual(argv[4:6], ["sh", "-c"])
        self.assertEqual(argv[-3:], ["--", "DUPLICATE_INVOICE", str(self.linux_root)])

    def test_missing_rg_in_the_distribution_falls_through(self) -> None:
        done = subprocess.CompletedProcess([], 127, stdout="", stderr="")
        with mock.patch.object(search_mod.subprocess, "run", return_value=done):
            self.assertIsNone(self._scan())

    def test_not_windows_or_not_wsl_is_left_to_the_other_backends(self) -> None:
        self.assertIsNone(search_mod._wsl_rg_scan(
            self.unc, "x", fixed_strings=False, case_insensitive=False,
            include_ignored=False, max_file_bytes=1, platform="linux",
        ))
        self.assertIsNone(search_mod._wsl_rg_scan(
            Path("C:\\work\\app"), "x", fixed_strings=False, case_insensitive=False,
            include_ignored=False, max_file_bytes=1, platform="win32",
        ))

    @unittest.skipUnless(_HAS_RG, "ripgrep not installed")
    def test_hits_come_back_under_the_windows_spelling(self) -> None:
        # The real script and ripgrep, with wsl.exe itself left out.
        with mock.patch("navin.utils.wsl.command_prefix", return_value=[]):
            hits = self._scan()
        expected = str(self.unc / "backend" / "ocr.py")
        self.assertEqual(hits, {expected: [(1, "DUPLICATE_INVOICE = 1")]})

    def test_it_runs_before_the_native_module(self) -> None:
        tool = GrepTool(workspace=self.linux_root)
        found = {str(self.linux_root / "backend/ocr.py"): [(1, "DUPLICATE_INVOICE = 1")]}
        with (
            mock.patch.object(search_mod, "_wsl_rg_scan", return_value=found),
            mock.patch.object(search_mod, "_native_scan") as native,
        ):
            out = _run(tool.execute(pattern="DUPLICATE_INVOICE"))
        native.assert_not_called()
        self.assertIn("backend/ocr.py", out)

    def test_ranking_mtimes_come_from_one_stat_inside_the_distribution(self) -> None:
        # One 9P stat per matched file cost ten seconds on a common word.
        hit = str(self.unc / "backend" / "ocr.py")
        linux = f"{self.linux_root}/backend/ocr.py"
        done = subprocess.CompletedProcess([], 0, stdout=f"1700000000 {linux}\0".encode(), stderr=b"")
        with mock.patch.object(search_mod.subprocess, "run", return_value=done) as run:
            mtimes = search_mod._wsl_mtimes(self.unc, [hit], platform="win32")
        self.assertEqual(mtimes, {hit: 1700000000.0})
        argv = run.call_args.args[0]
        self.assertEqual(argv[:4], ["wsl.exe", "-d", "Ubuntu", "--exec"])
        self.assertIn("stat", argv)
        self.assertEqual(run.call_args.kwargs["input"], linux.encode() + b"\0")
        self.assertIsNone(search_mod._wsl_mtimes(self.unc, [hit], platform="linux"))

    def test_find_files_lists_inside_the_distribution(self) -> None:
        done = subprocess.CompletedProcess([], 0, stdout=f"{self.linux_root}/backend/ocr.py\0", stderr="")
        with mock.patch.object(search_mod.subprocess, "run", return_value=done) as run:
            rows = search_mod._wsl_rg_files(self.unc, include_ignored=False, platform="win32")
        argv = run.call_args.args[0]
        self.assertEqual(argv[:4], ["wsl.exe", "-d", "Ubuntu", "--exec"])
        self.assertIn("--files", argv)
        self.assertEqual(argv[-2:], ["--", str(self.linux_root)])
        self.assertEqual(rows, [str(self.unc / "backend" / "ocr.py")])
        with mock.patch.object(search_mod.subprocess, "run", return_value=subprocess.CompletedProcess([], 127, "", "")):
            self.assertIsNone(search_mod._wsl_rg_files(self.unc, include_ignored=False, platform="win32"))
        self.assertIsNone(search_mod._wsl_rg_files(self.unc, include_ignored=False, platform="linux"))

    def test_find_files_uses_the_distribution_listing_first(self) -> None:
        tool = FindFilesTool(workspace=self.linux_root)
        listed = [str(self.linux_root / "backend/ocr.py")]
        with (
            mock.patch.object(search_mod, "_wsl_rg_files", return_value=listed),
            mock.patch.object(search_mod, "_rg_files") as host_rg,
            mock.patch.object(search_mod, "_native_walk") as native,
        ):
            out = _run(tool.execute(query="ocr"))
        host_rg.assert_not_called()
        native.assert_not_called()
        self.assertIn("backend/ocr.py", out)


class FindFilesTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        _write(self.root, "src/app.ts", "x\n")
        _write(self.root, "src/util.py", "x\n")
        _write(self.root, "target/gen.ts", "x\n")
        _write(self.root, ".next/page.ts", "x\n")
        self.tool = FindFilesTool(workspace=self.root)

    def test_build_directories_are_skipped(self) -> None:
        out = _run(self.tool.execute())
        self.assertIn("src/app.ts", out)
        self.assertNotIn("target/", out)
        self.assertNotIn(".next/", out)

    def test_type_filter_selects_one_language(self) -> None:
        out = _run(self.tool.execute(type="py"))
        self.assertEqual(out.strip(), "src/util.py")

    def test_directories_come_back_only_when_asked(self) -> None:
        self.assertNotIn("src/", _run(self.tool.execute(query="src")).split("\n"))
        with_dirs = _run(self.tool.execute(query="src", include_dirs=True))
        self.assertIn("src/", with_dirs.split("\n"))

    def test_every_backend_lists_the_same_tree(self) -> None:
        expected = _run(self.tool.execute(glob="**/*"))
        with mock.patch.object(search_mod, "_rg_binary", return_value=None):
            native = _run(self.tool.execute(glob="**/*"))
            with mock.patch.object(search_mod, "_native_module", return_value=None):
                pure = _run(self.tool.execute(glob="**/*"))
        self.assertEqual(native, expected)
        self.assertEqual(pure, expected)

    def test_the_listing_leaves_the_event_loop_free(self) -> None:
        """A slow tree must not freeze the loop every other session shares."""
        ticks = 0

        async def heartbeat() -> None:
            nonlocal ticks
            while True:
                await asyncio.sleep(0.005)
                ticks += 1

        def slow_listing(*_args: object, **_kwargs: object) -> list[tuple[str, bool]]:
            time.sleep(0.15)
            return [(str(self.root / "src" / "app.ts"), False)]

        async def scenario() -> str:
            task = asyncio.create_task(heartbeat())
            try:
                with mock.patch.object(search_mod, "_rg_files", return_value=None), \
                        mock.patch.object(search_mod, "_native_walk", side_effect=slow_listing):
                    return await self.tool.execute()
            finally:
                task.cancel()

        out = asyncio.run(scenario())
        self.assertIn("src/app.ts", out)
        self.assertGreater(ticks, 5)


@unittest.skipUnless(_HAS_GIT, "git not installed")
class FindFilesGitignoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        subprocess.run(
            ["git", "init", "-q"], cwd=self.root, check=True, capture_output=True,
        )
        _write(self.root, ".gitignore", "secret_build/\n*.generated.ts\n")
        _write(self.root, "src/app.ts", "x\n")
        _write(self.root, "secret_build/app.ts", "x\n")
        _write(self.root, "src/schema.generated.ts", "x\n")
        self.tool = FindFilesTool(workspace=self.root)

    def test_ignored_files_are_hidden_by_default_on_every_backend(self) -> None:
        out = _run(self.tool.execute(type="ts"))
        self.assertIn("src/app.ts", out)
        self.assertNotIn("secret_build", out)
        self.assertNotIn("generated", out)
        with mock.patch.object(search_mod, "_rg_binary", return_value=None):
            fallback = _run(self.tool.execute(type="ts"))
        self.assertEqual(fallback, out)

    def test_include_ignored_brings_them_back(self) -> None:
        out = _run(self.tool.execute(type="ts", include_ignored=True))
        self.assertIn("secret_build/app.ts", out)
        self.assertIn("src/schema.generated.ts", out)
        with mock.patch.object(search_mod, "_rg_binary", return_value=None):
            fallback = _run(self.tool.execute(type="ts", include_ignored=True))
        self.assertEqual(fallback, out)


class GrepOffsetPastEndTest(unittest.TestCase):
    def test_offset_past_the_last_match_is_not_reported_as_no_match(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, "a.py", "needle\nneedle\nneedle\n")
            tool = GrepTool(workspace=root)
            for mode in ("content", "files_with_matches", "count"):
                out = _run(tool.execute(pattern="needle", output_mode=mode, offset=10))
                self.assertIn("No entries at offset", out, mode)
                self.assertNotIn("No matches found", out, mode)
            none = _run(tool.execute(pattern="absent", output_mode="content"))
            self.assertIn("No matches found", none)


if __name__ == "__main__":
    unittest.main()


def test_broad_pattern_gets_a_narrowing_note_but_exact_and_count_do_not(tmp_path) -> None:
    import asyncio

    from navin.agent.tools.search import _BROAD_PATTERN_FILES, GrepTool

    for index in range(_BROAD_PATTERN_FILES + 5):
        (tmp_path / f"m{index}.py").write_text("import os\n", encoding="utf-8")
    (tmp_path / "target.py").write_text("def build_dashboard_route():\n    pass\n", encoding="utf-8")
    tool = GrepTool(workspace=tmp_path)

    broad = str(asyncio.run(tool.execute(pattern="import", path=".")))
    exact = str(asyncio.run(tool.execute(pattern="build_dashboard_route", path=".")))
    count = str(asyncio.run(tool.execute(pattern="import", path=".", output_mode="count")))

    assert f"broad pattern: {_BROAD_PATTERN_FILES + 5} files match" in broad
    assert "exact identifier" in broad
    assert "target.py" in exact and "broad pattern" not in exact
    # count is the scoping step itself: it reports totals, not a warning.
    assert "broad pattern" not in count and "total matches" in count
