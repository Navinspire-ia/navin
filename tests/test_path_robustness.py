"""Tools must work in whatever directory the user actually has.

A project is not always a tidy `~/src/app`. It sits under "My Documents", it is
named in Arabic or Japanese, it contains an ampersand, it is nested deep enough
to pass Windows' 260-character limit. Each of those breaks a different layer:
quoting for the shell, encoding for the filesystem, and the Win32 path API. The
cases below are run against the real tools rather than against a path helper, so
a regression anywhere in the chain surfaces here.
"""

import asyncio
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from navin.agent.tools.apply_patch import ApplyPatchTool
from navin.agent.tools.code_index import CodeIndexTool
from navin.agent.tools.file_state import FileStates
from navin.agent.tools.filesystem import (
    EditFileTool,
    ListDirTool,
    ReadFileTool,
    WriteFileTool,
)
from navin.agent.tools.search import FindFilesTool, GrepTool
from navin.utils import longpath

# Directory names that each break a different layer. Every one of them is legal
# on Linux and macOS; the Windows-illegal ones are filtered at runtime rather
# than removed, so the coverage stays maximal per platform.
AWKWARD_NAMES = [
    ("space", "my project"),
    ("spaces_many", "a b  c"),
    ("accents", "dossier-éàü"),
    ("cjk", "プロジェクト"),
    ("arabic", "مشروع"),
    ("emoji", "proj-🚀"),
    ("dot_prefix", ".hidden"),
    ("dot_inside", "v1.2.3"),
    ("ampersand", "r&d"),
    ("parens", "proj (copy)"),
    ("hash", "issue#42"),
    ("dollar", "cost$"),
    ("quote", "it's"),
    ("bracket", "arr[0]"),
    ("plus", "c++"),
    ("at", "user@host"),
    ("percent", "100%done"),
    ("caret", "a^b"),
    ("equals", "k=v"),
    ("comma", "a,b"),
    ("semicolon", "a;b"),
    ("backtick", "a`b"),
    ("exclam", "urgent!"),
    ("tilde", "draft~"),
    ("unicode_nfd", "cafe\u0301"),  # decomposed é, macOS normalises this
]


def _run(coro) -> str:
    result = asyncio.run(coro)
    return str(getattr(result, "content", result))


def _usable(root: Path, name: str) -> Path | None:
    """Create the directory, or return None when the platform forbids the name."""
    candidate = root / name
    try:
        candidate.mkdir(parents=True, exist_ok=True)
        probe = candidate / "probe.txt"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except (OSError, ValueError, UnicodeError):
        return None
    return candidate


