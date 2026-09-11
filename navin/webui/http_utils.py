# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Shared HTTP helpers for the embedded WebUI gateway."""

from __future__ import annotations

import email.utils
import hmac
import http
import ipaddress
import json
import re
import socket
import subprocess
import time
from typing import Any
from urllib.parse import parse_qs, urlparse

from websockets.datastructures import Headers
from websockets.http11 import Response

from navin.utils.proc import no_window_kwargs

QueryParams = dict[str, list[str]]


def strip_trailing_slash(path: str) -> str:
    if len(path) > 1 and path.endswith("/"):
        return path.rstrip("/")
    return path or "/"


def normalize_config_path(path: str) -> str:
    return strip_trailing_slash(path)


def case_insensitive_header(headers: Any, key: str) -> str:
    """Read a header from websockets/http test stubs without assuming casing."""
    try:
        value = headers.get(key)
    except Exception:
        value = None
    if value is None:
        try:
            value = headers.get(key.lower())
        except Exception:
            value = None
    return str(value or "").strip()


def safe_host_header(value: str) -> str:
    """Return a safe Host header value, or empty when it should not be echoed."""
    value = value.strip()
    if not value:
        return ""
    if re.fullmatch(r"\[[0-9A-Fa-f:.]+\](?::\d{1,5})?", value):
        return value
    if re.fullmatch(r"[A-Za-z0-9.-]+(?::\d{1,5})?", value):
        return value
    return ""


def host_for_url(host: str, port: int) -> str:
    host = host.strip()
    if host in ("0.0.0.0", "::"):
        host = "127.0.0.1"
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"{host}:{port}"


def http_json_response(data: dict[str, Any], *, status: int = 200) -> Response:
    body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    headers = Headers(
        [
            ("Date", email.utils.formatdate(usegmt=True)),
            ("Connection", "close"),
            ("Content-Length", str(len(body))),
            ("Content-Type", "application/json; charset=utf-8"),
        ]
    )
    reason = http.HTTPStatus(status).phrase
    return Response(status, reason, headers, body)


def http_response(
    body: bytes,
    *,
    status: int = 200,
    content_type: str = "text/plain; charset=utf-8",
    extra_headers: list[tuple[str, str]] | None = None,
) -> Response:
    headers = [
        ("Date", email.utils.formatdate(usegmt=True)),
        ("Connection", "close"),
        ("Content-Length", str(len(body))),
        ("Content-Type", content_type),
    ]
    if extra_headers:
        headers.extend(extra_headers)
    reason = http.HTTPStatus(status).phrase
    return Response(status, reason, Headers(headers), body)


def http_error(status: int, message: str | None = None) -> Response:
    body = (message or http.HTTPStatus(status).phrase).encode("utf-8")
    return http_response(body, status=status)


def parse_request_path(path_with_query: str) -> tuple[str, QueryParams]:
    """Parse normalized path and query parameters in one pass."""
    parsed = urlparse("ws://x" + path_with_query)
    path = strip_trailing_slash(parsed.path or "/")
    return path, parse_qs(parsed.query, keep_blank_values=True)


def parse_query(path_with_query: str) -> QueryParams:
    return parse_request_path(path_with_query)[1]


def query_first(query: QueryParams, key: str) -> str | None:
    values = query.get(key)
    return values[0] if values else None


def query_all(query: QueryParams, key: str) -> list[str]:
    return [value for value in (query.get(key) or []) if value]


def is_localhost(connection: Any) -> bool:
    addr = getattr(connection, "remote_address", None)
    if not addr:
        return False
    host = addr[0] if isinstance(addr, tuple) else addr
    if not isinstance(host, str):
        return False
    if host.startswith("::ffff:"):
        host = host[7:]
    return host in {"127.0.0.1", "::1", "localhost"}


def _probe_local_ipv4_networks() -> list[ipaddress.IPv4Network]:
    """Networks attached to this machine (used to recognize the WSL host)."""
    networks: list[ipaddress.IPv4Network] = []
    seen: set[str] = set()

    def add(cidr: str) -> None:
        try:
            net = ipaddress.ip_network(cidr, strict=False)
        except ValueError:
            return
        if not isinstance(net, ipaddress.IPv4Network) or net.is_loopback:
            return
        key = str(net)
        if key in seen:
            return
        seen.add(key)
        networks.append(net)

    try:
        out = subprocess.check_output(
            ["ip", "-o", "-4", "addr", "show"],
            text=True,
            timeout=1.0,
            stderr=subprocess.DEVNULL,
            **no_window_kwargs(),
        )
        for line in out.splitlines():
            parts = line.split()
            if "inet" not in parts:
                continue
            add(parts[parts.index("inet") + 1])
    except (OSError, subprocess.SubprocessError):
        pass

    if networks:
        return networks

    candidates: set[str] = set()
    try:
        candidates.add(socket.gethostbyname(socket.gethostname()))
    except OSError:
        pass
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.connect(("1.1.1.1", 80))
            candidates.add(probe.getsockname()[0])
        finally:
            probe.close()
    except OSError:
        pass
    for ip in candidates:
        if ip.startswith("127."):
            continue
        add(f"{ip}/24")
    return networks


