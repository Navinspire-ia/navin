# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for the open_in_editor tool (navin.agent.tools.open_in_editor).

The tool never opens anything itself: it validates the request and puts an
EditorOpenRequestedEvent on the outbound bus for the editor UI. What is
checked here is exactly that contract - path resolution against the project
root, file/folder detection, and the refusals (wrong channel, missing path,
nonexistent target).
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from navin.agent.tools.context import RequestContext, request_context
from navin.agent.tools.open_in_editor import OpenInEditorTool
from navin.bus.outbound_events import (
    EditorOpenRequestedEvent,
    outbound_event_from_message,
)


class _Bus:
    def __init__(self) -> None:
        self.sent: list = []
        self.outbound = SimpleNamespace(put_nowait=self.sent.append)


def _run(
    tool: OpenInEditorTool,
    *,
    channel: str = "websocket",
    workspace: Path | None = None,
    **kwargs,
):
    ctx = RequestContext(channel=channel, chat_id="chat-1", workspace=workspace)
    with request_context(ctx):
        return asyncio.run(tool.execute(**kwargs))


class OpenInEditorTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "src").mkdir()
        (self.root / "src" / "main.py").write_text("print('hi')\n")
        self.bus = _Bus()
        self.tool = OpenInEditorTool(bus=self.bus, working_dir=str(self.root))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _event(self) -> EditorOpenRequestedEvent:
        self.assertEqual(len(self.bus.sent), 1)
        event = outbound_event_from_message(self.bus.sent[0])
        self.assertIsInstance(event, EditorOpenRequestedEvent)
        return event

    def test_a_relative_file_resolves_against_the_project_root(self) -> None:
        result = _run(self.tool, path="src/main.py", line=3)
        self.assertNotIn("Error", str(result))
        event = self._event()
        self.assertEqual(event.path, str(self.root.resolve() / "src" / "main.py"))
        self.assertEqual(event.kind, "file")
        self.assertEqual(event.line, 3)

    def test_a_folder_is_reported_as_a_folder_and_drops_the_line(self) -> None:
        _run(self.tool, path="src", line=7)
        event = self._event()
        self.assertEqual(event.kind, "folder")
        self.assertIsNone(event.line)

    def test_a_missing_target_is_refused(self) -> None:
        result = _run(self.tool, path="does/not/exist.py")
        self.assertIn("not found", str(result))
        self.assertEqual(self.bus.sent, [])

    def test_a_missing_path_is_refused(self) -> None:
        result = _run(self.tool, path="  ")
        self.assertIn("Missing path", str(result))

    def test_other_channels_are_told_to_use_read_file(self) -> None:
        result = _run(self.tool, channel="telegram", path="src/main.py")
        self.assertIn("editor", str(result))
        self.assertEqual(self.bus.sent, [])

    def test_the_turn_workspace_beats_the_startup_working_dir(self) -> None:
        # The tool is built once with the gateway default workspace; a chat
        # working on another project must resolve relative paths there.
        with tempfile.TemporaryDirectory() as other:
            project = Path(other) / "project"
            (project / "app").mkdir(parents=True)
            (project / "app" / "run.py").write_text("pass\n")
            result = _run(self.tool, workspace=project, path="app/run.py")
            self.assertNotIn("Error", str(result))
            event = self._event()
            self.assertEqual(event.path, str(project.resolve() / "app" / "run.py"))


if __name__ == "__main__":
    unittest.main()