class AwkwardDirectoryTest(unittest.TestCase):
    """Every path-taking tool, against every directory name the OS accepts."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()
        self.states = FileStates()

    def _kw(self) -> dict:
        return {
            "workspace": self.root,
            "allowed_dir": self.root,
            "file_states": self.states,
        }

    def test_the_whole_file_chain_survives_every_directory_name(self) -> None:
        skipped: list[str] = []
        for label, name in AWKWARD_NAMES:
            with self.subTest(name=label):
                directory = _usable(self.root, name)
                if directory is None:
                    skipped.append(label)
                    self.skipTest(f"{label!r} is not a legal name on this platform")

                # The agent addresses files by their project-relative path, which
                # is what it would read back out of list_dir or grep.
                rel = f"{directory.name}/note.py"

                out = _run(WriteFileTool(**self._kw()).execute(
                    path=rel, content="def marker():\n    return 1\n"
                ))
                self.assertNotIn("Error", out, f"write failed for {label}")

                out = _run(ReadFileTool(**self._kw()).execute(path=rel))
                self.assertIn("def marker", out, f"read failed for {label}")

                out = _run(ListDirTool(**self._kw()).execute(path=directory.name))
                self.assertIn("note.py", out, f"list_dir failed for {label}")

                out = _run(GrepTool(**self._kw()).execute(pattern="marker"))
                self.assertIn("note.py", out, f"grep failed for {label}")

                out = _run(FindFilesTool(**self._kw()).execute(query="note"))
                self.assertIn("note.py", out, f"find_files failed for {label}")

                out = _run(EditFileTool(**self._kw()).execute(
                    path=rel, old_text="return 1", new_text="return 2"
                ))
                self.assertNotIn("Error", out, f"edit_file failed for {label}")
                self.assertIn("return 2", (directory / "note.py").read_text(encoding="utf-8"))

                out = _run(ApplyPatchTool(
                    workspace=self.root, file_states=self.states
                ).execute(edits=[{
                    "path": rel, "action": "replace",
                    "old_text": "return 2", "new_text": "return 3",
                }]))
                self.assertNotIn("Error:", out, f"apply_patch failed for {label}")

        if len(skipped) == len(AWKWARD_NAMES):
            self.fail("every name was skipped; the probe itself is broken")

    def test_the_code_index_finds_symbols_under_every_directory_name(self) -> None:
        for label, name in AWKWARD_NAMES:
            directory = _usable(self.root, name)
            if directory is None:
                continue
            (directory / "mod.py").write_text(
                f"def sym_{label}():\n    return 1\n", encoding="utf-8"
            )
        for label, name in AWKWARD_NAMES:
            if not (self.root / name).is_dir():
                continue
            with self.subTest(name=label):
                out = _run(CodeIndexTool(**self._kw()).execute(
                    action="definition", name=f"sym_{label}"
                ))
                self.assertIn(f"sym_{label}", out)


class DeepPathTest(unittest.TestCase):
    """Windows refuses paths over 260 characters unless long paths are enabled.

    A project checked out under a long user directory reaches that limit with
    ordinary nesting like node_modules, so this is a real configuration rather
    than a contrived one.
    """

    def setUp(self) -> None:
        # Not TemporaryDirectory: its cleanup runs through shutil.rmtree, which
        # hits the very limit this test is about and fails to delete the tree.
        self.root = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(self._remove_tree)
        self.states = FileStates()

    def _remove_tree(self) -> None:
        shutil.rmtree(longpath.io_path(self.root), ignore_errors=True)

    def _kw(self) -> dict:
        return {
            "workspace": self.root,
            "allowed_dir": self.root,
            "file_states": self.states,
        }

    def _deep_relative(self, total: int) -> str:
        """Nest 30-character segments until the absolute path passes `total`."""
        segment = "d" * 30
        parts: list[str] = []
        while len(str(self.root)) + sum(len(p) + 1 for p in parts) < total:
            parts.append(segment)
        return "/".join([*parts, "deep.txt"])

    def test_the_whole_chain_works_past_the_windows_limit(self) -> None:
        rel = self._deep_relative(300)
        out = _run(WriteFileTool(**self._kw()).execute(path=rel, content="deep = 1\n"))
        self.assertNotIn("Error", out, "write failed past MAX_PATH")

        out = _run(ReadFileTool(**self._kw()).execute(path=rel))
        self.assertIn("deep = 1", out, "read failed past MAX_PATH")

        out = _run(EditFileTool(**self._kw()).execute(
            path=rel, old_text="deep = 1", new_text="deep = 2"
        ))
        self.assertNotIn("Error", out, "edit failed past MAX_PATH")

        out = _run(ApplyPatchTool(workspace=self.root, file_states=self.states).execute(
            edits=[{
                "path": rel, "action": "replace",
                "old_text": "deep = 2", "new_text": "deep = 3",
            }]
        ))
        self.assertNotIn("Error:", out, "apply_patch failed past MAX_PATH")

        out = _run(ListDirTool(**self._kw()).execute(path=rel.rsplit("/", 1)[0]))
        self.assertIn("deep.txt", out, "list_dir failed past MAX_PATH")

    def test_a_very_deep_path_still_works(self) -> None:
        """Nesting well past the limit, as node_modules routinely produces."""
        rel = self._deep_relative(500)
        out = _run(WriteFileTool(**self._kw()).execute(path=rel, content="deeper\n"))
        self.assertNotIn("Error", out)
        self.assertIn("deeper", _run(ReadFileTool(**self._kw()).execute(path=rel)))


class MissingFileRecoveryTest(unittest.TestCase):
    """A mis-guessed file name must recover instead of dead-ending the agent.

    Models routinely invent descriptive names for generated artifacts
    ("review-report-my-topic.html" when the tool wrote a stamped name) or
    look for the right basename in the wrong directory. Reading is
    non-destructive, so read_file resolves the obvious cases itself and
    discloses the substitution in the output.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()
        self.states = FileStates()

    def _kw(self) -> dict:
        return {
            "workspace": self.root,
            "allowed_dir": self.root,
            "file_states": self.states,
        }

    def test_an_invented_report_name_reads_the_stamped_report(self) -> None:
        (self.root / "review-report-20260810-1123.html").write_text(
            "<html>real report</html>\n", encoding="utf-8"
        )
        out = _run(ReadFileTool(**self._kw()).execute(
            path="review-report-navin-code-vs-cursor.html"
        ))
        self.assertIn("real report", out)
        self.assertIn("closest match", out)
        self.assertIn("review-report-20260810-1123.html", out)

    def test_several_stamped_reports_pick_the_newest(self) -> None:
        old = self.root / "review-report-20260809-0900.html"
        new = self.root / "review-report-20260810-1123.html"
        old.write_text("<html>old</html>\n", encoding="utf-8")
        new.write_text("<html>new</html>\n", encoding="utf-8")
        os.utime(old, (1_000_000_000, 1_000_000_000))
        out = _run(ReadFileTool(**self._kw()).execute(
            path="review-report-anything.html"
        ))
        self.assertIn("new", out)
        self.assertIn("review-report-20260810-1123.html", out)

    def test_the_right_basename_in_the_wrong_directory_is_found(self) -> None:
        (self.root / "reports").mkdir()
        (self.root / "reports" / "summary.md").write_text("# summary\n", encoding="utf-8")
        out = _run(ReadFileTool(**self._kw()).execute(path="summary.md"))
        self.assertIn("# summary", out)
        self.assertIn("closest match", out)

    def test_an_ambiguous_basename_still_reports_not_found(self) -> None:
        for sub in ("a", "b"):
            (self.root / sub).mkdir()
            (self.root / sub / "notes.md").write_text(f"{sub}\n", encoding="utf-8")
        out = _run(ReadFileTool(**self._kw()).execute(path="notes.md"))
        self.assertIn("not found", out.lower())

    def test_a_totally_unrelated_name_still_reports_not_found(self) -> None:
        (self.root / "main.py").write_text("x = 1\n", encoding="utf-8")
        out = _run(ReadFileTool(**self._kw()).execute(path="does-not-exist.rs"))
        self.assertIn("not found", out.lower())


