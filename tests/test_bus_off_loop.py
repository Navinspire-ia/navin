# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Blocking work runs in a thread; its notifications still reach the bus safely."""

from __future__ import annotations

import asyncio
import threading

from navin.bus.notify import publish_notification, run_off_loop
from navin.bus.queue import MessageBus


def test_notification_from_a_worker_thread_reaches_the_loop() -> None:
    async def scenario():
        bus = MessageBus()
        loop_thread = threading.get_ident()
        seen = {}

        def work():
            seen["thread"] = threading.get_ident()
            return publish_notification(bus, title="Board updated")

        assert await run_off_loop(work) is True
        message = await asyncio.wait_for(bus.outbound.get(), timeout=2)
        assert seen["thread"] != loop_thread  # really ran off the loop
        return message

    message = asyncio.run(scenario())
    assert "Board updated" in str(message.event)


def test_on_the_loop_it_stays_a_direct_put() -> None:
    async def scenario():
        bus = MessageBus()
        publish_notification(bus, title="direct")
        return bus.outbound.qsize()

    assert asyncio.run(scenario()) == 1
