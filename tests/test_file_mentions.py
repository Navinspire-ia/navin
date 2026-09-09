# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for @-mentions of files and folders.

Covers the two halves: the fuzzy lookup that feeds the composer palette, and
the normalization plus prompt annotation that carry a chosen path to the model.
"""

from __future__ import annotations

import asyncio
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from navin.utils.file_mentions import (
    FILE_MENTION_METADATA_KEY,
    file_mention_context_provider,
    file_mention_runtime_lines,
    normalize_file_mentions,
)
from navin.webui.file_search_api import FileSearchError, file_search_payload


class _Scope:
    def __init__(self, root: Path) -> None:
        self.project_path = root


def _touch(root: Path, rel: str, body: str = "x\n") -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


class FileSearchTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        for rel in (
            "src/lib/rate-limit.ts",
            "src/components/DevWorkbench.tsx",
            "src/components/Button.tsx",
            "src/api.ts",
            "tests/test_rate_limit.py",
            "docs/rate-limiting.md",
        ):
            _touch(self.root, rel)
        self.scope = _Scope(self.root)

    def _paths(self, query: str, limit: int = 10) -> list[str]:
        payload = file_search_payload(self.scope, query, limit=limit)
        return [item["path"] for item in payload["items"]]

    def test_exact_stem_wins(self) -> None:
        self.assertEqual(self._paths("api")[0], "src/api.ts")

    def test_full_name_with_extension_matches(self) -> None:
        self.assertIn("src/lib/rate-limit.ts", self._paths("rate-limit.ts"))

    def test_abbreviation_finds_the_camel_case_file(self) -> None:
        """The classic Cmd+P gesture: initials, not a substring."""
        self.assertEqual(self._paths("dwb")[0], "src/components/DevWorkbench.tsx")

    def test_path_fragment_matches_as_a_path(self) -> None:
        results = self._paths("src/components/")
        self.assertTrue(all(r.startswith("src/components") for r in results), results)

    def test_source_ranks_above_its_test(self) -> None:
        results = self._paths("rate")
        self.assertLess(
            results.index("src/lib/rate-limit.ts"),
            results.index("tests/test_rate_limit.py"),
        )

    def test_directories_are_offered(self) -> None:
        payload = file_search_payload(self.scope, "components", limit=10)
        kinds = {item["path"]: item["kind"] for item in payload["items"]}
        self.assertEqual(kinds.get("src/components"), "directory")

    def test_file_outranks_the_directory_of_the_same_name(self) -> None:
        _touch(self.root, "src/button/index.ts")
        results = self._paths("button")
        self.assertEqual(results[0], "src/components/Button.tsx")

    def test_unmatched_query_returns_nothing(self) -> None:
        self.assertEqual(self._paths("zzqqxx"), [])

    def test_empty_query_still_offers_files(self) -> None:
        self.assertTrue(self._paths(""))

    def test_limit_is_honoured(self) -> None:
        self.assertLessEqual(len(self._paths("t", limit=3)), 3)

    def test_missing_project_is_an_error(self) -> None:
        with self.assertRaises(FileSearchError) as caught:
            file_search_payload(_Scope(self.root / "absent"), "a")
        self.assertEqual(caught.exception.status, 404)


class SymbolSearchTest(unittest.TestCase):
    """A user typing @handleSubmit means the function, not a file named after it."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        _touch(
            self.root,
            "src/checkout.py",
            "def handle_submit(order):\n    return order\n\n\nclass Throttler:\n    pass\n",
        )
        _touch(self.root, "src/api.py", "def unrelated():\n    pass\n")
        self.scope = _Scope(self.root)

    def _items(self, query: str, limit: int = 12) -> list[dict]:
        return file_search_payload(self.scope, query, limit=limit)["items"]

    def _symbols(self, query: str) -> list[dict]:
        return [item for item in self._items(query) if item["kind"] == "symbol"]

    def test_a_function_is_offered(self) -> None:
        names = [item["name"] for item in self._symbols("handle_submit")]
        self.assertIn("handle_submit", names)

    def test_the_symbol_carries_its_file_and_line(self) -> None:
        """Without the location the agent has to go looking for it again."""
        found = self._symbols("handle_submit")[0]
        self.assertEqual(found["path"], "src/checkout.py")
        self.assertEqual(found["line"], 1)

    def test_the_symbol_carries_its_kind(self) -> None:
        found = self._symbols("Throttler")[0]
        self.assertEqual(found["symbolKind"], "class")

    def test_a_prefix_is_enough(self) -> None:
        self.assertTrue(self._symbols("Throt"))

    def test_a_single_character_offers_no_symbols(self) -> None:
        """One letter matches thousands of definitions and none of them usefully."""
        self.assertEqual(self._symbols("h"), [])

    def test_a_path_fragment_offers_no_symbols(self) -> None:
        """A term with a separator is a path, and the index has nothing to say."""
        self.assertEqual(self._symbols("src/che"), [])

    def test_a_file_outranks_a_symbol_of_the_same_name(self) -> None:
        """`@checkout` most often means the file; the symbol stays available."""
        _touch(self.root, "src/other.py", "def checkout():\n    pass\n")
        items = self._items("checkout")
        self.assertEqual(items[0]["kind"], "file")
        self.assertIn("symbol", [item["kind"] for item in items])

    def test_symbols_do_not_crowd_out_files(self) -> None:
        body = "".join(f"def match_{index}():\n    pass\n" for index in range(30))
        _touch(self.root, "src/many.py", body)
        _touch(self.root, "src/match_1.py")
        items = self._items("match_1")
        self.assertLessEqual(sum(1 for i in items if i["kind"] == "symbol"), 5)
        self.assertIn("src/match_1.py", [i["path"] for i in items])


