"""Tests for the project code index (navin.index)."""

from __future__ import annotations

import os
import textwrap
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from navin.agent.tools.code_index import CodeIndexTool
from navin.index.service import CodeIndex
from navin.index.store import refs_cache_path
from navin.index.symbols import extract, language_for


class LanguageDetectionTest(unittest.TestCase):
    def test_known_extensions_map_to_languages(self) -> None:
        cases = {
            "a/b.py": "python",
            "a/b.pyi": "python",
            "src/App.tsx": "typescript",
            "src/app.ts": "typescript",
            "src/old.js": "javascript",
            "main.go": "go",
            "lib.rs": "rust",
            "Main.java": "java",
            "app.rb": "ruby",
            "schema.sql": "sql",
        }
        for path, expected in cases.items():
            with self.subTest(path=path):
                self.assertEqual(language_for(path), expected)

    def test_unknown_extension_is_empty(self) -> None:
        self.assertEqual(language_for("notes.xyz"), "")


class PythonExtractionTest(unittest.TestCase):
    SOURCE = textwrap.dedent(
        '''
        """Module purpose line.

        Longer description that must not appear in the role.
        """
        import os
        from pkg.sub import thing

        MAX_SIZE = 10
        _private = 1


        def top_level(a, b=2, *args, key=None, **kw):
            """Does a thing."""
            return a


        class Widget(Base):
            """A widget."""

            def method(self, x):
                from lazy.module import late
                return late(x)
        '''
    ).strip()

    def setUp(self) -> None:
        self.entry = extract("pkg/mod.py", self.SOURCE)

    def test_module_doc_is_first_line_only(self) -> None:
        self.assertEqual(self.entry.doc, "Module purpose line.")

    def test_symbols_have_kinds_and_qualnames(self) -> None:
        found = {s.qualname: s.kind for s in self.entry.symbols}
        self.assertEqual(found["top_level"], "function")
        self.assertEqual(found["Widget"], "class")
        self.assertEqual(found["Widget.method"], "method")
        self.assertEqual(found["MAX_SIZE"], "constant")
        self.assertEqual(found["_private"], "variable")

    def test_signature_records_parameters(self) -> None:
        symbol = next(s for s in self.entry.symbols if s.name == "top_level")
        self.assertEqual(symbol.signature, "top_level(a, b=…, *args, key=…, **kw)")

    def test_underscore_names_are_not_exported(self) -> None:
        private = next(s for s in self.entry.symbols if s.name == "_private")
        public = next(s for s in self.entry.symbols if s.name == "top_level")
        self.assertFalse(private.exported)
        self.assertTrue(public.exported)

    def test_lazy_imports_inside_functions_are_captured(self) -> None:
        # Function-level imports carry real dependency edges and must be indexed.
        self.assertIn("lazy.module", self.entry.imports)
        self.assertIn("pkg.sub", self.entry.imports)
        self.assertIn("os", self.entry.imports)

    def test_syntax_error_degrades_instead_of_failing(self) -> None:
        entry = extract("broken.py", "def ok():\n    pass\nclass Bad(\n")
        self.assertTrue(entry.parse_error)
        self.assertIn("ok", [s.name for s in entry.symbols])


class TypeScriptExtractionTest(unittest.TestCase):
    SOURCE = textwrap.dedent(
        """
        // Renders the dashboard shell.
        import { useState } from "react";
        import type { Row } from "@/lib/types";

        export const PAGE_SIZE = 25;

        export interface Props { rows: Row[] }

        export type Mode = "list" | "grid";

        export default function Dashboard({ rows }: Props) {
          return null;
        }

        const helper = (x: number) => x * 2;

        /* export function inBlockComment() {} */
        """
    ).strip()

    def setUp(self) -> None:
        self.entry = extract("src/Dashboard.tsx", self.SOURCE)

    def test_leading_comment_becomes_role(self) -> None:
        self.assertEqual(self.entry.doc, "Renders the dashboard shell.")

    def test_declaration_kinds(self) -> None:
        found = {s.name: s.kind for s in self.entry.symbols}
        self.assertEqual(found["Props"], "interface")
        self.assertEqual(found["Mode"], "type")
        self.assertEqual(found["Dashboard"], "function")
        self.assertEqual(found["PAGE_SIZE"], "constant")
        self.assertEqual(found["helper"], "function")

    def test_export_keyword_drives_visibility(self) -> None:
        found = {s.name: s.exported for s in self.entry.symbols}
        self.assertTrue(found["Dashboard"])
        self.assertFalse(found["helper"])

    def test_block_comments_are_skipped(self) -> None:
        self.assertNotIn("inBlockComment", [s.name for s in self.entry.symbols])

    def test_imports_are_collected(self) -> None:
        self.assertIn("react", self.entry.imports)
        self.assertIn("@/lib/types", self.entry.imports)