class CaseSensitivityTest(unittest.TestCase):
    """macOS and Windows fold case; Linux does not. Tools must not assume either."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()
        self.states = FileStates()
        (self.root / "src").mkdir()
        (self.root / "src" / "App.tsx").write_text("export const App = 1\n", encoding="utf-8")

    def _kw(self) -> dict:
        return {
            "workspace": self.root,
            "allowed_dir": self.root,
            "file_states": self.states,
        }

    def test_the_exact_case_always_reads(self) -> None:
        out = _run(ReadFileTool(**self._kw()).execute(path="src/App.tsx"))
        self.assertIn("export const App", out)

    def test_a_wrong_case_either_reads_or_says_it_is_missing(self) -> None:
        out = _run(ReadFileTool(**self._kw()).execute(path="src/app.tsx"))
        insensitive = (self.root / "src" / "app.tsx").is_file()
        if insensitive:
            self.assertIn("export const App", out)
        else:
            self.assertIn("not found", out.lower())
            # A near-miss on case is the most likely typo, so the suggestion has
            # to name the real file rather than leave the agent guessing.
            self.assertIn("App.tsx", out)


class TrailingAndSeparatorTest(unittest.TestCase):
    """Separator and trailing-slash noise the model produces routinely."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()
        self.states = FileStates()
        (self.root / "pkg").mkdir()
        (self.root / "pkg" / "mod.py").write_text("x = 1\n", encoding="utf-8")

    def _kw(self) -> dict:
        return {
            "workspace": self.root,
            "allowed_dir": self.root,
            "file_states": self.states,
        }

    def test_a_trailing_slash_on_a_directory_is_accepted(self) -> None:
        self.assertIn("mod.py", _run(ListDirTool(**self._kw()).execute(path="pkg/")))

    def test_a_backslash_separator_is_accepted(self) -> None:
        out = _run(ReadFileTool(**self._kw()).execute(path="pkg\\mod.py"))
        self.assertIn("x = 1", out)

    def test_a_doubled_separator_is_accepted(self) -> None:
        out = _run(ReadFileTool(**self._kw()).execute(path="pkg//mod.py"))
        self.assertIn("x = 1", out)

    def test_a_dot_segment_in_the_middle_is_accepted(self) -> None:
        out = _run(ReadFileTool(**self._kw()).execute(path="pkg/./mod.py"))
        self.assertIn("x = 1", out)


