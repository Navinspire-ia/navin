"""Probe whether DebugMCP is reachable via the real MCP handshake."""

from __future__ import annotations

from typing import Any

from navin.debug.mcp_client import mcp_status_json, probe_debugmcp

__all__ = ["mcp_status_json", "probe_debugmcp"]


def status(url: str | None = None, *, timeout: float = 5.0) -> dict[str, Any]:
    return probe_debugmcp(url, timeout=timeout)
