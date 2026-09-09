# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""What the agent is allowed to put in front of the user.

The panel this tool drives only renders files inside the project, so a tool
that accepted anything on disk could report a preview it was impossible to
show: the agent says "opened", the panel stays empty, and nothing anywhere
says why. These tests pin the refusal and the wording that replaces it.
"""

from __future__ import annotations

import asyncio
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from navin.agent.tools.open_file_preview import OpenFilePreviewTool


class _Queue:
    def __init__(self) -> None:
        self.items: list = []

    def put_nowait(self, item) -> None:
        self.items.append(item)


class _Bus:
    def __init__(self) -> None:
        self.outbound = _Queue()


def _run(tool: OpenFilePreviewTool, path: str, workspace: Path | None = None):
    context = SimpleNamespace(channel="websocket", chat_id="c1", workspace=workspace)
    with patch(
        "navin.agent.tools.open_file_preview.current_request_context",
        return_value=context,
    ):
        return asyncio.run(tool.execute(path=path))


def _text(result) -> str:
    return getattr(result, "content", None) or str(result)


class OpenFilePreviewTest(unittest.TestCase):
    def test_opens_a_file_inside_the_project(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "project"
            (root / "reports").mkdir(parents=True)
            target = root / "reports" / "report.html"
            target.write_text("<p>hi</p>", encoding="utf-8")
            bus = _Bus()
            tool = OpenFilePreviewTool(bus=bus, working_dir=str(root))
            result = _run(tool, "reports/report.html")

        self.assertEqual(len(bus.outbound.items), 1)
        self.assertIn("report.html", _text(result))

    def test_refuses_a_file_above_the_project_when_restricted(self):
        """The '../' the agent reaches for when it guesses where it wrote."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "project"
            root.mkdir(parents=True)
            outside = Path(tmp) / "previews"
            outside.mkdir()
            (outside / "slide.png").write_bytes(b"\x89PNG\r\n\x1a\n")
            bus = _Bus()
            tool = OpenFilePreviewTool(bus=bus, working_dir=str(root))
            with patch(
                "navin.agent.tools.open_file_preview._workspace_restricted",
                return_value=True,
            ):
                result = _run(tool, "../previews/slide.png")

        self.assertEqual(bus.outbound.items, [], "nothing should have been sent")
        message = _text(result)
        self.assertIn("outside the project", message)
        # The way out has to be in the message, or the agent just tries again.
        self.assertIn("copy the file into the", message.lower())

    def test_allows_it_when_the_workspace_restriction_is_off(self):
        """The panel accepts it in that mode, so the tool must not be stricter."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "project"
            root.mkdir(parents=True)
            outside = Path(tmp) / "previews"
            outside.mkdir()
            (outside / "slide.png").write_bytes(b"\x89PNG\r\n\x1a\n")
            bus = _Bus()
            tool = OpenFilePreviewTool(bus=bus, working_dir=str(root))
            with patch(
                "navin.agent.tools.open_file_preview._workspace_restricted",
                return_value=False,
            ):
                _run(tool, "../previews/slide.png")

        self.assertEqual(len(bus.outbound.items), 1)

    def test_the_turn_workspace_beats_the_startup_working_dir(self):
        """A chat on another project must resolve relative paths there."""
        with TemporaryDirectory() as tmp:
            default = Path(tmp) / "default-workspace"
            default.mkdir(parents=True)
            project = Path(tmp) / "project"
            (project / "reports").mkdir(parents=True)
            (project / "reports" / "report.html").write_text("<p>hi</p>", encoding="utf-8")
            bus = _Bus()
            tool = OpenFilePreviewTool(bus=bus, working_dir=str(default))
            result = _run(tool, "reports/report.html", workspace=project)

        self.assertEqual(len(bus.outbound.items), 1)
        self.assertIn("report.html", _text(result))

    def test_a_missing_file_says_where_it_looked(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "project"
            root.mkdir(parents=True)
            tool = OpenFilePreviewTool(bus=_Bus(), working_dir=str(root))
            message = _text(_run(tool, "previews/nope.png"))

        self.assertIn("not found", message)
        # Without the resolved path the agent cannot tell a typo from a wrong
        # working directory, and guesses again.
        self.assertIn(str(root), message)


if __name__ == "__main__":
    unittest.main()
