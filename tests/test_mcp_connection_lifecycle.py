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

    def test_http_405_logs_one_actionable_line_without_secrets(self):
        from loguru import logger

        lines: list[str] = []
        sink = logger.add(lambda message: lines.append(str(message)), level="WARNING")

        async def action():
            result = await connect_mcp_servers(
                {
                    "debug": MCPServerConfig(
                        type="streamableHttp",
                        url="http://127.0.0.1:3001/secret-token/mcp?token=abc",
                    )
                },
                ToolRegistry(),
            )
            self.assertEqual(result, {})

        try:
            self.run_connection(lambda request: httpx.Response(405), action)
        finally:
            logger.remove(sink)
        hits = [line for line in lines if "answered 405" in line]
        self.assertEqual(len(hits), 1, lines)
        self.assertIn('"type": "sse"', hits[0])
        self.assertIn("http://127.0.0.1:3001/...", hits[0])
        self.assertNotIn("secret-token", hits[0])
        self.assertNotIn("abc", hits[0])
        self.assertFalse(any("connection closed" in line for line in lines), lines)

    def capture_logs(self, level="INFO"):
        from loguru import logger

        lines: list[str] = []
        sink = logger.add(lambda message: lines.append(str(message)), level=level)
        return lines, lambda: logger.remove(sink)

    @staticmethod
    def sse_only_handler():
        """A legacy SSE-only server: 405 to streamable HTTP POSTs, SSE stream on GET."""
        events: asyncio.Queue = asyncio.Queue()
        seen: list[tuple[str, str, str]] = []

        class Stream(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield b"event: endpoint\ndata: /messages?session=1\n\n"
                while True:
                    yield await events.get()

        async def handler(request):
            seen.append((request.method, request.url.path, request.headers.get("accept", "")))
            if request.url.path == "/mcp" and request.method == "POST":
                return httpx.Response(405, headers={"Allow": "GET"})
            if request.url.path == "/mcp" and request.method == "GET":
                return httpx.Response(
                    200, headers={"content-type": "text/event-stream"}, stream=Stream()
                )
            if request.url.path == "/messages" and request.method == "POST":
                message = json.loads(request.content)
                if "id" in message:
                    if message["method"] == "initialize":
                        result = {
                            "protocolVersion": "2024-11-05",
                            "capabilities": {},
                            "serverInfo": {"name": "legacy", "version": "1"},
                        }
                    else:
                        result = {"tools": []}
                    payload = json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": result})
                    await events.put(f"event: message\ndata: {payload}\n\n".encode())
                return httpx.Response(202)
            return httpx.Response(404)

        return handler, seen

    def test_inferred_streamable_http_405_falls_back_to_sse(self):
        handler, seen = self.sse_only_handler()
        lines, stop = self.capture_logs()

        async def action():
            connections = await connect_mcp_servers(
                {"legacy": MCPServerConfig(url="http://127.0.0.1:3001/mcp", headers={"X-Token": "t"})},
                ToolRegistry(),
            )
            self.assertEqual(set(connections), {"legacy"})

            class State:
                pass

            state = State()
            state._mcp_stacks = connections
            await asyncio.create_task(close_mcp_servers(state))
            self.assertEqual(state._mcp_stacks, {})

        try:
            # One client for the failed streamable attempt, one for the SSE session.
            self.run_connection(handler, action, expected_closes=2)
        finally:
            stop()
        self.assertIn(("POST", "/mcp", "application/json, text/event-stream"), seen)
        gets = [entry for entry in seen if entry[0] == "GET" and entry[1] == "/mcp"]
        self.assertEqual(len(gets), 1, seen)
        hits = [line for line in lines if "connected over SSE instead" in line]
        self.assertEqual(len(hits), 1, lines)
        self.assertIn('"type": "sse"', hits[0])
        self.assertFalse(any("WARNING" in line for line in lines), lines)
        self.assertTrue(any("connected, 0 capabilities registered" in line for line in lines), lines)

    def test_inferred_streamable_http_405_and_failed_sse_logs_one_warning(self):
        lines, stop = self.capture_logs(level="WARNING")

        async def action():
            result = await connect_mcp_servers(
                {"legacy": MCPServerConfig(url="http://127.0.0.1:3001/secret/mcp?token=abc")},
                ToolRegistry(),
            )
            self.assertEqual(result, {})

        try:
            self.run_connection(lambda request: httpx.Response(405), action, expected_closes=2)
        finally:
            stop()
        self.assertEqual(len(lines), 1, lines)
        self.assertIn("answered 405", lines[0])
        self.assertIn("either transport", lines[0])
        self.assertNotIn("secret", lines[0])
        self.assertNotIn("abc", lines[0])
        self.assertNotIn("Traceback", lines[0])

    def test_explicit_streamable_http_405_does_not_try_sse(self):
        seen: list[str] = []

        def handler(request):
            seen.append(request.method)
            return httpx.Response(405)

        lines, stop = self.capture_logs(level="WARNING")
        try:
            self.run_connection(handler, self.connect)
        finally:
            stop()
        self.assertNotIn("GET", seen, seen)
        self.assertEqual(len([line for line in lines if "answered 405" in line]), 1, lines)
        self.assertFalse(any("over SSE" in line for line in lines), lines)

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
