"""Phase 2: agent context pack (open files + git dirty + symbols)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from navin.agent.context_pack import (
    OPEN_FILES_METADATA_KEY,
    build_context_pack_lines,
)
from navin.utils.file_mentions import FILE_MENTION_METADATA_KEY


class AgentContextPackTest(unittest.TestCase):
    def test_packs_open_files_and_symbols(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "svc.py").write_text(
                "def run():\n    return 1\n\nclass Service:\n    pass\n",
                encoding="utf-8",
            )
            from navin.index import get_index

            get_index(root).ensure()
            lines = build_context_pack_lines(
                workspace=root,
                metadata={OPEN_FILES_METADATA_KEY: [str(root / "svc.py")]},
            )
            joined = "\n".join(lines)
            self.assertIn("Open/attached:", joined)
            self.assertIn("svc.py", joined)
            self.assertIn("symbols:", joined)
            self.assertTrue("run" in joined or "Service" in joined)

    def test_includes_file_mentions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.py").write_text("X = 1\n", encoding="utf-8")
            lines = build_context_pack_lines(
                workspace=root,
                metadata={
                    FILE_MENTION_METADATA_KEY: [{"path": "a.py", "kind": "file"}],
                },
            )
            joined = "\n".join(lines)
            self.assertIn("a.py", joined)

    def test_empty_without_signals(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "alone.py").write_text("pass\n", encoding="utf-8")
            lines = build_context_pack_lines(workspace=root, metadata={})
            self.assertEqual(lines, [])

    def test_dirty_git_without_open_files_stays_empty(self) -> None:
        """A greeting must not pay git status + lint before the first token."""
        import subprocess

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(
                ["git", "init"],
                cwd=root,
                check=True,
                capture_output=True,
            )
            (root / "dirty.py").write_text("x = 1\n", encoding="utf-8")
            lines = build_context_pack_lines(workspace=root, metadata={})
            self.assertEqual(lines, [])
            joined = "\n".join(lines)
            self.assertNotIn("dirty.py", joined)
            self.assertNotIn("Git dirty", joined)

    def test_twelve_open_files_all_fit_in_the_token_budget(self) -> None:
        """P2-5 acceptance: truncation is by tokens, not an arbitrary count of 6."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            names = [f"mod_{index:02d}.py" for index in range(12)]
            for name in names:
                (root / name).write_text("X = 1\n", encoding="utf-8")
            lines = build_context_pack_lines(
                workspace=root,
                metadata={OPEN_FILES_METADATA_KEY: [str(root / n) for n in names]},
            )
            joined = "\n".join(lines)
            for name in names:
                self.assertIn(name, joined)
            self.assertNotIn("over the token budget", joined)

    def test_a_tiny_budget_truncates_and_says_so(self) -> None:
        """When the budget really is spent, the pack says what it dropped."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            names = [
                f"deeply/nested/package/path/module_with_a_long_name_{index:02d}.py"
                for index in range(12)
            ]
            for name in names:
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("X = 1\n", encoding="utf-8")
            lines = build_context_pack_lines(
                workspace=root,
                metadata={OPEN_FILES_METADATA_KEY: [str(root / n) for n in names]},
                token_budget=60,
            )
            joined = "\n".join(lines)
            self.assertIn("over the token budget", joined)
            # The first focus file is always kept, whatever the budget.
            self.assertIn("module_with_a_long_name_00.py", joined)

    def test_the_budget_bounds_the_final_size(self) -> None:
        from navin.utils.helpers import estimate_text_tokens

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            names = [f"pkg/file_{index:03d}.py" for index in range(40)]
            for name in names:
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("X = 1\n", encoding="utf-8")
            budget = 300
            lines = build_context_pack_lines(
                workspace=root,
                metadata={OPEN_FILES_METADATA_KEY: [str(root / n) for n in names]},
                token_budget=budget,
            )
            # Small slack for the header and the truncation marker itself.
            self.assertLessEqual(
                estimate_text_tokens("\n".join(lines)), budget + 100
            )


if __name__ == "__main__":
    unittest.main()
