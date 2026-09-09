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


class WebSocketPingTimeoutTests(unittest.TestCase):
    def test_stale_twenty_second_config_is_raised_to_two_minutes(self) -> None:
        self.assertEqual(effective_ping_timeout_s(20.0), 120.0)
        self.assertEqual(effective_ping_timeout_s(5.0), 120.0)

    def test_an_explicit_longer_timeout_is_kept(self) -> None:
        self.assertEqual(effective_ping_timeout_s(180.0), 180.0)
        self.assertEqual(effective_ping_timeout_s(120.0), 120.0)


if __name__ == "__main__":
    unittest.main()
