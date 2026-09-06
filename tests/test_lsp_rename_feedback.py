"""An applied rename is a write, and writes report what they broke.

write_file, edit_file and apply_patch all attach the linters' verdict to their
result; a rename with apply=true rewrites several files at once and used to be
the one write that reported nothing. These tests pin the feedback to the tool
result, and cover the path handling the lsp tool shares with lint: the absolute
spelling of a workspace file must reach the server project-relative instead of
being reported as missing.
"""

from __future__ import annotations

import asyncio
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from navin.agent.tools.lsp import LspTool
from navin.lsp.manager import LspManager


def _run(coro):
    return asyncio.run(coro)


def _edit(line: int, col: int, end_col: int) -> dict:
    return {
        "line": line,
        "col": col,
        "end_line": line,
        "end_col": end_col,
        "new_text": "new_name",
    }


class _ToolTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()
        self.tool = LspTool(workspace=str(self.root), restrict_to_workspace=True)


class RenameFeedbackTest(_ToolTest):
    def setUp(self) -> None:
        super().setUp()
        (self.root / "lib.py").write_text(
            "def old_name(value):\n    return value\n", encoding="utf-8"
        )
        (self.root / "app.py").write_text(
            "from lib import old_name\n\nprint(old_name(1))\n", encoding="utf-8"
        )
        # A complete answer, so the index guard stays silent and the apply runs.
        edits = {"lib.py": [_edit(1, 5, 13)], "app.py": [_edit(3, 7, 15)]}
        for name, replacement in (
            ("rename", lambda *a, **k: edits),
            ("installed_servers_for", lambda *a, **k: ["pyright"]),
        ):
            patcher = mock.patch.object(LspManager, name, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _apply(self) -> str:
        return _run(
            self.tool.execute(
                action="rename", path="lib.py", line=1, col=5,
                new_name="new_name", apply=True,
            )
        )

    def test_a_rename_that_breaks_something_reports_it_on_the_result(self) -> None:
        report = "Diagnostics for the files just written:\n  app.py:1:1 error boom"
        with mock.patch(
            "navin.agent.tools.edit_feedback.diagnostics_after_write",
            return_value=report,
        ) as feedback:
            out = self._apply()
        self.assertIn("Applied 2 edit(s)", out)
        self.assertIn("Diagnostics for the files just written", out)
        written, kwargs = feedback.call_args[0][0], feedback.call_args[1]
        self.assertEqual(
            sorted(path.name for path in written), ["app.py", "lib.py"]
        )
        self.assertEqual(kwargs["workspace"], self.root)

    def test_a_clean_rename_reads_exactly_as_before(self) -> None:
        with mock.patch(
            "navin.agent.tools.edit_feedback.diagnostics_after_write",
            return_value="",
        ):
            out = self._apply()
        self.assertIn("Applied 2 edit(s)", out)
        self.assertNotIn("Diagnostics", out)
        self.assertFalse(out.endswith("\n\n"))

    def test_the_lint_after_edit_switch_also_covers_renames(self) -> None:
        self.tool._lint_after_edit = False
        with mock.patch(
            "navin.agent.tools.edit_feedback.diagnostics_after_write"
        ) as feedback:
            out = self._apply()
        self.assertIn("Applied 2 edit(s)", out)
        feedback.assert_not_called()

    def test_a_refused_apply_runs_no_diagnostics(self) -> None:
        """Nothing was written, so there is nothing to lint."""
        (self.root / "app.py").unlink()  # index no longer sees a second user
        (self.root / "lib.py").write_text("x = 1\n", encoding="utf-8")  # stale edits
        with mock.patch(
            "navin.agent.tools.edit_feedback.diagnostics_after_write"
        ) as feedback:
            out = self._apply()
        self.assertIn("Refused", out)
        self.assertNotIn("Applied", out)
        feedback.assert_not_called()


class LspAbsolutePathTest(_ToolTest):
    def test_an_absolute_workspace_path_reaches_the_server_relativized(self) -> None:
        target = self.root / "pkg" / "mod.py"
        target.parent.mkdir(parents=True)
        target.write_text("x = 1\n", encoding="utf-8")
        with mock.patch.object(LspTool, "_run", return_value="ok") as run:
            out = _run(self.tool.execute(action="diagnostics", path=str(target)))
        self.assertEqual(out, "ok")
        # _run(manager, root, action, rel, ...): the path the server sees.
        self.assertEqual(run.call_args[0][3], "pkg/mod.py")

    def test_an_absolute_path_outside_the_workspace_names_the_root(self) -> None:
        with TemporaryDirectory() as elsewhere:
            foreign = Path(elsewhere).resolve() / "mod.py"
            foreign.write_text("x = 1\n", encoding="utf-8")
            out = _run(self.tool.execute(action="diagnostics", path=str(foreign)))
        self.assertIn("outside the workspace root", out)
        self.assertIn(str(self.root), out)

    def test_a_root_anchored_relative_path_still_works(self) -> None:
        (self.root / "mod.py").write_text("x = 1\n", encoding="utf-8")
        with mock.patch.object(LspTool, "_run", return_value="ok") as run:
            out = _run(self.tool.execute(action="diagnostics", path="/mod.py"))
        self.assertEqual(out, "ok")
        self.assertEqual(run.call_args[0][3], "mod.py")


if __name__ == "__main__":
    unittest.main()
