# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Exercise MCP transport failures and shutdown using the real SDK task groups."""

import asyncio
import json
import unittest
from unittest import mock

import httpx

from navin.agent.tools.mcp import close_mcp_servers, connect_mcp_servers
from navin.agent.tools.registry import ToolRegistry
from navin.config.schema import MCPServerConfig


class MCPConnectionLifecycleTest(unittest.TestCase):
    def run_connection(self, handler, action, expected_closes=1):
        errors = []

        async def run():
            loop = asyncio.get_running_loop()
            loop.set_exception_handler(lambda _loop, context: errors.append(context))
            transport = httpx.MockTransport(handler)
            transport.aclose = mock.AsyncMock(wraps=transport.aclose)
            with (
                mock.patch("navin.agent.tools.mcp._probe_http_url", return_value=True),
                mock.patch("navin.agent.tools.mcp.validate_url_target", return_value=(True, "")),
                mock.patch("navin.agent.tools.mcp._validate_mcp_request_url", new=mock.AsyncMock()),
                mock.patch("navin.agent.tools.mcp._pinned_transport_kwargs", return_value={"transport": transport}),
            ):
                await action()
            self.assertEqual(transport.aclose.await_count, expected_closes)
            self.assertFalse(any(task.get_name().startswith("mcp:") for task in asyncio.all_tasks()))
            await loop.shutdown_asyncgens()

        asyncio.run(run())
        self.assertEqual(errors, [], [error.get("message") for error in errors])

    async def connect(self):
        return await connect_mcp_servers(
            {"debug": MCPServerConfig(type="streamableHttp", url="http://127.0.0.1:3001/mcp")},
            ToolRegistry(),
        )

    def test_http_405_does_not_cancel_caller_or_leak_transport(self):
        async def action():
            self.assertEqual(await self.connect(), {})

        self.run_connection(lambda request: httpx.Response(405), action)

    def test_cancel_during_handshake_closes_transport_and_preserves_cancellation(self):
        started = asyncio.Event()

        async def handler(request):
            started.set()
            await asyncio.Event().wait()

        async def action():
            task = asyncio.create_task(self.connect())
            await asyncio.wait_for(started.wait(), timeout=2)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

        self.run_connection(handler, action)

    def test_failed_server_does_not_block_healthy_server_or_shared_shutdown(self):
        def handler(request):
            if request.url.path == "/rejected":
                return httpx.Response(405)
            if request.method != "POST":
                return httpx.Response(405)
            message = json.loads(request.content)
            if "id" not in message:
                return httpx.Response(202)
            if message["method"] == "initialize":
                result = {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "serverInfo": {"name": "test", "version": "1"},
                }
            else:
                result = {"tools": []}
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": message["id"], "result": result})

        async def action():
            connections = await connect_mcp_servers(
                {
                    "rejected": MCPServerConfig(type="streamableHttp", url="http://127.0.0.1:3001/rejected"),
                    "debug": MCPServerConfig(type="streamableHttp", url="http://127.0.0.1:3001/mcp"),
                },
                ToolRegistry(),
            )
            self.assertEqual(set(connections), {"debug"})
            # The shared shutdown lock needs a hashable, weak-referenceable owner.
            class State:
                pass

            state = State()
            state._mcp_stacks = connections
            await asyncio.create_task(close_mcp_servers(state))
            self.assertEqual(state._mcp_stacks, {})

        self.run_connection(handler, action, expected_closes=2)
