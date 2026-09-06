"""``runtime_model_updated`` must say which chat picked the model.

The frame reaches every open connection, so an untagged one made all threads
repaint their badge with whatever session had spoken last, and a background
budget swap could repin the chat the user was reading.
"""

from __future__ import annotations

import json
import unittest
from typing import Any

from navin.bus.events import OutboundMessage
from navin.bus.outbound_events import RuntimeModelUpdatedEvent


class RuntimeModelFrameTest(unittest.IsolatedAsyncioTestCase):
    def _channel(self):
        from navin.channels.websocket import WebSocketChannel

        channel = WebSocketChannel.__new__(WebSocketChannel)
        sent: list[str] = []

        async def _safe_send_to(_connection: Any, raw: str, label: str = "") -> None:
            sent.append(raw)

        channel._safe_send_to = _safe_send_to  # type: ignore[method-assign]
        channel._conn_chats = {"conn-a": set()}  # type: ignore[attr-defined]
        channel._subs = {"chat-1": ["conn-a"]}  # type: ignore[attr-defined]
        return channel, sent

    async def test_frame_names_the_chat_that_selected_the_model(self):
        channel, sent = self._channel()
        await channel.send(
            OutboundMessage(
                channel="webui",
                chat_id="chat-2",
                content="",
                event=RuntimeModelUpdatedEvent(
                    model="gpt-5.6-terra",
                    model_preset="codex",
                    reason="budget",
                    previous_model="ox-alpha",
                    used_percent=80,
                ),
            )
        )
        body = json.loads(sent[0])
        self.assertEqual(body["event"], "runtime_model_updated")
        self.assertEqual(body["chat_id"], "chat-2")
        self.assertEqual(body["model_name"], "gpt-5.6-terra")
        self.assertEqual(body["model_preset"], "codex")
        self.assertEqual(body["reason"], "budget")

    async def test_chat_id_is_omitted_when_unknown(self):
        channel, sent = self._channel()
        await channel.send_runtime_model_updated(model_name="ox-alpha")
        body = json.loads(sent[0])
        self.assertNotIn("chat_id", body)
        self.assertEqual(body["model_name"], "ox-alpha")


if __name__ == "__main__":
    unittest.main()
