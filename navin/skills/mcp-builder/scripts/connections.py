#!/usr/bin/env python3
"""Small MCP client helpers for stdio, streamable HTTP and SSE servers.

Used from the command line to inspect a server while building it, or imported
by evaluate.py. The `mcp` package is imported lazily so that --help and the
validation paths work on machines where it is not installed.

Examples:
    python3 connections.py list-tools --transport stdio --command python3 --arg server.py
    python3 connections.py call --transport http --url http://127.0.0.1:8000/mcp \
        --tool notes_search --args '{"query": "budget"}'
    python3 connections.py list-tools --transport sse --url https://host/sse \
        --header "Authorization: Bearer $TOKEN"
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from contextlib import AsyncExitStack
from typing import Any

MISSING_MCP_MESSAGE = (
    "The 'mcp' Python package is not installed. Install it with\n"
    "    python3 -m pip install mcp\n"
    "then run this command again."
)


class MissingDependencyError(RuntimeError):
    """Raised when the mcp package cannot be imported."""


def load_mcp() -> dict[str, Any]:
    """Import the mcp client pieces on demand and return them in a dict."""
    try:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.sse import sse_client
        from mcp.client.stdio import stdio_client
        from mcp.client.streamable_http import streamablehttp_client
    except ImportError as exc:  # pragma: no cover - depends on the machine
        raise MissingDependencyError(MISSING_MCP_MESSAGE) from exc
    return {
        "ClientSession": ClientSession,
        "StdioServerParameters": StdioServerParameters,
        "sse_client": sse_client,
        "stdio_client": stdio_client,
        "streamablehttp_client": streamablehttp_client,
    }


class Connection:
    """Async context manager wrapping one MCP client session.

    transport: "stdio", "http" (streamable HTTP) or "sse".
    """

    def __init__(
        self,
        transport: str,
        *,
        command: str | None = None,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
        url: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.transport = normalize_transport(transport)
        self.command = command
        self.args = list(args or [])
        self.env = env
        self.url = url
        self.headers = dict(headers or {})
        self.session: Any = None
        self._stack: AsyncExitStack | None = None
        if self.transport == "stdio" and not command:
            raise ValueError("stdio transport needs --command")
        if self.transport in ("http", "sse") and not url:
            raise ValueError(f"{self.transport} transport needs --url")

    def _open_transport(self, lib: dict[str, Any]):
        if self.transport == "stdio":
            params = lib["StdioServerParameters"](command=self.command, args=self.args, env=self.env)
            return lib["stdio_client"](params)
        if self.transport == "sse":
            return lib["sse_client"](url=self.url, headers=self.headers)
        return lib["streamablehttp_client"](url=self.url, headers=self.headers)

    async def __aenter__(self) -> Connection:
        lib = load_mcp()
        self._stack = AsyncExitStack()
        await self._stack.__aenter__()
        try:
            streams = await self._stack.enter_async_context(self._open_transport(lib))
            read, write = streams[0], streams[1]
            self.session = await self._stack.enter_async_context(lib["ClientSession"](read, write))
            await self.session.initialize()
        except BaseException:
            await self._stack.__aexit__(*sys.exc_info())
            raise
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._stack is not None:
            await self._stack.__aexit__(exc_type, exc, tb)
        self._stack = None
        self.session = None

    async def list_tools(self) -> list[dict[str, Any]]:
        response = await self.session.list_tools()
        return [
            {
                "name": tool.name,
                "description": tool.description or "",
                "input_schema": tool.inputSchema,
            }
            for tool in response.tools
        ]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        result = await self.session.call_tool(name, arguments=arguments)
        content = []
        for block in result.content:
            if getattr(block, "type", None) == "text":
                content.append({"type": "text", "text": block.text})
            else:
                content.append(
                    block.model_dump() if hasattr(block, "model_dump") else {"repr": repr(block)}
                )
        return {
            "is_error": bool(getattr(result, "isError", False)),
            "content": content,
            "structured_content": getattr(result, "structuredContent", None),
        }


def normalize_transport(value: str) -> str:
    key = value.strip().lower().replace("-", "_")
    if key == "stdio":
        return "stdio"
    if key in ("http", "streamable_http", "streamablehttp"):
        return "http"
    if key == "sse":
        return "sse"
    raise ValueError(f"unknown transport {value!r}: use stdio, http or sse")


def parse_headers(items: list[str]) -> dict[str, str]:
    headers: dict[str, str] = {}
    for item in items:
        if ":" not in item:
            raise ValueError(f"header {item!r} must look like 'Name: value'")
        name, value = item.split(":", 1)
        headers[name.strip()] = value.strip()
    return headers


def parse_env(items: list[str]) -> dict[str, str]:
    env: dict[str, str] = {}
    for item in items:
        if "=" not in item:
            raise ValueError(f"env entry {item!r} must look like NAME=value")
        name, value = item.split("=", 1)
        env[name] = value
    return env


def connection_from_args(ns: argparse.Namespace) -> Connection:
    return Connection(
        ns.transport,
        command=ns.command,
        args=ns.arg,
        env=parse_env(ns.env) or None,
        url=ns.url,
        headers=parse_headers(ns.header),
    )


def add_connection_arguments(parser: argparse.ArgumentParser, *, required: bool = True) -> None:
    parser.add_argument("--transport", required=required, help="stdio, http (streamable HTTP) or sse")
    parser.add_argument("--command", help="stdio: executable to start (python3, node, uvx ...)")
    parser.add_argument(
        "--arg", action="append", default=[], help="stdio: one argument for the command (repeatable)"
    )
    parser.add_argument(
        "--env", action="append", default=[], help="stdio: NAME=value for the server process"
    )
    parser.add_argument("--url", help="http/sse: endpoint URL")
    parser.add_argument(
        "--header", action="append", default=[], help="http/sse: 'Name: value' (repeatable)"
    )


async def _run_list(ns: argparse.Namespace) -> int:
    async with connection_from_args(ns) as conn:
        tools = await conn.list_tools()
    print(json.dumps(tools, indent=2, ensure_ascii=False))
    return 0


async def _run_call(ns: argparse.Namespace) -> int:
    try:
        arguments = json.loads(ns.args) if ns.args else {}
    except json.JSONDecodeError as exc:
        print(f"--args is not valid JSON: {exc}", file=sys.stderr)
        return 2
    if not isinstance(arguments, dict):
        print("--args must be a JSON object", file=sys.stderr)
        return 2
    async with connection_from_args(ns) as conn:
        result = await conn.call_tool(ns.tool, arguments)
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    return 1 if result["is_error"] else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inspect or call an MCP server over stdio, streamable HTTP or SSE.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("Examples:", 1)[-1],
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_list = sub.add_parser("list-tools", help="print the tools the server exposes as JSON")
    add_connection_arguments(p_list)
    p_call = sub.add_parser("call", help="call one tool and print the result as JSON")
    add_connection_arguments(p_call)
    p_call.add_argument("--tool", required=True, help="tool name")
    p_call.add_argument("--args", default="{}", help="JSON object with the tool arguments")
    return parser


def main(argv: list[str] | None = None) -> int:
    ns = build_parser().parse_args(argv)
    runner = _run_list if ns.cmd == "list-tools" else _run_call
    try:
        return asyncio.run(runner(ns))
    except MissingDependencyError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
