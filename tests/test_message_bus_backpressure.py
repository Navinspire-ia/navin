"""The gateway message bus is bounded and applies lossless backpressure."""

from __future__ import annotations

import asyncio
import unittest

from navin.bus.events import InboundMessage, OutboundMessage
from navin.bus.queue import (
    DEFAULT_INBOUND_MAXSIZE,
    DEFAULT_OUTBOUND_MAXSIZE,
    MessageBus,
)


def _inbound(content: str) -> InboundMessage:
    return InboundMessage(
        channel="websocket",
        sender_id="user",
        chat_id="chat-1",
        content=content,
    )


def _outbound(content: str) -> OutboundMessage:
    return OutboundMessage(
        channel="websocket",
        chat_id="chat-1",
        content=content,
    )


class MessageBusBackpressureTest(unittest.IsolatedAsyncioTestCase):
    async def test_factory_queues_have_finite_capacities(self) -> None:
        bus = MessageBus()
        self.assertEqual(bus.inbound_capacity, DEFAULT_INBOUND_MAXSIZE)
        self.assertEqual(bus.outbound_capacity, DEFAULT_OUTBOUND_MAXSIZE)
        self.assertGreater(bus.inbound_capacity, 0)
        self.assertGreater(bus.outbound_capacity, 0)

    async def test_inbound_publish_waits_without_losing_fifo_order(self) -> None:
        bus = MessageBus(inbound_maxsize=1, outbound_maxsize=1)
        first = _inbound("first")
        second = _inbound("second")
        await bus.publish_inbound(first)

        waiting = asyncio.create_task(bus.publish_inbound(second))
        await asyncio.sleep(0)
        self.assertFalse(waiting.done())
        self.assertEqual(bus.inbound_backpressure_count, 1)
        self.assertIs(await bus.consume_inbound(), first)

        await asyncio.wait_for(waiting, timeout=1)
        self.assertIs(await bus.consume_inbound(), second)

    async def test_outbound_publish_waits_without_losing_fifo_order(self) -> None:
        bus = MessageBus(inbound_maxsize=1, outbound_maxsize=1)
        first = _outbound("first")
        second = _outbound("second")
        await bus.publish_outbound(first)

        waiting = asyncio.create_task(bus.publish_outbound(second))
        await asyncio.sleep(0)
        self.assertFalse(waiting.done())
        self.assertEqual(bus.outbound_backpressure_count, 1)
        self.assertIs(await bus.consume_outbound(), first)

        await asyncio.wait_for(waiting, timeout=1)
        self.assertIs(await bus.consume_outbound(), second)

    async def test_nowait_overload_is_explicit_not_unbounded(self) -> None:
        bus = MessageBus(inbound_maxsize=1, outbound_maxsize=1)
        bus.outbound.put_nowait(_outbound("first"))
        with self.assertRaises(asyncio.QueueFull):
            bus.outbound.put_nowait(_outbound("second"))
        self.assertEqual(bus.outbound_size, 1)

    async def test_negative_capacity_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            MessageBus(inbound_maxsize=-1)


if __name__ == "__main__":
    unittest.main()
