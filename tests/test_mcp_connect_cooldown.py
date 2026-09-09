# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""A dead MCP server in the config used to tax every single turn.

``connect_missing_servers`` runs before the turn's first token. When a
configured server is unreachable (a debug endpoint long gone, a machine that
is off), every message paid the connect probe - a few seconds each, forever.
The failure log gives each dead server one attempt per cooldown window; a
server that connects again drops out of the log immediately.
"""

from __future__ import annotations

import asyncio
import unittest
from unittest import mock

from navin.agent.tools.mcp import connect_missing_servers


def _run(coro):
    return asyncio.run(coro)


class _State:
    """The handful of attributes the connector reads; weakref-able for its lock."""

    def __init__(self) -> None:
        self._mcp_servers = {"dead": object()}
        self._mcp_stacks: dict[str, object] = {}
        self._mcp_connecting = False
        self._mcp_closing = False


def _state() -> _State:
    return _State()


class ConnectCooldownTest(unittest.TestCase):
    def test_a_failed_server_is_not_probed_again_on_the_next_message(self) -> None:
        state = _state()
        registry = mock.Mock()
        connect = mock.AsyncMock(return_value={})
        with mock.patch("navin.agent.tools.mcp.connect_mcp_servers", connect):
            _run(connect_missing_servers(state, registry))
            _run(connect_missing_servers(state, registry))
        self.assertEqual(connect.await_count, 1)

    def test_the_cooldown_expiring_allows_a_new_attempt(self) -> None:
        state = _state()
        registry = mock.Mock()
        connect = mock.AsyncMock(return_value={})
        with (
            mock.patch("navin.agent.tools.mcp.connect_mcp_servers", connect),
            mock.patch("navin.agent.tools.mcp._FAILED_CONNECT_COOLDOWN_S", 0.0),
        ):
            _run(connect_missing_servers(state, registry))
            _run(connect_missing_servers(state, registry))
        self.assertEqual(connect.await_count, 2)

    def test_a_successful_connect_clears_the_failure(self) -> None:
        state = _state()
        registry = mock.Mock()
        alive = mock.AsyncMock()
        with (
            mock.patch(
                "navin.agent.tools.mcp.connect_mcp_servers",
                mock.AsyncMock(return_value={"dead": alive}),
            ),
            mock.patch("navin.agent.tools.mcp._attach_reconnect_handlers"),
        ):
            state._mcp_failed_at = {"dead": 0.0}
            with mock.patch("navin.agent.tools.mcp._FAILED_CONNECT_COOLDOWN_S", 0.0):
                _run(connect_missing_servers(state, registry))
        self.assertEqual(state._mcp_failed_at, {})
        self.assertIn("dead", state._mcp_stacks)

    def test_a_connected_server_is_never_reprobed(self) -> None:
        state = _state()
        state._mcp_stacks = {"dead": object()}
        registry = mock.Mock()
        connect = mock.AsyncMock(return_value={})
        with mock.patch("navin.agent.tools.mcp.connect_mcp_servers", connect):
            _run(connect_missing_servers(state, registry))
        self.assertEqual(connect.await_count, 0)


if __name__ == "__main__":
    unittest.main()