_LOCAL_NETWORKS_TTL_S = 60.0
_local_networks_cache: tuple[float, list[ipaddress.IPv4Network]] | None = None


def _iter_local_ipv4_networks() -> list[ipaddress.IPv4Network]:
    """Cached view of the local networks.

    The probe shells out to ``ip addr`` and this runs on the event loop for
    every workspace-control request, so it must not be paid per call: under
    WSL2 no peer is loopback and the UI polls several of these routes.
    """
    global _local_networks_cache

    now = time.monotonic()
    cached = _local_networks_cache
    if cached is not None and now - cached[0] < _LOCAL_NETWORKS_TTL_S:
        return cached[1]
    networks = _probe_local_ipv4_networks()
    _local_networks_cache = (now, networks)
    return networks


def is_same_machine_client(connection: Any) -> bool:
    """True for loopback or a peer on this host's own interfaces.

    Under WSL2 the Windows browser often talks to the WSL IP (or the Windows
    vEthernet address), not 127.0.0.1. Those peers are still this machine:
    Preview Publish and other workspace controls must work for them.
    """
    if is_localhost(connection):
        return True
    addr = getattr(connection, "remote_address", None)
    if not addr:
        return False
    host = addr[0] if isinstance(addr, tuple) else addr
    if not isinstance(host, str):
        return False
    if host.startswith("::ffff:"):
        host = host[7:]
    try:
        remote = ipaddress.ip_address(host)
    except ValueError:
        return False
    if remote.version != 4 or not remote.is_private:
        return False
    return any(remote in net for net in _iter_local_ipv4_networks())


def _host_without_port(value: str) -> str:
    value = value.strip().strip('"').strip("'")
    if not value:
        return ""
    if value.startswith("["):
        end = value.find("]")
        return value[1:end] if end > 0 else value
    if value.count(":") == 1:
        host, port = value.rsplit(":", 1)
        if port.isdigit():
            return host
    return value


def is_loopback_host(value: str) -> bool:
    host = _host_without_port(value)
    if host.startswith("::ffff:"):
        host = host[7:]
    host = host.rstrip(".").lower()
    if host == "localhost" or host.endswith(".localhost"):
        # RFC 6761: *.localhost is loopback. Tauri's WebView uses tauri.localhost.
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _split_comma_header(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def _forwarded_header_values(value: str, key: str) -> list[str]:
    values: list[str] = []
    for entry in _split_comma_header(value):
        for part in entry.split(";"):
            name, sep, raw = part.partition("=")
            if sep and name.strip().lower() == key:
                cleaned = raw.strip().strip('"')
                if cleaned:
                    values.append(cleaned)
    return values


def _all_forwarded_values_are_loopback(headers: Any) -> bool:
    checks: list[str] = []
    checks.extend(_split_comma_header(case_insensitive_header(headers, "X-Forwarded-For")))
    checks.extend(_split_comma_header(case_insensitive_header(headers, "X-Real-IP")))
    checks.extend(_split_comma_header(case_insensitive_header(headers, "X-Forwarded-Host")))
    forwarded = case_insensitive_header(headers, "Forwarded")
    checks.extend(_forwarded_header_values(forwarded, "for"))
    checks.extend(_forwarded_header_values(forwarded, "host"))
    return all(is_loopback_host(value) for value in checks)


def is_local_browser_request(connection: Any, headers: Any) -> bool:
    """True for a browser on this machine (loopback, Tauri, or the WSL host).

    Install and other privileged routes used ``is_localhost`` only. Under WSL2
    the Windows window talks to the WSL IP, and Tauri sends Host
    ``tauri.localhost``. Both are this machine; rejecting them made Install Now
    fail with no useful explanation.
    """
    if not is_same_machine_client(connection):
        return False
    host = case_insensitive_header(headers, "Host")
    if not host:
        return True
    if is_loopback_host(host):
        return _all_forwarded_values_are_loopback(headers)
    # Windows browser → WSL: Host is the WSL address, not 127.0.0.1.
    try:
        remote = ipaddress.ip_address(_host_without_port(host))
    except ValueError:
        return False
    if remote.version != 4 or not remote.is_private:
        return False
    if not any(remote in net for net in _iter_local_ipv4_networks()):
        return False
    return _all_forwarded_values_are_loopback(headers)


def bearer_token(headers: Any) -> str | None:
    auth = headers.get("Authorization") or headers.get("authorization")
    if auth and auth.lower().startswith("bearer "):
        return auth[7:].strip() or None
    return None


def issue_route_secret_matches(headers: Any, configured_secret: str) -> bool:
    if not configured_secret:
        return True
    authorization = headers.get("Authorization") or headers.get("authorization")
    if authorization and authorization.lower().startswith("bearer "):
        supplied = authorization[7:].strip()
        return hmac.compare_digest(supplied, configured_secret)
    header_token = headers.get("X-Navin-Auth") or headers.get("x-navin-auth")
    if not header_token:
        return False
    return hmac.compare_digest(header_token.strip(), configured_secret)