class NonUtf8Test(unittest.TestCase):
    """Real repositories contain latin-1 files and files with a BOM."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()
        self.states = FileStates()

    def _kw(self) -> dict:
        return {
            "workspace": self.root,
            "allowed_dir": self.root,
            "file_states": self.states,
        }

    def test_a_latin1_file_does_not_crash_the_reader(self) -> None:
        (self.root / "legacy.txt").write_bytes("caf\xe9 na\xefve\n".encode("latin-1"))
        out = _run(ReadFileTool(**self._kw()).execute(path="legacy.txt"))
        self.assertNotIn("Traceback", out)
        self.assertNotIn("UnicodeDecodeError", out)

    def test_a_bom_does_not_leak_into_the_first_line(self) -> None:
        (self.root / "bom.py").write_bytes(b"\xef\xbb\xbfimport os\n")
        out = _run(ReadFileTool(**self._kw()).execute(path="bom.py"))
        self.assertIn("import os", out)
        self.assertNotIn("\ufeff", out)

    def test_grep_skips_binary_without_crashing(self) -> None:
        (self.root / "blob.bin").write_bytes(bytes(range(256)) * 10)
        (self.root / "real.py").write_text("needle = 1\n", encoding="utf-8")
        out = _run(GrepTool(**self._kw()).execute(pattern="needle"))
        self.assertIn("real.py", out)
        self.assertNotIn("Traceback", out)

    def test_a_blob_that_decodes_as_utf8_is_still_binary(self) -> None:
        """NUL is a legal UTF-8 codepoint, so decoding cleanly proves nothing.

        A blob of mostly-ASCII bytes with embedded NULs - a .mo catalogue, a
        packed index, a small executable - would otherwise be handed to the model
        as source text.
        """
        (self.root / "packed.bin").write_bytes(b"index\x00entry\x00value\x00" * 4)
        out = _run(ReadFileTool(**self._kw()).execute(path="packed.bin"))
        self.assertIn("binary", out.lower())
        self.assertNotIn("\x00", out)

    def test_a_utf16_file_without_a_bom_is_read_as_text(self) -> None:
        """Read as UTF-8 it would come back riddled with NULs instead of content."""
        body = "value = 1  # a longer line so the encoding is recognised\n"
        (self.root / "wide.py").write_bytes(body.encode("utf-16-le"))
        out = _run(ReadFileTool(**self._kw()).execute(path="wide.py"))
        self.assertIn("value = 1", out)
        self.assertNotIn("\x00", out)

    def test_a_utf16_file_survives_an_edit(self) -> None:
        target = self.root / "wide.py"
        body = "value = old  # a longer line so the encoding is recognised\n"
        target.write_bytes(body.encode("utf-16-le"))
        out = _run(
            EditFileTool(**self._kw()).execute(
                path="wide.py", old_text="old", new_text="new"
            )
        )
        self.assertNotIn("Error", out)
        self.assertEqual(
            target.read_bytes(), body.replace("old", "new").encode("utf-16-le")
        )


class BackslashBoundaryTest(unittest.TestCase):
    """Reading backslashes as separators must not open a way out of the project.

    The convenience added for Windows-style paths runs inside the same helper
    that decides containment, so every traversal spelled with backslashes has to
    be refused exactly like its forward-slash twin.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._out = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(self._out.cleanup)
        self.root = Path(self._tmp.name).resolve()
        self.outside = Path(self._out.name).resolve()
        (self.outside / "secret.txt").write_text("TOPSECRET\n", encoding="utf-8")
        self.states = FileStates()

    def _kw(self) -> dict:
        return {
            "workspace": self.root,
            "allowed_dir": self.root,
            "file_states": self.states,
        }

    def test_backslash_traversal_is_refused(self) -> None:
        for attempt in (
            "..\\..\\secret.txt",
            "sub\\..\\..\\secret.txt",
            "..\\" * 12 + "secret.txt",
            str(self.outside / "secret.txt").replace("/", "\\"),
        ):
            with self.subTest(attempt=attempt):
                out = _run(ReadFileTool(**self._kw()).execute(path=attempt))
                self.assertNotIn("TOPSECRET", out)
                self.assertIn("outside allowed directory", out)

    def test_a_backslash_write_cannot_land_outside(self) -> None:
        out = _run(WriteFileTool(**self._kw()).execute(
            path="..\\..\\pwned.txt", content="x"
        ))
        self.assertIn("outside allowed directory", out)
        self.assertFalse((self.root.parent / "pwned.txt").exists())

    @unittest.skipIf(os.sep == "\\", "POSIX-only: Windows has no such filename")
    def test_a_file_really_named_with_a_backslash_still_wins(self) -> None:
        """The rewrite is a fallback, not a rule: a real name takes precedence."""
        target = self.root / "od\\dname.txt"
        target.write_text("literal\n", encoding="utf-8")
        (self.root / "od").mkdir()
        (self.root / "od" / "dname.txt").write_text("nested\n", encoding="utf-8")
        out = _run(ReadFileTool(**self._kw()).execute(path="od\\dname.txt"))
        self.assertIn("literal", out)


