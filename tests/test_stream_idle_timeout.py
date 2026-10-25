# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Idle / ping floors that keep the first IDE turn alive.

The website (Chromium) answers websocket pongs instantly. The desktop WebView
often does not, and a 20s ping timeout dropped the socket mid-turn. Switching
models then "fixed" it because that reconnects. These floors exist so the first
message works without that ritual.
"""

from __future__ import annotations

import unittest
import unittest.mock

from navin.channels.websocket import effective_ping_timeout_s
from navin.providers.base import (
    DEFAULT_STREAM_IDLE_TIMEOUT_S,
    resolve_stream_idle_timeout_s,
)


class StreamIdleTimeoutTests(unittest.TestCase):
    def test_default_is_two_minutes(self) -> None:
        self.assertEqual(DEFAULT_STREAM_IDLE_TIMEOUT_S, 120.0)
        self.assertEqual(resolve_stream_idle_timeout_s(env_value=None), 120.0)
        self.assertEqual(resolve_stream_idle_timeout_s(env_value=""), 120.0)

    def test_env_can_raise_the_floor(self) -> None:
        self.assertEqual(resolve_stream_idle_timeout_s(env_value="180"), 180.0)

    def test_invalid_env_falls_back_to_two_minutes(self) -> None:
        self.assertEqual(resolve_stream_idle_timeout_s(env_value="nope"), 120.0)
        self.assertEqual(resolve_stream_idle_timeout_s(env_value="0"), 120.0)
        self.assertEqual(resolve_stream_idle_timeout_s(env_value="-5"), 120.0)


class StreamStallToleranceTests(unittest.TestCase):
    """A frozen engine loop must not cut a reply the server is still sending."""

    def test_a_chunk_that_arrived_during_a_loop_freeze_is_delivered(self) -> None:
        import asyncio
        import time

        from navin.providers.base import wait_stream_idle

        async def scenario(wait) -> str:
            loop = asyncio.get_running_loop()
            chunk: asyncio.Future[str] = loop.create_future()

            def freeze_then_deliver() -> None:
                time.sleep(0.55)  # the loop is frozen well past the 0.2 s window
                loop.call_soon(chunk.set_result, "token")  # socket read on wake-up

            loop.call_later(0.05, freeze_then_deliver)
            return await wait(chunk, 0.2)

        # The old wait: the late timer wins over the chunk already received.
        with self.assertRaises(asyncio.TimeoutError):
            asyncio.run(scenario(lambda fut, timeout: asyncio.wait_for(fut, timeout=timeout)))
        self.assertEqual(asyncio.run(scenario(wait_stream_idle)), "token")

    def test_a_late_chunk_after_a_freeze_gets_a_fresh_window(self) -> None:
        import asyncio
        import time

        from navin.providers.base import wait_stream_idle

        async def scenario() -> str:
            loop = asyncio.get_running_loop()
            chunk: asyncio.Future[str] = loop.create_future()
            loop.call_later(0.01, lambda: time.sleep(0.5))  # freeze across the timer
            loop.call_later(0.6, lambda: chunk.set_result("late but alive"))
            return await wait_stream_idle(chunk, 0.2)

        with unittest.mock.patch("navin.providers.base.STREAM_STALL_GRACE_S", 0.1):
            self.assertEqual(asyncio.run(scenario()), "late but alive")

    def test_a_silent_server_still_times_out_and_the_read_is_cancelled(self) -> None:
        import asyncio

        from navin.providers.base import wait_stream_idle

        async def scenario() -> bool:
            never: asyncio.Future[str] = asyncio.get_running_loop().create_future()
            with self.assertRaises(asyncio.TimeoutError):
                await wait_stream_idle(never, 0.05)
            return never.cancelled()

        self.assertTrue(asyncio.run(scenario()))

    def test_end_of_stream_and_no_timeout_pass_through(self) -> None:
        import asyncio

        from navin.providers.base import wait_stream_idle

        async def empty():
            if False:
                yield None

        async def scenario() -> str:
            with self.assertRaises(StopAsyncIteration):
                await wait_stream_idle(empty().__anext__(), 1.0)

            async def value() -> str:
                return "ok"

            return await wait_stream_idle(value(), None)

        self.assertEqual(asyncio.run(scenario()), "ok")


class WebSocketPingTimeoutTests(unittest.TestCase):
    def test_stale_twenty_second_config_is_raised_to_two_minutes(self) -> None:
        self.assertEqual(effective_ping_timeout_s(20.0), 120.0)
        self.assertEqual(effective_ping_timeout_s(5.0), 120.0)

    def test_an_explicit_longer_timeout_is_kept(self) -> None:
        self.assertEqual(effective_ping_timeout_s(180.0), 180.0)
        self.assertEqual(effective_ping_timeout_s(120.0), 120.0)


if __name__ == "__main__":
    unittest.main()
