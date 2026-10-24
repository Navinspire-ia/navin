# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""A prompt shows in every other window on the chat, not only the sender's."""

from __future__ import annotations

import asyncio
import json
import unittest
from typing import Any

from navin.channels.websocket import WebSocketChannel


class _Channel:
    _broadcast_user_message = WebSocketChannel._broadcast_user_message

    def __init__(self, subs: dict[str, set[Any]]) -> None:
        self._subs = subs
        self.sent: list[tuple[Any, dict[str, Any]]] = []

    def _identity_for(self, connection: Any) -> dict[str, str]:
        return {"display_name": "Ana" if connection == "guest" else "Host"}

    async def _safe_send_to(self, peer: Any, raw: str, *, label: str = "") -> None:
        self.sent.append((peer, json.loads(raw)))


class UserMessageBroadcastTest(unittest.TestCase):
    def test_every_other_window_gets_the_prompt(self) -> None:
        channel = _Channel({"c1": {"guest", "host-tab", "other-guest"}})
        asyncio.run(channel._broadcast_user_message("guest", "c1", "salut", {"turn_id": "t1"}))
        self.assertEqual(sorted(peer for peer, _ in channel.sent), ["host-tab", "other-guest"])
        payload = channel.sent[0][1]
        self.assertEqual(payload, {
            "event": "user_message", "chat_id": "c1", "content": "salut", "turn_id": "t1", "author": "Ana",
        })

    def test_alone_on_the_chat_sends_nothing(self) -> None:
        channel = _Channel({"c1": {"host-tab"}})
        asyncio.run(channel._broadcast_user_message("host-tab", "c1", "hi", {}))
        self.assertEqual(channel.sent, [])


if __name__ == "__main__":
    unittest.main()