class IndexQueryTest(unittest.TestCase):
    """End-to-end queries over a small on-disk project."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "pkg").mkdir()
        (self.root / "pkg" / "__init__.py").write_text("", encoding="utf-8")
        (self.root / "pkg" / "core.py").write_text(
            textwrap.dedent(
                '''
                """Core helpers."""


                def compute(value):
                    return value * 2
                '''
            ).strip(),
            encoding="utf-8",
        )
        (self.root / "pkg" / "caller.py").write_text(
            textwrap.dedent(
                """
                from pkg.core import compute


                def run():
                    return compute(21)
                """
            ).strip(),
            encoding="utf-8",
        )
        (self.root / "unrelated.py").write_text(
            "compute = 'a string mentioning compute'\n", encoding="utf-8"
        )

        # Keep the cache inside the temp dir so tests never touch ~/.navin.
        self._patch = mock.patch(
            "navin.config.loader.get_config_path",
            return_value=self.root / ".cfg" / "config.json",
        )
        self._patch.start()
        self.index = CodeIndex(self.root)
        self.index.refresh(force=True)

    def tearDown(self) -> None:
        self._patch.stop()
        self._tmp.cleanup()

    def test_definition_is_exact(self) -> None:
        locations = self.index.definition("compute")
        paths = {loc.path for loc in locations}
        self.assertIn("pkg/core.py", paths)
        core = next(loc for loc in locations if loc.path == "pkg/core.py")
        self.assertEqual(core.kind, "function")

    def test_import_edges_resolve(self) -> None:
        self.assertIn("pkg/core.py", self.index.dependencies("pkg/caller.py"))
        self.assertIn("pkg/caller.py", self.index.dependents("pkg/core.py"))

    def test_references_come_from_parsed_code_not_text(self) -> None:
        refs, total = self.index.references("compute")
        self.assertGreater(total, 0)
        ordered = [loc.path for loc in refs]
        self.assertIn("pkg/caller.py", ordered)
        # unrelated.py only mentions "compute" inside a string literal. A text
        # search would report it; a parsed index must not.
        self.assertNotIn("unrelated.py", ordered)

    def test_references_name_the_enclosing_definition(self) -> None:
        refs, _ = self.index.references("compute")
        hit = next(loc for loc in refs if loc.path == "pkg/caller.py")
        self.assertEqual(hit.name, "run")
        self.assertIn("call", hit.confidence)
        self.assertEqual(hit.context, "return compute(21)")

    def test_outline_resolves_bare_filename(self) -> None:
        result = self.index.outline("core.py")
        self.assertIsNotNone(result)
        assert result is not None
        resolved, symbols = result
        self.assertEqual(resolved, "pkg/core.py")
        self.assertIn("compute", [s.name for s in symbols])

    def test_search_prefers_exact_then_prefix(self) -> None:
        names = [loc.name for loc in self.index.search("comp")]
        self.assertIn("compute", names)

    def test_incremental_refresh_reuses_cache(self) -> None:
        stats = self.index.refresh()
        self.assertEqual(stats.parsed, 0)
        self.assertGreater(stats.reused, 0)

    def test_edited_file_is_reparsed(self) -> None:
        target = self.root / "pkg" / "core.py"
        target.write_text(
            '"""Core helpers."""\n\n\ndef compute(value):\n    return value\n\n\n'
            "def added():\n    return 1\n",
            encoding="utf-8",
        )
        self.index.refresh()
        self.assertTrue(self.index.definition("added"))

    def test_overview_reports_counts(self) -> None:
        overview = self.index.overview()
        self.assertGreater(overview["files"], 0)
        self.assertGreater(overview["symbols"], 0)
        self.assertIn("python", overview["languages"])

    def test_overview_only_loads_references_when_asked(self) -> None:
        self.index.overview()
        self.assertNotIn("references", self.index.overview())
        with_refs = self.index.overview(include_refs=True)
        self.assertGreater(with_refs["references"], 0)
        self.assertGreater(with_refs["call_edges"], 0)


class ReferenceExtractionTest(unittest.TestCase):
    """Reference records must attribute each use to its enclosing definition."""

    SOURCE = textwrap.dedent(
        """
        from helpers import helper, Base, decorate


        CONSTANT = helper


        @decorate
        def outer(value):
            return helper(value)


        class Widget(Base):
            def render(self):
                return helper(self)
        """
    ).strip()

    def setUp(self) -> None:
        self.refs = extract("app.py", self.SOURCE).refs

    def _find(self, name: str, kind: str) -> list[str]:
        return [ref.scope for ref in self.refs if ref.name == name and ref.kind == kind]

    def test_call_is_attributed_to_enclosing_function(self) -> None:
        self.assertIn("outer", self._find("helper", "call"))

    def test_call_inside_method_uses_qualified_scope(self) -> None:
        self.assertIn("Widget.render", self._find("helper", "call"))

    def test_module_level_use_has_empty_scope(self) -> None:
        self.assertIn("", self._find("helper", "name"))

    def test_base_class_is_recorded_as_inheritance(self) -> None:
        self.assertEqual(self._find("Base", "base"), [""])

    def test_decorator_is_recorded_in_enclosing_scope(self) -> None:
        self.assertEqual(self._find("decorate", "decorator"), [""])

    def test_callee_is_not_double_counted_as_a_bare_name(self) -> None:
        # A call must produce one record, not both a call and a name load.
        in_outer = [
            ref.kind for ref in self.refs if ref.name == "helper" and ref.scope == "outer"
        ]
        self.assertEqual(in_outer, ["call"])

    def test_string_literals_are_not_references(self) -> None:
        refs = extract("s.py", "x = 'helper helper'\n").refs
        self.assertEqual([ref for ref in refs if ref.name == "helper"], [])


class CallGraphTest(unittest.TestCase):
    """The call graph must stay precise where names collide across modules."""

    FILES = {
        "pkg/__init__.py": "from pkg.core import engine\n",
        "pkg/core.py": textwrap.dedent(
            """
            def helper(value):
                return value


            def engine(value):
                return helper(value)
            """
        ).strip(),
        "app/main.py": textwrap.dedent(
            """
            from pkg import engine


            def run():
                return engine(1)
            """
        ).strip(),
        # A different module with a same-named method: nothing here may leak
        # into the call graph of pkg/core.py's engine.
        "other/thing.py": textwrap.dedent(
            """
            class Unrelated:
                def engine(self):
                    return 0

                def start(self):
                    return self.engine()
            """
        ).strip(),
        "tests/test_run.py": textwrap.dedent(
            """
            from app.main import run


            def test_run():
                assert run() == 1
            """
        ).strip(),
    }

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        for rel, body in self.FILES.items():
            target = self.root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(body + "\n", encoding="utf-8")
        self._patch = mock.patch(
            "navin.config.loader.get_config_path",
            return_value=self.root / ".cfg" / "config.json",
        )
        self._patch.start()
        self.index = CodeIndex(self.root)
        self.index.refresh(force=True)

    def tearDown(self) -> None:
        self._patch.stop()
        self._tmp.cleanup()

    def test_callers_reports_the_calling_definition(self) -> None:
        callers = self.index.callers("helper")
        self.assertEqual([(loc.path, loc.name) for loc in callers], [("pkg/core.py", "engine")])

    def test_callees_lists_only_project_definitions(self) -> None:
        names = [loc.name for loc in self.index.callees("engine")]
        self.assertIn("helper", names)

    def test_callees_resolve_through_a_package_reexport(self) -> None:
        # app/main.py imports from `pkg`, not from `pkg.core`; the edge must
        # still resolve rather than degrade to a bare name match.
        callees = self.index.callees("run")
        engine = next(loc for loc in callees if loc.name == "engine")
        self.assertEqual(engine.path, "pkg/core.py")
        self.assertNotIn("name match", engine.confidence)

    def test_impact_does_not_follow_unrelated_homonyms(self) -> None:
        report = self.index.impact("helper", max_depth=3)
        files = set(report["files"])
        self.assertIn("pkg/core.py", files)
        self.assertIn("app/main.py", files)
        # Unrelated.engine shares a name with pkg.core.engine but is reached
        # from nowhere in this chain.
        self.assertNotIn("other/thing.py", files)

    def test_impact_names_the_covering_tests(self) -> None:
        report = self.index.impact("helper", max_depth=3)
        self.assertEqual(report["tests"], ["tests/test_run.py"])

    def test_impact_reports_depth_levels_in_order(self) -> None:
        report = self.index.impact("helper", max_depth=3)
        first = [loc.name for loc in report["levels"][0]]
        self.assertEqual(first, ["engine"])
        self.assertIn("run", [loc.name for loc in report["levels"][1]])

    def test_builtin_call_does_not_resolve_to_a_project_symbol(self) -> None:
        (self.root / "pkg" / "shadow.py").write_text(
            "def dict():\n    return {}\n", encoding="utf-8"
        )
        (self.root / "pkg" / "uses.py").write_text(
            "def build():\n    return dict()\n", encoding="utf-8"
        )
        self.index.refresh(force=True)
        names = [loc.name for loc in self.index.callees("build")]
        self.assertNotIn("dict", names)

    def test_members_and_container_are_inverse(self) -> None:
        members = self.index.members("Unrelated")
        self.assertEqual(
            sorted(loc.name for loc in members),
            ["Unrelated.engine", "Unrelated.start"],
        )
        container = self.index.container("Unrelated.start")
        self.assertEqual([loc.name for loc in container], ["Unrelated"])

    def test_reference_cache_survives_a_new_index_instance(self) -> None:
        self.index.reference_count()
        fresh = CodeIndex(self.root)
        fresh.refresh()  # reuses the main cache, so entries carry no refs
        self.assertEqual(
            [(loc.path, loc.name) for loc in fresh.callers("helper")],
            [("pkg/core.py", "engine")],
        )

    def test_reference_cache_is_not_rewritten_when_already_complete(self) -> None:
        # Files with no references still need a cache record. Without one they
        # miss the cache forever, get re-parsed, and force a full rewrite on
        # every single reference query.
        (self.root / "pkg" / "empty.py").write_text("", encoding="utf-8")
        self.index.refresh(force=True)
        self.index.reference_count()
        stamp = refs_cache_path(self.root).stat().st_mtime_ns

        fresh = CodeIndex(self.root)
        fresh.refresh()
        fresh.reference_count()
        self.assertEqual(refs_cache_path(self.root).stat().st_mtime_ns, stamp)

    def test_edited_file_invalidates_its_cached_references(self) -> None:
        self.index.reference_count()
        (self.root / "pkg" / "core.py").write_text(
            "def helper(value):\n    return value\n\n\ndef engine(value):\n"
            "    return value\n",
            encoding="utf-8",
        )
        fresh = CodeIndex(self.root)
        fresh.refresh()
        self.assertEqual(fresh.callers("helper"), [])


class LiveRevalidationTest(unittest.TestCase):
    """The index must answer about the project as it is, not as it was.

    An agent edits a file and then asks who calls a symbol. Before revalidation
    the answer described the contents the file had when the process first looked,
    so the agent acted on code it had already replaced. A full refresh is far too
    slow to run on every query, so the cheap path has to stay correct.
    """

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()
        (self.root / "mod.py").write_text(
            "def alpha():\n    return 1\n", encoding="utf-8"
        )
        self.index = CodeIndex(self.root)
        self.index.ensure()

    def _write(self, rel: str, body: str) -> None:
        target = self.root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(textwrap.dedent(body), encoding="utf-8")
        # A same-second write with an identical size would look unchanged to a
        # stat comparison; bump the timestamp so the test exercises the logic
        # rather than a filesystem coincidence.
        stat = target.stat()
        os.utime(target, ns=(stat.st_atime_ns, stat.st_mtime_ns + 2_000_000))
        # Agent write paths funnel through record_file_before, which marks the
        # index dirty; mirror that so the throttled revalidation re-stats now.
        self.index.mark_dirty()

    def _names(self, symbol: str) -> list[str]:
        return [name for name, _ in self.index._by_name.get(symbol, [])]

    def test_a_symbol_added_after_the_first_query_is_found(self) -> None:
        self.assertEqual(self.index.definition("beta"), [])
        self._write("mod.py", """
            def alpha():
                return 1

            def beta():
                return alpha()
        """)
        self.assertTrue(self.index.definition("beta"))

    def test_a_symbol_removed_after_the_first_query_is_gone(self) -> None:
        self.assertTrue(self.index.definition("alpha"))
        self._write("mod.py", "def other():\n    return 1\n")
        self.assertEqual(self.index.definition("alpha"), [])

    def test_a_brand_new_file_is_picked_up(self) -> None:
        self._write("later.py", "def gamma():\n    return 2\n")
        self.assertTrue(self.index.definition("gamma"))

    def test_a_deleted_file_drops_out(self) -> None:
        self.assertTrue(self.index.definition("alpha"))
        (self.root / "mod.py").unlink()
        # Agent deletions go through record_file_before too (file_manage).
        self.index.mark_dirty()
        self.assertEqual(self.index.definition("alpha"), [])

    def test_callers_reflect_an_edit(self) -> None:
        """The reference layer is cached separately and must invalidate too."""
        self._write("mod.py", """
            def alpha():
                return 1

            def beta():
                return alpha()
        """)
        self.assertTrue(self.index.callers("alpha"))
        self._write("mod.py", """
            def alpha():
                return 1

            def beta():
                return 2
        """)
        self.assertEqual(self.index.callers("alpha"), [])

    def test_an_untouched_project_reports_no_change(self) -> None:
        """Revalidation must not claim work it did not do, or it would rebuild."""
        self.assertFalse(self.index.revalidate())
        self.assertFalse(self.index.revalidate())

    def test_a_non_indexable_file_still_shows_up_in_the_file_list(self) -> None:
        self._write("notes.md", "# hello\n")
        self.index.ensure()
        self.assertIn("notes.md", self.index.all_files)


class PathHelpTest(unittest.TestCase):
    """A missed path must come back with the best hint the index can give.

    With exactly one candidate the tool used to answer only "not in the
    index", withholding the very path it had already found and sending the
    caller off to grep for it.
    """

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tool = CodeIndexTool(workspace=Path(self._tmp.name))

    def _help(self, candidates: list[str]) -> str:
        index = mock.Mock()
        index.candidates_for.return_value = candidates
        return self.tool._path_help(index, "core.py")

    def test_a_single_candidate_is_proposed(self) -> None:
        out = self._help(["pkg/core.py"])
        self.assertIn("Did you mean 'pkg/core.py'", out)

    def test_several_candidates_are_listed_as_ambiguous(self) -> None:
        out = self._help(["a/core.py", "b/core.py"])
        self.assertIn("Ambiguous", out)
        self.assertIn("a/core.py", out)
        self.assertIn("b/core.py", out)

    def test_no_candidate_reports_a_plain_miss(self) -> None:
        out = self._help([])
        self.assertIn("not in the index", out)
        self.assertNotIn("Did you mean", out)


class ImportResolutionScopeTest(unittest.TestCase):
    """Hoisting the top-level prefix set must not change what resolves."""

    def test_a_module_under_a_top_level_package_still_resolves(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            (root / "src" / "pkg").mkdir(parents=True)
            (root / "src" / "pkg" / "__init__.py").write_text("", encoding="utf-8")
            (root / "src" / "pkg" / "leaf.py").write_text(
                "VALUE = 1\n", encoding="utf-8"
            )
            (root / "app.py").write_text(
                "from pkg.leaf import VALUE\n", encoding="utf-8"
            )
            index = CodeIndex(root)
            index.refresh()
            self.assertIn("src/pkg/leaf.py", index.dependencies("app.py"))

    def test_resolution_works_when_called_before_any_build(self) -> None:
        """The resolver has a fallback; a flat project must not depend on it."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            (root / "one.py").write_text("import two\n", encoding="utf-8")
            (root / "two.py").write_text("X = 1\n", encoding="utf-8")
            index = CodeIndex(root)
            index.refresh()
            self.assertIn("two.py", index.dependencies("one.py"))


