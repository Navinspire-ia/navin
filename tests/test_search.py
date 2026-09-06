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
                without_rg = self._grep(**kwargs)
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
                native = self._grep(output_mode=mode)
                with (
                    mock.patch.object(search_mod, "_native_module", return_value=None),
                    mock.patch.object(search_mod, "_rg_binary", return_value=None),
                ):
                    pure = self._grep(output_mode=mode)
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
                pure = self._grep(pattern=self._PATTERN, output_mode="content", multiline=True)
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


if __name__ == "__main__":
    unittest.main()