class NormalizationTest(unittest.TestCase):
    def test_relative_path_survives(self) -> None:
        self.assertEqual(
            normalize_file_mentions([{"path": "src/a.ts"}]),
            [{"path": "src/a.ts", "kind": "file"}],
        )

    def test_kind_is_preserved(self) -> None:
        got = normalize_file_mentions([{"path": "src", "kind": "directory"}])
        self.assertEqual(got[0]["kind"], "directory")

    def test_unknown_kind_falls_back_to_file(self) -> None:
        got = normalize_file_mentions([{"path": "src", "kind": "socket"}])
        self.assertEqual(got[0]["kind"], "file")

    def test_leading_dot_slash_is_stripped(self) -> None:
        got = normalize_file_mentions([{"path": "./src/a.ts"}])
        self.assertEqual(got[0]["path"], "src/a.ts")

    def test_backslashes_become_separators(self) -> None:
        got = normalize_file_mentions([{"path": "src\\a.ts"}])
        self.assertEqual(got[0]["path"], "src/a.ts")

    def test_traversal_is_rejected(self) -> None:
        self.assertEqual(normalize_file_mentions([{"path": "../../etc/passwd"}]), [])

    def test_absolute_path_is_rejected_not_rewritten(self) -> None:
        """Stripping the slash would silently point at a different file."""
        self.assertEqual(normalize_file_mentions([{"path": "/etc/passwd"}]), [])
        self.assertEqual(normalize_file_mentions([{"path": "C:/Windows/x"}]), [])

    def test_duplicates_collapse(self) -> None:
        got = normalize_file_mentions([{"path": "a.ts"}, {"path": "./a.ts"}])
        self.assertEqual(len(got), 1)

    def test_count_is_capped(self) -> None:
        got = normalize_file_mentions([{"path": f"f{i}.ts"} for i in range(50)])
        self.assertEqual(len(got), 12)

    def test_junk_is_ignored(self) -> None:
        for raw in ("not a list", None, [{"path": ""}], [{"nope": 1}], [42]):
            with self.subTest(raw=raw):
                self.assertEqual(normalize_file_mentions(raw), [])

    def test_overlong_path_is_rejected(self) -> None:
        self.assertEqual(normalize_file_mentions([{"path": "a/" * 300}]), [])


class SymbolNormalizationTest(unittest.TestCase):
    @staticmethod
    def _one(**overrides):
        base = {"path": "a.py", "kind": "symbol", "name": "run", "line": 7}
        base.update(overrides)
        got = normalize_file_mentions([base])
        return got[0] if got else None

    def test_a_symbol_keeps_its_name_and_line(self) -> None:
        self.assertEqual(
            self._one(), {"path": "a.py", "kind": "symbol", "name": "run", "line": "7"}
        )

    def test_a_qualified_name_survives(self) -> None:
        self.assertEqual(self._one(name="Parser.parse")["name"], "Parser.parse")

    def test_two_symbols_in_one_file_are_two_mentions(self) -> None:
        """Deduplicating on path alone would drop the second one."""
        got = normalize_file_mentions([
            {"path": "a.py", "kind": "symbol", "name": "run", "line": 7},
            {"path": "a.py", "kind": "symbol", "name": "stop", "line": 20},
        ])
        self.assertEqual([m["name"] for m in got], ["run", "stop"])

    def test_the_same_symbol_twice_collapses(self) -> None:
        got = normalize_file_mentions([
            {"path": "a.py", "kind": "symbol", "name": "run", "line": 7},
            {"path": "a.py", "kind": "symbol", "name": "run", "line": 7},
        ])
        self.assertEqual(len(got), 1)

    def test_a_file_and_a_symbol_in_it_coexist(self) -> None:
        got = normalize_file_mentions([
            {"path": "a.py", "kind": "symbol", "name": "run", "line": 7},
            {"path": "a.py", "kind": "file"},
        ])
        self.assertEqual([m["kind"] for m in got], ["symbol", "file"])

    def test_a_nameless_symbol_degrades_to_its_file(self) -> None:
        self.assertEqual(self._one(name=None)["kind"], "file")

    def test_a_name_that_is_not_an_identifier_is_refused(self) -> None:
        """The name reaches a prompt, so it may not carry arbitrary text."""
        for bad in ("rm -rf /", "a\nb", "x; drop table", "<script>", "a" * 300):
            with self.subTest(name=bad):
                self.assertEqual(self._one(name=bad)["kind"], "file")

    def test_a_nonsense_line_becomes_zero(self) -> None:
        for bad in ("abc", None, -3, 0, 10**9):
            with self.subTest(line=bad):
                self.assertEqual(self._one(line=bad)["line"], "0")

    def test_a_symbol_outside_the_project_is_still_refused(self) -> None:
        self.assertEqual(
            normalize_file_mentions(
                [{"path": "/etc/passwd", "kind": "symbol", "name": "root"}]
            ),
            [],
        )


class RuntimeLinesTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        _touch(self.root, "src/api.ts", "one\ntwo\nthree\n")
        (self.root / "src/lib").mkdir(parents=True)

    def _lines(self, mentions: list[dict[str, str]]) -> list[str]:
        return file_mention_runtime_lines(
            {FILE_MENTION_METADATA_KEY: mentions}, workspace=self.root
        )

    def test_file_is_announced_with_size_and_language(self) -> None:
        line = self._lines([{"path": "src/api.ts", "kind": "file"}])[0]
        self.assertIn("File Attachment: 'src/api.ts'", line)
        self.assertIn("3 lines", line)
        self.assertIn("typescript", line)
        self.assertIn("read_file", line)

    def test_trailing_newline_does_not_inflate_the_line_count(self) -> None:
        _touch(self.root, "exact.py", "a\nb\n")
        self.assertIn("2 lines", self._lines([{"path": "exact.py"}])[0])
        _touch(self.root, "nonl.py", "a\nb")
        self.assertIn("2 lines", self._lines([{"path": "nonl.py"}])[0])

    def test_folder_is_announced_as_a_folder(self) -> None:
        line = self._lines([{"path": "src/lib", "kind": "directory"}])[0]
        self.assertIn("Folder Attachment", line)
        self.assertIn("list_dir", line)

    def test_directory_kind_is_taken_from_disk_not_the_client(self) -> None:
        """A client claiming a folder is a file must not mislead the model."""
        line = self._lines([{"path": "src/lib", "kind": "file"}])[0]
        self.assertIn("Folder Attachment", line)

    def test_symbol_is_announced_with_its_definition_site(self) -> None:
        line = self._lines(
            [{"path": "src/api.ts", "kind": "symbol", "name": "fetchAll", "line": 2}]
        )[0]
        self.assertIn("Symbol Attachment: 'fetchAll'", line)
        self.assertIn("src/api.ts:2", line)
        self.assertIn("references", line)

    def test_a_symbol_without_a_line_still_names_its_file(self) -> None:
        line = self._lines(
            [{"path": "src/api.ts", "kind": "symbol", "name": "fetchAll", "line": 0}]
        )[0]
        self.assertIn("src/api.ts", line)
        self.assertNotIn(":0", line)

    def test_a_symbol_claimed_on_a_folder_is_announced_as_a_folder(self) -> None:
        """Disk wins over what the client asserts, as it does for files."""
        line = self._lines([{"path": "src/lib", "kind": "symbol", "name": "x"}])[0]
        self.assertIn("Folder Attachment", line)

    def test_missing_path_is_reported_as_an_error(self) -> None:
        line = self._lines([{"path": "src/gone.ts"}])[0]
        self.assertIn("Attachment Error", line)
        self.assertIn("does not exist", line)

    def test_escaping_path_never_reaches_the_model(self) -> None:
        self.assertEqual(self._lines([{"path": "../outside.txt"}]), [])

    def test_no_mentions_means_no_lines(self) -> None:
        self.assertEqual(self._lines([]), [])
        self.assertEqual(file_mention_runtime_lines({}, workspace=self.root), [])
        self.assertEqual(
            file_mention_runtime_lines(
                {FILE_MENTION_METADATA_KEY: [{"path": "src/api.ts"}]}, workspace=None
            ),
            [],
        )


class ContextProviderTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        _touch(self.root, "src/api.ts")

    def _provide(self, metadata: dict) -> object:
        from navin.agent.tools.context import RequestContext

        request = RequestContext(
            channel="websocket",
            chat_id="chat-1",
            original_user_text="explain @src/api.ts",
            metadata=metadata,
            workspace=self.root,
        )
        return asyncio.run(file_mention_context_provider(request))

    def test_block_is_produced_for_a_mention(self) -> None:
        block = self._provide(
            {FILE_MENTION_METADATA_KEY: [{"path": "src/api.ts", "kind": "file"}]}
        )
        self.assertIsNotNone(block)
        self.assertEqual(block.source, "file_mentions")
        self.assertIn("src/api.ts", block.content)
        self.assertIn("Runtime Context", block.content)

    def test_no_block_without_mentions(self) -> None:
        self.assertIsNone(self._provide({}))


if __name__ == "__main__":
    unittest.main()
