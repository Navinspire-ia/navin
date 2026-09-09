# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""A restore point must reach the chat transcript.

Auto checkpoints existed for a long time, but nothing told the chat about
them: the user had to know to open Code > Git > Checkpoints. The runtime event
travels loop -> WebuiTurnCoordinator -> outbound bus -> websocket, and the UI
renders a divider. These tests pin the coordinator hop and the event shape.
"""

from __future__ import annotations

import asyncio
import unittest
from unittest import mock

from navin.bus.outbound_events import CheckpointSavedEvent
from navin.bus.runtime_events import CheckpointSaved
from navin.session.webui_turns import WebuiTurnCoordinator


def _run(coro):
    return asyncio.run(coro)


def _coordinator_with_bus() -> tuple[WebuiTurnCoordinator, mock.AsyncMock]:
    coordinator = object.__new__(WebuiTurnCoordinator)
    bus = mock.Mock()
    bus.publish_outbound = mock.AsyncMock()
    coordinator.bus = bus
    return coordinator, bus.publish_outbound


class CheckpointSavedHopTest(unittest.TestCase):
    def test_a_websocket_session_gets_the_outbound_event(self) -> None:
        coordinator, publish = _coordinator_with_bus()
        _run(
            coordinator._handle_checkpoint_saved(
                CheckpointSaved(
                    session_key="websocket:chat-42",
                    name="auto-2026-08-22-190000",
                )
            )
        )
        self.assertEqual(publish.await_count, 1)
        outbound = publish.await_args.args[0]
        self.assertEqual(outbound.chat_id, "chat-42")
        event = outbound.event
        self.assertIsInstance(event, CheckpointSavedEvent)
        self.assertEqual(event.name, "auto-2026-08-22-190000")
        self.assertTrue(event.auto)

    def test_non_websocket_sessions_stay_silent(self) -> None:
        coordinator, publish = _coordinator_with_bus()
        for key in ("telegram:123", "", "unified:default"):
            _run(
                coordinator._handle_checkpoint_saved(
                    CheckpointSaved(session_key=key, name="auto-x")
                )
            )
        self.assertEqual(publish.await_count, 0)


if __name__ == "__main__":
    unittest.main()
