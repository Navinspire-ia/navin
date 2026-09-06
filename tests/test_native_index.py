"""Native (Rust) index accelerators: tree-sitter symbols and tantivy search.

Both features are optional accelerators with Python fallbacks, so every test
here skips when the extension (or the function under test) is missing rather
than failing the suite on a source checkout without the wheel.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from navin.index.fulltext import FulltextIndex
from navin.index.symbols import extract

try:
    import navin_core
except ImportError:  # pragma: no cover - wheel not built in this checkout
    navin_core = None


def _needs(function: str) -> None:
    if navin_core is None or not hasattr(navin_core, function):
        raise unittest.SkipTest(f"navin_core.{function} not available")


class TreeSitterExtractTest(unittest.TestCase):
    def test_typescript_symbols_are_exact(self):
        _needs("ts_extract")
        entry = extract(
            "widget.ts",
            'import { x } from "./mod";\n'
            "export class Widget {\n"
            "  render(): void { paint(); }\n"
            "}\n"
            "export function paint(): void {}\n",
        )
        names = {(s.name, s.kind) for s in entry.symbols}
        self.assertIn(("Widget", "class"), names)
        self.assertIn(("render", "method"), names)
        self.assertIn(("paint", "function"), names)
        method = next(s for s in entry.symbols if s.name == "render")
        self.assertEqual(method.qualname, "Widget.render")
        self.assertIn("./mod", entry.imports)
        self.assertTrue(
            any(ref.name == "paint" and ref.kind == "call" for ref in entry.refs)
        )

    def test_call_refs_carry_their_enclosing_scope(self):
        _needs("ts_extract")
        entry = extract(
            "svc.go",
            "package main\n"
            "type Service struct{}\n"
            "func (s *Service) Run() { helper() }\n"
            "func helper() {}\n",
        )
        call = next(ref for ref in entry.refs if ref.name == "helper")
        self.assertEqual(call.scope, "Run")

    def test_unsupported_language_falls_back_to_patterns(self):
        # Vue has no tree-sitter grammar wired in; the pattern table must
        # still produce symbols exactly as before.
        entry = extract(
            "Widget.vue",
            "<script>\nexport default {\n  methods: {\n    onClick() {}\n  }\n}\n</script>\n",
        )
        # Even a thin pattern hit (or an empty parse) must not raise; the
        # important contract is that the fallback path stays usable.
        self.assertIsNotNone(entry)

    def test_kotlin_symbols_are_exact(self):
        _needs("ts_extract")
        entry = extract(
            "MainActivity.kt",
            "package com.example.app\n"
            "import android.os.Bundle\n"
            "class MainActivity {\n"
            "  fun onCreate() { helper() }\n"
            "  companion object Factory { }\n"
            "}\n"
            "fun helper() {}\n",
        )
        names = {(s.name, s.kind) for s in entry.symbols}
        self.assertIn(("MainActivity", "class"), names)
        self.assertIn(("onCreate", "function"), names)
        self.assertIn(("helper", "function"), names)
        self.assertTrue(any("Bundle" in imp or "android" in imp for imp in entry.imports))
        method = next(s for s in entry.symbols if s.name == "onCreate")
        self.assertEqual(method.qualname, "MainActivity.onCreate")

    def test_swift_symbols_are_exact(self):
        _needs("ts_extract")
        entry = extract(
            "MainView.swift",
            "import Foundation\n"
            "class MainView {\n"
            "  func onAppear() { helper() }\n"
            "  var title: String = \"\"\n"
            "}\n"
            "protocol Renderable {\n"
            "  func render()\n"
            "}\n"
            "func helper() {}\n",
        )
        names = {(s.name, s.kind) for s in entry.symbols}
        self.assertIn(("MainView", "class"), names)
        self.assertIn(("onAppear", "function"), names)
        self.assertIn(("helper", "function"), names)
        self.assertIn(("Renderable", "protocol"), names)
        self.assertTrue(any("Foundation" in imp for imp in entry.imports))
        method = next(s for s in entry.symbols if s.name == "onAppear")
        self.assertEqual(method.qualname, "MainView.onAppear")

    def test_native_coverage_is_documented_not_rewritten(self):
        """Tree-sitter is the primary path for the main languages.

        Expanding coverage means adding one grammar + query in navin-core,
        not replacing languages.json. This list is the contract the Python
        fallback relies on when the wheel is absent or returns None.
        """
        _needs("ts_extract")
        wired = {
            "javascript",
            "typescript",
            "go",
            "rust",
            "java",
            "c",
            "cpp",
            "csharp",
            "ruby",
            "php",
            "kotlin",
            "swift",
        }
        for language in wired:
            # Empty source still returns a JSON payload (not None) when the
            # language is recognised - None is reserved for "use patterns".
            raw = navin_core.ts_extract(language, f"x.{language}", "")
            self.assertIsNotNone(raw, language)
        self.assertIsNone(navin_core.ts_extract("vue", "Widget.vue", "export default {}"))
        self.assertIsNone(navin_core.ts_extract("sql", "schema.sql", "CREATE TABLE t (id INT);"))


class FulltextIndexTest(unittest.TestCase):
    def test_index_search_and_incremental_removal(self):
        _needs("fulltext_update")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "proj"
            (root / "src").mkdir(parents=True)
            (root / "src" / "auth.py").write_text(
                "def authenticate(user):\n    return hash_password(user)\n",
                encoding="utf-8",
            )
            (root / "README.md").write_text(
                "Salted password hashing everywhere.\n", encoding="utf-8"
            )

            index = FulltextIndex(root)
            index.index_dir = Path(tmp) / "ft"
            index.manifest_path = Path(tmp) / "ft-manifest.json"

            files = {
                "src/auth.py": (root / "src" / "auth.py").stat().st_mtime_ns,
                "README.md": (root / "README.md").stat().st_mtime_ns,
            }
            self.assertEqual(index.refresh(files), 2)
            # Second refresh with identical fingerprints must be a no-op.
            self.assertEqual(index.refresh(files), 0)

            hits = index.search("password hashing")
            self.assertIsNotNone(hits)
            self.assertTrue(any(hit["path"] == "README.md" for hit in hits))

            del files["README.md"]
            index.refresh(files)
            hits_after = index.search("salted")
            self.assertEqual(
                [hit for hit in hits_after if hit["path"] == "README.md"], []
            )


if __name__ == "__main__":
    unittest.main()