class RevalidationThrottleTest(unittest.TestCase):
    """Back-to-back ensure() calls must not pay the stat-walk every time.

    A burst of index tools in one turn used to re-stat the whole tree per
    call (~200ms on 5k files). The TTL suppresses that; mark_dirty (wired to
    agent write paths) bypasses it so edits are never served stale.
    """

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()
        (self.root / "mod.py").write_text("def alpha():\n    return 1\n", encoding="utf-8")
        self.index = CodeIndex(self.root)
        self.index.ensure()

    def test_revalidate_is_throttled_within_the_ttl(self) -> None:
        with mock.patch(
            "navin.index.service.discover_files",
            side_effect=AssertionError("stat-walk should have been throttled"),
        ):
            self.assertFalse(self.index.revalidate())

    def test_mark_dirty_bypasses_the_throttle(self) -> None:
        target = self.root / "extra.py"
        target.write_text("def gamma():\n    return 3\n", encoding="utf-8")
        self.index.mark_dirty()
        self.index.ensure()
        self.assertTrue(self.index.definition("gamma"))


class WarmerTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()
        (self.root / "mod.py").write_text("def alpha():\n    return 1\n", encoding="utf-8")

    def test_schedule_warm_builds_the_index_off_thread(self) -> None:
        import time as _time

        from navin.index import service as index_service
        from navin.index.warmer import schedule_warm

        index_service._instances.pop(str(self.root), None)
        self.assertTrue(schedule_warm(self.root))
        deadline = _time.monotonic() + 10
        while _time.monotonic() < deadline:
            index = index_service._instances.get(str(self.root))
            if index is not None and index.stats is not None:
                break
            _time.sleep(0.02)
        index = index_service._instances.get(str(self.root))
        self.assertIsNotNone(index)
        self.assertIsNotNone(index.stats)
        self.assertTrue(index.definition("alpha"))

    def test_schedule_warm_rejects_missing_roots(self) -> None:
        from navin.index.warmer import schedule_warm

        self.assertFalse(schedule_warm(None))
        self.assertFalse(schedule_warm(self.root / "does-not-exist"))

    def test_note_file_written_marks_matching_index_dirty(self) -> None:
        from navin.index import service as index_service
        from navin.index.warmer import note_file_written

        index = index_service.get_index(self.root)
        index.ensure()
        index._dirty = False
        note_file_written(self.root / "mod.py")
        self.assertTrue(index._dirty)

    def test_warm_fulltext_and_semantic_are_best_effort(self) -> None:
        from unittest.mock import MagicMock, patch

        from navin.index.warmer import _warm_fulltext, _warm_semantic

        index = MagicMock()
        index.root = self.root
        with patch("navin.index.fulltext.FulltextIndex.available", return_value=False):
            _warm_fulltext(index)  # must not raise
        with (
            patch("navin.config.loader.load_config") as load_config,
            patch(
                "navin.agent.tools.code_index._is_free_provider",
                return_value=False,
            ),
        ):
            cfg = MagicMock()
            cfg.tools.semantic_search = MagicMock(enabled=None, provider="openai")
            load_config.return_value = cfg
            _warm_semantic(self.root, index)  # paid provider auto-off


if __name__ == "__main__":
    unittest.main()
