# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""The checkpoint event hop from the loop to a WebUI chat.

The loop no longer announces its automatic restore point at every message: the
divider it drew in the chat was noise, and the restore points stay in Code >
Checkpoints and /checkpoint. These tests pin the coordinator hop and the event
shape for whatever still publishes the event.
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


def test_checkpoint_streamed_write_is_the_same_json_as_json_dumps(tmp_path):
    import json

    from navin.agent.checkpoints import CheckpointStore

    payload = {
        "name": "n", "messages": [{"role": "user", "content": "é \u2603 \"q\""}, {"role": "assistant", "content": ""}],
        "files": {"a.py": "QUJD", "é.py": None}, "empty": [], "note": "x", "count": 2, "flag": True,
    }
    target = tmp_path / "c.json"
    CheckpointStore._write(target, payload)
    assert target.read_text(encoding="utf-8") == json.dumps(payload, ensure_ascii=False)


def test_a_turn_announces_no_restore_point_and_copies_the_project_only_on_an_edit(tmp_path, monkeypatch):
    from navin.agent import checkpoints
    from navin.agent.loop import AgentLoop
    from navin.bus.queue import MessageBus
    from navin.bus.runtime_events import ensure_runtime_event_publisher
    from navin.config.loader import get_config_path, set_config_path
    from navin.providers.base import LLMProvider, LLMResponse
    from navin.webui import checkpoints_api

    hooks_seen: list[object] = []

    class AnswerOnly(LLMProvider):
        def get_default_model(self):
            return "test-checkpoint"

        async def chat(self, **kwargs):
            hooks_seen.append(checkpoints._before_edit_hook.get())
            return LLMResponse(content="Nothing to change.")

        async def chat_with_retry(self, **kwargs):
            return await self.chat(**kwargs)

    copies: list[str] = []
    monkeypatch.setattr(checkpoints_api, "create_checkpoint", lambda root, **kw: copies.append(str(root)))
    old_config = get_config_path()
    set_config_path(tmp_path / "config.json")
    try:
        with mock.patch("navin.agent.skills._home_skill_dirs", return_value=[]):
            async def turn():
                loop = AgentLoop(provider=AnswerOnly(), workspace=tmp_path, bus=MessageBus())
                loop._mcp_servers = {}
                announced: list[CheckpointSaved] = []
                ensure_runtime_event_publisher(loop).bus.subscribe(announced.append, CheckpointSaved)
                try:
                    reply = await asyncio.wait_for(loop.process_direct("Explain the parser"), 30)
                    await asyncio.sleep(0)
                    return loop, reply, announced
                finally:
                    await loop.close_mcp()

            loop, reply, announced = asyncio.run(turn())
    finally:
        set_config_path(old_config)
    assert reply.content == "Nothing to change."
    assert announced == []  # no divider in the chat at every message
    assert copies == []  # a turn that only answers never copies the project
    # The turn could still snapshot before an edit, and /checkpoint keeps the chat restore point.
    assert hooks_seen and all(callable(h) for h in hooks_seen)
    assert checkpoints._before_edit_hook.get() is None
    assert [c["auto"] for c in loop.checkpoints.list("cli:direct")] == [True]
