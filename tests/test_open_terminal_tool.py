# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for the open_terminal tool (navin.agent.tools.open_terminal).

The tool only forwards a request to the editor UI. What is pinned here is the
cwd contract: a relative folder resolves against the per-turn workspace (the
session's project), never against the gateway process cwd, and an absolute
folder passes through untouched.
"""

from __future__ import annotations

import asyncio
import unittest
from pathlib import Path
from types import SimpleNamespace

from navin.agent.tools.context import RequestContext, request_context
from navin.agent.tools.open_terminal import OpenTerminalTool
from navin.bus.outbound_events import (
    TerminalOpenRequestedEvent,
    outbound_event_from_message,
)


class _Bus:
    def __init__(self) -> None:
        self.sent: list = []
        self.outbound = SimpleNamespace(put_nowait=self.sent.append)


def _run(
    tool: OpenTerminalTool,
    *,
    channel: str = "websocket",
    workspace: Path | None = None,
    **kwargs,
):
    ctx = RequestContext(channel=channel, chat_id="chat-1", workspace=workspace)
    with request_context(ctx):
        return asyncio.run(tool.execute(**kwargs))


class OpenTerminalTest(unittest.TestCase):
    def setUp(self) -> None:
        self.bus = _Bus()
        self.tool = OpenTerminalTool(bus=self.bus)

    def _event(self) -> TerminalOpenRequestedEvent:
        self.assertEqual(len(self.bus.sent), 1)
        event = outbound_event_from_message(self.bus.sent[0])
        self.assertIsInstance(event, TerminalOpenRequestedEvent)
        return event

    def test_a_relative_cwd_resolves_against_the_turn_workspace(self) -> None:
        project = Path("/tmp/some-project")
        _run(self.tool, workspace=project, cwd="src/api")
        self.assertEqual(self._event().cwd, str(project / "src" / "api"))

    def test_an_absolute_cwd_passes_through(self) -> None:
        _run(self.tool, workspace=Path("/tmp/some-project"), cwd="/opt/elsewhere")
        self.assertEqual(self._event().cwd, "/opt/elsewhere")

    def test_no_cwd_stays_empty_so_the_ui_uses_the_project(self) -> None:
        _run(self.tool, workspace=Path("/tmp/some-project"))
        self.assertIsNone(self._event().cwd)

    def test_other_channels_are_refused(self) -> None:
        result = _run(self.tool, channel="telegram")
        self.assertIn("exec", str(result))
        self.assertEqual(self.bus.sent, [])


if __name__ == "__main__":
    unittest.main()
