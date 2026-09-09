# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Montage setup stream helpers (live install console feed)."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from navin.montage.setup_stream import MontageSetupStream, chat_id_from_session_key


class ChatIdFromSessionKeyTest(unittest.TestCase):
    def test_strips_websocket_prefix(self) -> None:
        self.assertEqual(
            chat_id_from_session_key("websocket:abc-123"),
            "abc-123",
        )

    def test_bare_id(self) -> None:
        self.assertEqual(chat_id_from_session_key("abc-123"), "abc-123")

    def test_empty(self) -> None:
        self.assertIsNone(chat_id_from_session_key(""))
        self.assertIsNone(chat_id_from_session_key(None))


class MontageSetupStreamTest(unittest.TestCase):
    def test_collects_ndjson_events(self) -> None:
        bus = SimpleNamespace(outbound=_FakeQueue())
        stream = MontageSetupStream(bus, "chat-1", command="navin montage setup --package ffmpeg")
        stream.log("Downloading…\n")
        stream.log("Installed: /tmp/ffmpeg\n")
        stream.finish(0)
        types = [row["type"] for row in stream.events]
        self.assertEqual(types, ["start", "log", "log", "done"])
        body = stream.ndjson_body().decode("utf-8")
        self.assertIn('"type": "start"', body)
        self.assertIn("navin montage setup --package ffmpeg", body)
        self.assertIn('"exit_code": 0', body)
        # start + 2 output + exit on the bus
        self.assertEqual(len(bus.outbound.items), 4)

    def test_broadcasts_when_chat_missing(self) -> None:
        bus = SimpleNamespace(outbound=_FakeQueue())
        stream = MontageSetupStream(bus, None, command="navin montage setup --package all")
        stream.log("hi\n")
        stream.finish(0)
        chat_ids = [getattr(item, "chat_id", None) for item in bus.outbound.items]
        self.assertTrue(chat_ids)
        self.assertTrue(all(cid == "*" for cid in chat_ids))


class _FakeQueue:
    def __init__(self) -> None:
        self.items: list[object] = []

    def put_nowait(self, item: object) -> None:
        self.items.append(item)


if __name__ == "__main__":
    unittest.main()
