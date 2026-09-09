# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Canonical port registry and conflict checks for Navin services.

Navin-owned ports are bound by the gateway / WebUI / API. External ports
(DebugMCP, Figma MCP, ...) are only clients from Navin's point of view - we
never reserve them at the OS level; we only report their status.
"""

from __future__ import annotations

import re
import socket
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from navin.utils.proc import no_window_kwargs

PortOwner = Literal["navin", "external"]
PortStatus = Literal["free", "navin", "busy", "unknown"]

_DEFAULT_BIND_HOST = "127.0.0.1"
_NAVIN_CMDLINE_MARKERS = ("navin gateway", "navin serve", "-m navin", "python -m navin")


@dataclass(frozen=True)
class PortSpec:
    """One known Navin-related port role."""

    name: str
    default_port: int
    owner: PortOwner
    config_path: str
    description: str
    default_host: str = _DEFAULT_BIND_HOST


@dataclass(frozen=True)
class ResolvedPort:
    """A port role resolved against the active config."""

    spec: PortSpec
    host: str
    port: int


@dataclass(frozen=True)
class ListenerInfo:
    """Best-effort identity of the process listening on a port."""

    pid: int | None
    cmdline: str | None
    source: str


@dataclass(frozen=True)
class PortCheckResult:
    """Status of one resolved port."""

    role: ResolvedPort
    status: PortStatus
    listener: ListenerInfo | None = None
    detail: str = ""

    @property
    def is_conflict(self) -> bool:
        """True when a Navin-owned port is held by a non-Navin process."""
        return self.role.spec.owner == "navin" and self.status == "busy"


PORT_REGISTRY: tuple[PortSpec, ...] = (
    PortSpec(
        name="webui",
        default_port=8765,
        owner="navin",
        config_path="channels.websocket.port",
        description="WebUI + WebSocket channel",
        default_host="127.0.0.1",
    ),
    PortSpec(
        name="gateway",
        default_port=18790,
        owner="navin",
        config_path="gateway.port",
        description="Gateway health endpoint",
        default_host="127.0.0.1",
    ),
    PortSpec(
        name="api",
        default_port=8900,
        owner="navin",
        config_path="api.port",
        description="OpenAI-compatible API (navin serve)",
        default_host="127.0.0.1",
    ),
    PortSpec(
        name="debugmcp",
        default_port=3001,
        owner="external",
        config_path="tools.mcp_servers.debugmcp.url",
        description="DebugMCP extension MCP server (client only)",
        default_host="127.0.0.1",
    ),
    PortSpec(
        name="figma",
        default_port=3845,
        owner="external",
        config_path="tools.mcp_servers.figma.url",
        description="Figma Dev Mode MCP server (client only)",
        default_host="127.0.0.1",
    ),
)


def port_registry_by_name() -> dict[str, PortSpec]:
    return {spec.name: spec for spec in PORT_REGISTRY}


def port_in_use(host: str, port: int, *, timeout_s: float = 0.25) -> bool:
    """Return True if something accepts a TCP connection on host:port."""
    if port <= 0 or port > 65535:
        return False
    for target in _connect_hosts(host):
        try:
            with socket.create_connection((target, port), timeout=timeout_s):
                return True
        except OSError:
            continue
    return False


def who_listens(port: int) -> ListenerInfo | None:
    """Best-effort PID/cmdline for whatever listens on ``port`` (IPv4/IPv6)."""
    if port <= 0 or port > 65535:
        return None
    info = _who_via_ss(port)
    if info is not None:
        return info
    return _who_via_lsof(port)


def resolve_from_config(config: Any | None = None) -> list[ResolvedPort]:
    """Resolve registry defaults against an optional Config object."""
    out: list[ResolvedPort] = []
    for spec in PORT_REGISTRY:
        host, port = _resolve_spec(spec, config)
        out.append(ResolvedPort(spec=spec, host=host, port=port))
    return out


def check_ports(
    config: Any | None = None,
    *,
    include_external: bool = True,
) -> list[PortCheckResult]:
    """Probe every registry role and classify free / navin / busy."""
    results: list[PortCheckResult] = []
    for role in resolve_from_config(config):
        if role.spec.owner == "external" and not include_external:
            continue
        results.append(_check_one(role))
    return results


def check_navin_ports(config: Any | None = None) -> list[PortCheckResult]:
    """Return only conflicting Navin-owned ports (non-Navin listener)."""
    return [
        row
        for row in check_ports(config, include_external=False)
        if row.is_conflict
    ]


def format_port_table(results: list[PortCheckResult]) -> str:
    """Plain-text table for CLI display."""
    headers = ("ROLE", "OWNER", "HOST:PORT", "STATUS", "DETAIL")
    rows: list[tuple[str, ...]] = [headers]
    for row in results:
        endpoint = f"{row.role.host}:{row.role.port}"
        detail = row.detail
        if row.listener and row.listener.pid:
            cmd = (row.listener.cmdline or "").strip() or "?"
            if len(cmd) > 60:
                cmd = cmd[:57] + "..."
            detail = f"pid={row.listener.pid} {cmd}".strip()
        rows.append(
            (
                row.role.spec.name,
                row.role.spec.owner,
                endpoint,
                row.status,
                detail or row.role.spec.description,
            )
        )
    widths = [max(len(r[i]) for r in rows) for i in range(len(headers))]
    lines = [
        "  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row))
        for row in rows
    ]
    return "\n".join(lines)


def is_navin_listener(listener: ListenerInfo | None) -> bool:
    """Heuristic: cmdline looks like a Navin gateway/serve process."""
    if listener is None or not listener.cmdline:
        return False
    text = listener.cmdline.lower()
    return any(marker in text for marker in _NAVIN_CMDLINE_MARKERS)


def _check_one(role: ResolvedPort) -> PortCheckResult:
    if not port_in_use(role.host, role.port):
        return PortCheckResult(
            role=role,
            status="free",
            detail=role.spec.description,
        )
    listener = who_listens(role.port)
    if is_navin_listener(listener):
        return PortCheckResult(
            role=role,
            status="navin",
            listener=listener,
            detail="already held by a Navin process",
        )
    if listener and listener.pid:
        return PortCheckResult(
            role=role,
            status="busy",
            listener=listener,
            detail=f"in use by pid={listener.pid}",
        )
    if role.spec.owner == "navin":
        return PortCheckResult(
            role=role,
            status="busy",
            listener=listener,
            detail="in use (owner unknown)",
        )
    return PortCheckResult(
        role=role,
        status="busy",
        listener=listener,
        detail="reachable (external service)",
    )


def _field(node: Any, name: str) -> Any:
    """Attribute or mapping lookup: channel configs are plain dicts on Config."""
    if node is None:
        return None
    if isinstance(node, dict):
        return node.get(name)
    return getattr(node, name, None)


def _host_port(node: Any, spec: PortSpec) -> tuple[str, int]:
    host = str(_field(node, "host") or spec.default_host)
    try:
        port = int(_field(node, "port") or spec.default_port)
    except (TypeError, ValueError):
        port = spec.default_port
    return host, port


def _resolve_spec(spec: PortSpec, config: Any | None) -> tuple[str, int]:
    if config is None:
        return spec.default_host, spec.default_port
    if spec.name == "webui":
        # channels.websocket is a dict on the loaded Config, not a model:
        # attribute access silently fell back to 8765 while the gateway
        # served 8766, so every port check looked at an empty port.
        return _host_port(_field(_field(config, "channels"), "websocket"), spec)
    if spec.name == "gateway":
        return _host_port(_field(config, "gateway"), spec)
    if spec.name == "api":
        return _host_port(_field(config, "api"), spec)
    if spec.name in {"debugmcp", "figma"}:
        url = _mcp_server_url(config, spec.name)
        if url:
            host, port = _host_port_from_url(url, spec.default_host, spec.default_port)
            return host, port
        return spec.default_host, spec.default_port
    return spec.default_host, spec.default_port


def _mcp_server_url(config: Any, name: str) -> str | None:
    tools = getattr(config, "tools", None)
    servers = getattr(tools, "mcp_servers", None)
    if not isinstance(servers, dict):
        return None
    cfg = servers.get(name)
    if cfg is None:
        return None
    url = getattr(cfg, "url", None)
    if isinstance(url, str) and url.strip():
        return url.strip()
    if isinstance(cfg, dict):
        raw = cfg.get("url")
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    return None


def _host_port_from_url(url: str, default_host: str, default_port: int) -> tuple[str, int]:
    from urllib.parse import urlparse

    parsed = urlparse(url)
    host = parsed.hostname or default_host
    if parsed.port is not None:
        return host, int(parsed.port)
    if parsed.scheme == "https":
        return host, 443
    if parsed.scheme == "http":
        return host, 80
    return host, default_port


def _loopback_connect_host(host: str) -> str:
    """Map wildcard bind addresses to a connectable loopback target."""
    return _connect_hosts(host)[0]


def _connect_hosts(host: str) -> tuple[str, ...]:
    """Return connectable peers for a bind host, preserving its address family."""
    value = (host or "").strip() or _DEFAULT_BIND_HOST
    if value == "0.0.0.0":
        return ("127.0.0.1",)
    if value in {"::", "[::]"}:
        # Some systems expose an IPv6 wildcard as dual-stack, others as IPv6-only.
        return ("::1", "127.0.0.1")
    return (value.lstrip("[").rstrip("]"),)


def _who_via_ss(port: int) -> ListenerInfo | None:
    try:
        completed = subprocess.run(  # noqa: S603
            ["ss", "-ltnp"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
            **no_window_kwargs(),
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    pid_re = re.compile(r"pid=(\d+)")
    for line in completed.stdout.splitlines():
        if "LISTEN" not in line:
            continue
        if not re.search(rf"[:\]]{port}\b", line):
            continue
        match = pid_re.search(line)
        pid = int(match.group(1)) if match else None
        cmdline = _cmdline_for_pid(pid) if pid else None
        users = ""
        if "users:(" in line:
            users = line.split("users:(", 1)[1].rstrip()
        return ListenerInfo(pid=pid, cmdline=cmdline or users or None, source="ss")
    return None


def _who_via_lsof(port: int) -> ListenerInfo | None:
    try:
        completed = subprocess.run(  # noqa: S603
            ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
            **no_window_kwargs(),
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode not in {0, 1}:
        return None
    lines = [ln for ln in completed.stdout.splitlines() if ln.strip()]
    if len(lines) < 2:
        return None
    parts = lines[1].split()
    if len(parts) < 2:
        return None
    try:
        pid = int(parts[1])
    except ValueError:
        return None
    cmdline = _cmdline_for_pid(pid)
    return ListenerInfo(pid=pid, cmdline=cmdline or parts[0], source="lsof")


def _cmdline_for_pid(pid: int | None) -> str | None:
    if pid is None or pid <= 0:
        return None
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    if not raw:
        return None
    return " ".join(raw.replace("\x00", " ").split())
