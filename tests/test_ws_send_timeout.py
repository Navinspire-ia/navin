# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""A stalled webui client must not stall the shared outbound dispatcher.

Regression for the "streaming stops then resumes" report: ``_safe_send_to``
used to await ``connection.send`` with no bound, so one slow client (frozen
renderer, saturated socket) blocked delta delivery to every chat and backed
up the outbound queue until the agent loop itself paused on publish.
"""

from __future__ import annotations

import asyncio
import time
import unittest
from typing import Any
from unittest.mock import MagicMock


class _StalledClient:
    """A websocket whose TCP buffer is full: send never completes."""

    def __init__(self) -> None:
        self.calls = 0

    async def send(self, _raw: str) -> None:
        self.calls += 1
        await asyncio.Event().wait()  # never resolves


class _FastClient:
    def __init__(self) -> None:
        self.frames: list[str] = []

    async def send(self, raw: str) -> None:
        self.frames.append(raw)


def _channel_with(timeout_s: float) -> tuple[Any, dict[Any, list[str]]]:
    from navin.channels.websocket import WebSocketChannel

    channel = WebSocketChannel.__new__(WebSocketChannel)
    channel._SEND_TIMEOUT_S = timeout_s  # type: ignore[attr-defined]
    channel._transcripts = MagicMock()
    channel._media = MagicMock()
    channel._media.rewrite_local_markdown_images = lambda text: text
    channel._stream_text_buffers = {}  # type: ignore[attr-defined]
    channel.logger = MagicMock()
    cleaned: list[Any] = []
    channel._cleanup_connection = cleaned.append  # type: ignore[method-assign]
    return channel, cleaned


class SendTimeoutTests(unittest.TestCase):
    def test_stalled_client_is_dropped_and_fast_client_still_receives(self) -> None:
        async def scenario() -> None:
            channel, cleaned = _channel_with(timeout_s=0.05)
            stalled = _StalledClient()
            fast = _FastClient()
            channel._subs = {"chat-1": [stalled, fast]}  # type: ignore[attr-defined]

            started = time.monotonic()
            await channel.send_delta("chat-1", "bonjour", stream_id="s1")
            elapsed = time.monotonic() - started

            # The dispatcher was not held hostage: bounded wait, not infinite.
            self.assertLess(elapsed, 1.0)
            # The stalled client was dropped instead of blocking forever.
            self.assertIn(stalled, cleaned)
            self.assertEqual(stalled.calls, 1)
            # The healthy client received its frame.
            self.assertEqual(len(fast.frames), 1)
            self.assertIn("bonjour", fast.frames[0])

        asyncio.run(scenario())

    def test_healthy_client_send_is_untouched(self) -> None:
        async def scenario() -> None:
            channel, cleaned = _channel_with(timeout_s=5.0)
            fast = _FastClient()
            channel._subs = {"chat-1": [fast]}  # type: ignore[attr-defined]

            await channel.send_delta("chat-1", "ok", stream_id="s1")

            self.assertEqual(cleaned, [])
            self.assertEqual(len(fast.frames), 1)

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