class EncodingRoundTripTest(unittest.TestCase):
    """Editing one line must not silently rewrite the rest of the file.

    A file stored in cp1252 or with a BOM belongs to some other tool in the
    user's chain: a Visual Studio project, an exported CSV, a legacy build
    script. Rewriting it as plain UTF-8 turns a one-line change into a
    whole-file diff, and on the BOM case can break the consumer outright.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()
        self.states = FileStates()

    def _kw(self) -> dict:
        return {
            "workspace": self.root,
            "allowed_dir": self.root,
            "file_states": self.states,
        }

    def _edit(self, rel: str, old: str, new: str) -> str:
        return _run(EditFileTool(**self._kw()).execute(path=rel, old_text=old, new_text=new))

    def test_a_cp1252_file_is_readable(self) -> None:
        (self.root / "legacy.txt").write_bytes("caf\xe9 na\xefve\n".encode("cp1252"))
        out = _run(ReadFileTool(**self._kw()).execute(path="legacy.txt"))
        self.assertIn("café", out)
        self.assertIn("naïve", out)

    def test_a_cp1252_file_stays_cp1252_after_an_edit(self) -> None:
        target = self.root / "legacy.txt"
        target.write_bytes("caf\xe9 ancien\n".encode("cp1252"))
        _run(ReadFileTool(**self._kw()).execute(path="legacy.txt"))
        self.assertNotIn("Error", self._edit("legacy.txt", "ancien", "nouveau"))
        self.assertEqual(target.read_bytes(), "caf\xe9 nouveau\n".encode("cp1252"))

    def test_a_bom_survives_an_edit(self) -> None:
        target = self.root / "bom.py"
        target.write_bytes(b"\xef\xbb\xbfvalue = 1\n")
        _run(ReadFileTool(**self._kw()).execute(path="bom.py"))
        self.assertNotIn("Error", self._edit("bom.py", "value = 1", "value = 2"))
        self.assertEqual(target.read_bytes(), b"\xef\xbb\xbfvalue = 2\n")

    def test_crlf_survives_an_edit(self) -> None:
        target = self.root / "win.txt"
        target.write_bytes(b"alpha\r\nbeta\r\n")
        _run(ReadFileTool(**self._kw()).execute(path="win.txt"))
        self.assertNotIn("Error", self._edit("win.txt", "beta", "gamma"))
        self.assertEqual(target.read_bytes(), b"alpha\r\ngamma\r\n")

    def test_a_utf16_file_is_readable_and_stays_utf16(self) -> None:
        target = self.root / "wide.txt"
        target.write_bytes("hello world\n".encode("utf-16"))
        out = _run(ReadFileTool(**self._kw()).execute(path="wide.txt"))
        self.assertIn("hello world", out)
        self.assertNotIn("Error", self._edit("wide.txt", "world", "there"))
        self.assertEqual(target.read_bytes().decode("utf-16"), "hello there\n")

    def test_apply_patch_preserves_the_encoding_too(self) -> None:
        target = self.root / "legacy.py"
        target.write_bytes("# caf\xe9\nvalue = 1\n".encode("cp1252"))
        _run(ReadFileTool(**self._kw()).execute(path="legacy.py"))
        out = _run(ApplyPatchTool(workspace=self.root, file_states=self.states).execute(
            edits=[{
                "path": "legacy.py", "action": "replace",
                "old_text": "value = 1", "new_text": "value = 2",
            }]
        ))
        self.assertNotIn("Error:", out)
        self.assertEqual(target.read_bytes(), "# caf\xe9\nvalue = 2\n".encode("cp1252"))

    def test_a_real_binary_file_is_still_refused(self) -> None:
        """Widening the decoder must not turn an object file into garbage text."""
        (self.root / "obj.o").write_bytes(b"\x7fELF\x02\x01\x01" + bytes(200) + b"\xc3\xf5")
        out = _run(ReadFileTool(**self._kw()).execute(path="obj.o"))
        self.assertIn("Error", out)
        self.assertIn("binary", out.lower())

    def test_new_characters_beyond_the_old_codec_fall_back_to_utf8(self) -> None:
        """Correctness beats fidelity: never drop a character to keep a codec."""
        target = self.root / "legacy.txt"
        target.write_bytes("caf\xe9 ancien\n".encode("cp1252"))
        _run(ReadFileTool(**self._kw()).execute(path="legacy.txt"))
        self.assertNotIn("Error", self._edit("legacy.txt", "ancien", "日本語"))
        self.assertIn("日本語", target.read_bytes().decode("utf-8"))


class SymlinkTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()
        self.states = FileStates()

    def _kw(self) -> dict:
        return {
            "workspace": self.root,
            "allowed_dir": self.root,
            "file_states": self.states,
        }

    def test_a_symlink_inside_the_project_is_followed(self) -> None:
        (self.root / "real").mkdir()
        (self.root / "real" / "f.txt").write_text("linked\n", encoding="utf-8")
        try:
            (self.root / "alias").symlink_to(self.root / "real", target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks need elevated privileges on this platform")
        out = _run(ReadFileTool(**self._kw()).execute(path="alias/f.txt"))
        self.assertIn("linked", out)


if __name__ == "__main__":
    unittest.main()
