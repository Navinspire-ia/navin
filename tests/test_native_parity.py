"""Phase 0 safety net: native (Rust) backends must match Python fallbacks.

These tests never change tool schemas or production behaviour. They only
compare optional accelerators against the pure-Python / CLI reference so a
future optimisation cannot silently diverge.

Every case skips when ``navin_core`` (or the function under test) is absent,
so a source checkout without ``make native`` stays green.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from navin.agent.tools import scrape as scrape_mod
from navin.utils import git_state as git_state_mod
from navin.utils.git_state import clear_cache, repo_state
from navin.utils.native import has_native

try:
    import navin_core
except ImportError:  # pragma: no cover - wheel not built in this checkout
    navin_core = None


def _needs(function: str) -> None:
    if navin_core is None or not hasattr(navin_core, function):
        raise unittest.SkipTest(f"navin_core.{function} not available")


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, check=False
    )


class NativeModuleContractTest(unittest.TestCase):
    def test_disable_native_env_forces_none(self) -> None:
        import navin.utils.native as native_mod

        prev = os.environ.get("NAVIN_DISABLE_NATIVE")
        os.environ["NAVIN_DISABLE_NATIVE"] = "1"
        try:
            native_mod._loaded = False
            native_mod._module = None
            self.assertIsNone(native_mod.native())
            self.assertFalse(native_mod.has_native())
        finally:
            if prev is None:
                os.environ.pop("NAVIN_DISABLE_NATIVE", None)
            else:
                os.environ["NAVIN_DISABLE_NATIVE"] = prev
            native_mod._loaded = False
            native_mod._module = None

    @unittest.skipUnless(has_native(), "navin_core not installed")
    def test_hot_path_exports_exist(self) -> None:
        required = (
            "grep_scan",
            "stat_tree",
            "read_files",
            "hash_files",
            "git_state",
            "ts_extract",
            "fulltext_update",
            "fulltext_search",
            "scrape_clean",
            "scrape_extract",
        )
        for name in required:
            with self.subTest(name=name):
                self.assertTrue(hasattr(navin_core, name), name)


class StatTreeParityTest(unittest.TestCase):
    @unittest.skipUnless(has_native(), "navin_core not installed")
    def test_mtime_ns_and_size_match_os_stat(self) -> None:
        _needs("stat_tree")
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            path = root / "src" / "widget.py"
            path.write_text("x = 1\n", encoding="utf-8")
            (root / "empty.txt").write_text("", encoding="utf-8")

            rels = ["src/widget.py", "empty.txt", "missing.py"]
            pairs = navin_core.stat_tree(str(root), rels)

            self.assertNotIn("missing.py", pairs)
            for rel in ("src/widget.py", "empty.txt"):
                st = (root / rel).stat()
                self.assertEqual(pairs[rel], (st.st_mtime_ns, st.st_size), rel)


class ReadFilesParityTest(unittest.TestCase):
    @unittest.skipUnless(has_native(), "navin_core not installed")
    def test_bytes_match_path_read(self) -> None:
        _needs("read_files")
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "note.txt"
            body = "café\nline2\n".encode("utf-8")
            path.write_bytes(body)

            blobs = navin_core.read_files([str(path)], max_bytes=2_000_000)
            data, err = blobs[str(path)]
            self.assertIsNone(err)
            self.assertEqual(bytes(data), body)

    @unittest.skipUnless(has_native(), "navin_core not installed")
    def test_oversize_file_is_reported_not_partial(self) -> None:
        _needs("read_files")
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "big.bin"
            path.write_bytes(b"abcdef")
            blobs = navin_core.read_files([str(path)], max_bytes=3)
            data, err = blobs[str(path)]
            self.assertIsNone(data)
            self.assertIsNotNone(err)


class HashFilesParityTest(unittest.TestCase):
    @unittest.skipUnless(has_native(), "navin_core not installed")
    def test_digest_is_stable_blake3(self) -> None:
        _needs("hash_files")
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "payload.txt"
            path.write_bytes(b"hello navin\n")
            first = navin_core.hash_files([str(path)], max_bytes=2_000_000)
            second = navin_core.hash_files([str(path)], max_bytes=2_000_000)
            self.assertEqual(first[str(path)], second[str(path)])
            # Known BLAKE3-256 hex of b"hello navin\n" (must not drift across opts).
            self.assertEqual(
                first[str(path)],
                "ff3b0b9234d6e7741c3816739ca8504ff3ecdbdb2645fe7bb4e208df48559139",
            )
            self.assertNotIn(str(Path(tmp) / "missing.txt"), first)


class GitStateParityTest(unittest.TestCase):
    """libgit2 path must mirror ``git status --porcelain=v2`` field for field."""

    def setUp(self) -> None:
        if shutil.which("git") is None:
            self.skipTest("git not installed")
        if not has_native() or not hasattr(navin_core, "git_state"):
            self.skipTest("navin_core.git_state not available")
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

    def _write(self, name: str, body: str) -> None:
        target = self.root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")

    def _compare_native_and_cli(self) -> None:
        clear_cache()
        native_state = repo_state(self.root, refresh=True)
        with mock.patch.object(git_state_mod, "_read_native", return_value=None):
            clear_cache()
            cli_state = repo_state(self.root, refresh=True)
        for field in (
            "is_repo",
            "branch",
            "detached",
            "head",
            "upstream",
            "ahead",
            "behind",
            "staged",
            "unstaged",
            "untracked",
            "conflicted",
            "unavailable",
        ):
            self.assertEqual(
                getattr(native_state, field),
                getattr(cli_state, field),
                field,
            )

    def test_clean_tree(self) -> None:
        self._write("a.txt", "one\n")
        self._commit()
        self._compare_native_and_cli()

    def test_dirty_tree_with_space_in_path(self) -> None:
        self._write("a.txt", "one\n")
        self._commit()
        self._write("a.txt", "two\n")
        self._write("with space.txt", "new\n")
        self._write("pkg/nested.py", "x\n")
        self._compare_native_and_cli()

    def test_staged_and_unstaged_same_file(self) -> None:
        self._write("a.txt", "one\n")
        self._commit()
        self._write("a.txt", "two\n")
        _git(self.root, "add", "a.txt")
        self._write("a.txt", "three\n")
        self._compare_native_and_cli()


class ScrapeCleanParityTest(unittest.TestCase):
    @unittest.skipUnless(has_native(), "navin_core not installed")
    def test_clean_matches_python_fallback(self) -> None:
        _needs("scrape_clean")
        samples = (
            "  hello   world  \n\n\n  foo  ",
            "a\n\n\nb\n",
            "  ",
            "line1\n   \nline2",
            "déjà\u00a0vu",
        )
        for sample in samples:
            with self.subTest(sample=sample):
                self.assertEqual(
                    navin_core.scrape_clean(sample),
                    scrape_mod._clean_text(sample),
                )


class ScrapeExtractContractTest(unittest.TestCase):
    """Extract formatting may differ slightly; the public contract must not."""

    @unittest.skipUnless(has_native(), "navin_core not installed")
    def test_extract_returns_expected_shape_and_title(self) -> None:
        _needs("scrape_extract")
        html = (
            "<html><head><title>Hi</title>"
            "<meta name='description' content='D'></head>"
            "<body><h1>Hi</h1><p>Hello <a href='/a'>A</a></p>"
            "<script>x()</script></body></html>"
        )
        base = "https://example.com/page"
        native_payload = json.loads(navin_core.scrape_extract(html, base))
        python_payload = scrape_mod._extract_html_py(html, base)

        self.assertEqual(set(native_payload), {"title", "text", "markdown", "links", "meta"})
        self.assertEqual(set(python_payload), {"title", "text", "markdown", "links", "meta"})
        self.assertEqual(native_payload["title"], python_payload["title"])
        self.assertEqual(native_payload["title"], "Hi")
        # Script bodies must not leak into readable text on either path.
        self.assertNotIn("x()", native_payload["text"])
        self.assertNotIn("x()", python_payload["text"])


if __name__ == "__main__":
    unittest.main()
