# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Real DebugMCP Streamable HTTP client (initialize + tools/list).

Speaks the MCP JSON-RPC handshake used by DebugMCP / the MCP SDK - no GET probe
hack. When the extension is down, returns a clear connection error.
"""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urlparse

import httpx

_DEFAULT_URL = "http://127.0.0.1:3001/mcp"
_PROTOCOL = "2025-06-18"
_CLIENT_INFO = {"name": "navin-debug", "version": "1.0.0"}

# Expected DAP surface from DebugMCP (informational if server is older/newer).
_EXPECTED_TOOLS = (
    "add_breakpoint",
    "start_debugging",
    "list_variable_names",
    "get_variables_values",
    "evaluate_expression",
    "continue_execution",
    "pause_execution",
)


def _parse_jsonrpc_payload(content_type: str, body: str) -> dict[str, Any] | None:
    """Parse a JSON or SSE body into the first JSON-RPC object found."""
    text = (body or "").strip()
    if not text:
        return None
    ctype = (content_type or "").lower()
    if "application/json" in ctype or text.startswith("{"):
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            data = None
        if isinstance(data, dict):
            return data
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict) and ("result" in item or "error" in item):
                    return item
    # SSE: look for data: {...} frames
    for match in re.finditer(r"(?m)^data:\s*(\{.*\})\s*$", text):
        try:
            data = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data
    return None


def _rpc(
    client: httpx.Client,
    url: str,
    *,
    method: str,
    params: dict[str, Any] | None,
    rpc_id: int | None,
    session_id: str | None = None,
) -> tuple[dict[str, Any] | None, str | None, int]:
    payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        payload["params"] = params
    if rpc_id is not None:
        payload["id"] = rpc_id
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if session_id:
        headers["mcp-session-id"] = session_id
    resp = client.post(url, content=json.dumps(payload), headers=headers)
    sid = resp.headers.get("mcp-session-id") or session_id
    parsed = _parse_jsonrpc_payload(resp.headers.get("content-type", ""), resp.text)
    return parsed, sid, resp.status_code


def probe_debugmcp(url: str | None = None, *, timeout: float = 5.0) -> dict[str, Any]:
    """Initialize a DebugMCP session and list real tools.

    Returns structured status: reachable, session_id, tools, missing expected tools.
    """
    endpoint = (url or _DEFAULT_URL).strip() or _DEFAULT_URL
    parsed_url = urlparse(endpoint)
    if parsed_url.scheme not in {"http", "https"}:
        return {
            "ok": False,
            "reachable": False,
            "url": endpoint,
            "error": f"unsupported URL scheme: {parsed_url.scheme!r}",
        }
    if parsed_url.hostname not in {"127.0.0.1", "localhost", "::1"}:
        # DebugMCP is a local extension server - refuse remote hosts by default.
        return {
            "ok": False,
            "reachable": False,
            "url": endpoint,
            "error": "DebugMCP probe only allows localhost endpoints",
        }

    result: dict[str, Any] = {
        "ok": False,
        "reachable": False,
        "url": endpoint,
        "protocol": _PROTOCOL,
        "hint": (
            "Install the DebugMCP VS Code/Cursor extension, start a debug-capable "
            "workspace, then enable the Navin MCP preset 'debugmcp'."
        ),
    }

    try:
        with httpx.Client(timeout=timeout, follow_redirects=False) as client:
            init, session_id, status = _rpc(
                client,
                endpoint,
                method="initialize",
                params={
                    "protocolVersion": _PROTOCOL,
                    "capabilities": {},
                    "clientInfo": _CLIENT_INFO,
                },
                rpc_id=1,
            )
            if status >= 500:
                result["error"] = f"HTTP {status} on initialize"
                result["http_status"] = status
                return result
            if not session_id and status >= 400 and init is None:
                result["error"] = f"HTTP {status} on initialize (no MCP session)"
                result["http_status"] = status
                return result

            result["reachable"] = True
            result["http_status"] = status
            result["session_id"] = session_id
            if init and init.get("error"):
                result["error"] = init["error"]
                return result
            if init and isinstance(init.get("result"), dict):
                server = init["result"].get("serverInfo") or {}
                result["server"] = {
                    "name": server.get("name"),
                    "version": server.get("version"),
                }
                result["server_protocol"] = init["result"].get("protocolVersion")

            # notifications/initialized (no response required)
            _rpc(
                client,
                endpoint,
                method="notifications/initialized",
                params={},
                rpc_id=None,
                session_id=session_id,
            )

            listed, _, list_status = _rpc(
                client,
                endpoint,
                method="tools/list",
                params={},
                rpc_id=2,
                session_id=session_id,
            )
            result["tools_http_status"] = list_status
            tools: list[str] = []
            if listed and isinstance(listed.get("result"), dict):
                raw_tools = listed["result"].get("tools") or []
                for tool in raw_tools:
                    if isinstance(tool, dict) and tool.get("name"):
                        tools.append(str(tool["name"]))
            elif listed and listed.get("error"):
                result["error"] = listed["error"]
                result["ok"] = False
                return result

            tools = sorted(set(tools))
            missing = [name for name in _EXPECTED_TOOLS if name not in tools]
            result["tools"] = tools
            result["tool_count"] = len(tools)
            result["missing_expected"] = missing
            result["ok"] = bool(tools) or bool(session_id)
            if tools:
                result["note"] = (
                    f"DebugMCP session live ({len(tools)} tools). "
                    "Use MCP tools for real breakpoints - do not guess from logs."
                )
            elif session_id:
                result["note"] = (
                    "MCP session opened but tools/list returned empty. "
                    "Check the DebugMCP extension version."
                )
                result["ok"] = True
            return result
    except httpx.ConnectError as exc:
        result["error"] = f"connection refused: {exc}"
        result["note"] = (
            "DebugMCP not running. Start the extension, then retry "
            "debug_repair(action=mcp_status)."
        )
        return result
    except httpx.TimeoutException as exc:
        result["error"] = f"timeout: {exc}"
        return result
    except httpx.HTTPError as exc:
        result["error"] = str(exc)[:300]
        return result


def mcp_status_json(url: str | None = None) -> str:
    return json.dumps(probe_debugmcp(url), ensure_ascii=False, indent=2)
