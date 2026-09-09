# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Mid-turn follow-ups keep one FIFO owner when their bounded queue fills."""

from __future__ import annotations

import asyncio
import unittest

from navin.agent.loop import enqueue_pending_followup
from navin.bus.events import InboundMessage


def _message(content: str) -> InboundMessage:
    return InboundMessage(
        channel="websocket",
        sender_id="user",
        chat_id="chat-1",
        content=content,
    )


class PendingFollowupBackpressureTest(unittest.IsolatedAsyncioTestCase):
    async def test_full_queue_waits_for_a_slot_without_losing_fifo_order(self) -> None:
        queue: asyncio.Queue[InboundMessage] = asyncio.Queue(maxsize=1)
        first = _message("first")
        second = _message("second")
        queue.put_nowait(first)

        waiting = asyncio.create_task(
            enqueue_pending_followup(
                queue,
                second,
                session_key="websocket:chat-1",
            )
        )
        await asyncio.sleep(0)
        self.assertFalse(waiting.done())
        self.assertIs(await queue.get(), first)

        await asyncio.wait_for(waiting, timeout=1)
        self.assertIs(await queue.get(), second)

    async def test_available_slot_completes_immediately(self) -> None:
        queue: asyncio.Queue[InboundMessage] = asyncio.Queue(maxsize=1)
        message = _message("ready")
        await enqueue_pending_followup(
            queue,
            message,
            session_key="websocket:chat-1",
        )
        self.assertIs(queue.get_nowait(), message)


if __name__ == "__main__":
    unittest.main()
