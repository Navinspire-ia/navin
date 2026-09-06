"""Local injection proxy for the instrumented Dev preview.

Sits between the preview iframe and the user's dev server
(``127.0.0.1:<target>``). Every HTML response gets the telemetry probe
injected (see ``preview_probe.py``); everything else - assets, API calls,
WebSocket upgrades (Vite HMR) - is passed through untouched.

The probe posts its batches to ``/__navin_probe__/telemetry`` on the proxy
origin itself, so no CORS, token, or gateway body-parsing is involved.

One proxy per target port, bound to 127.0.0.1 on an ephemeral port.
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
from dataclasses import dataclass
from typing import Any

from loguru import logger

from navin.webui.preview_probe import (
    PROBE_JS,
    PROBE_SCRIPT_PATH,
    PROBE_TELEMETRY_PATH,
    TELEMETRY,
    inject_probe,
)

MAX_HEAD_BYTES = 64 * 1024
MAX_PROBE_BODY_BYTES = 512 * 1024
MAX_INJECT_BODY_BYTES = 8 * 1024 * 1024
UPSTREAM_CONNECT_TIMEOUT_S = 5.0

_HOP_BY_HOP = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailers",
        "transfer-encoding",
        "upgrade",
        "accept-encoding",
        "proxy-connection",
    }
)


class PreviewProxyError(Exception):
    pass


def parse_head(head: bytes) -> tuple[str, list[tuple[str, str]]]:
    """Split an HTTP head into (start line, header pairs)."""
    text = head.decode("latin-1")
    lines = text.split("\r\n")
    start = lines[0]
    headers: list[tuple[str, str]] = []
    for line in lines[1:]:
        if not line:
            continue
        name, sep, value = line.partition(":")
        if sep:
            headers.append((name.strip(), value.strip()))
    return start, headers


def header_value(headers: list[tuple[str, str]], name: str) -> str | None:
    lowered = name.lower()
    for key, value in headers:
        if key.lower() == lowered:
            return value
    return None


async def _read_head(reader: asyncio.StreamReader) -> bytes:
    head = await reader.readuntil(b"\r\n\r\n")
    if len(head) > MAX_HEAD_BYTES:
        raise PreviewProxyError("HTTP head too large")
    return head


async def _read_chunked(reader: asyncio.StreamReader) -> bytes:
    """Decode a chunked transfer-encoded body from *reader*."""
    body = bytearray()
    while True:
        size_line = await reader.readline()
        size_text = size_line.split(b";", 1)[0].strip()
        try:
            size = int(size_text, 16)
        except ValueError as e:
            raise PreviewProxyError(f"bad chunk size: {size_text!r}") from e
        if size == 0:
            # Consume trailer lines up to the final blank line.
            while True:
                line = await reader.readline()
                if line in (b"\r\n", b"\n", b""):
                    break
            return bytes(body)
        body.extend(await reader.readexactly(size))
        await reader.readexactly(2)  # trailing CRLF
        if len(body) > MAX_INJECT_BODY_BYTES:
            raise PreviewProxyError("chunked body too large to inject")


async def _read_body(
    reader: asyncio.StreamReader, headers: list[tuple[str, str]]
) -> bytes:
    transfer = (header_value(headers, "Transfer-Encoding") or "").lower()
    if "chunked" in transfer:
        return await _read_chunked(reader)
    length = header_value(headers, "Content-Length")
    if length is not None:
        size = int(length)
        if size > MAX_INJECT_BODY_BYTES:
            raise PreviewProxyError("body too large to inject")
        return await reader.readexactly(size)
    # No length: read until upstream closes (we always send Connection: close).
    body = await reader.read(MAX_INJECT_BODY_BYTES + 1)
    if len(body) > MAX_INJECT_BODY_BYTES:
        raise PreviewProxyError("body too large to inject")
    return body


def _simple_response(
    status: int, reason: str, body: bytes, content_type: str
) -> bytes:
    return (
        f"HTTP/1.1 {status} {reason}\r\n"
        f"Content-Type: {content_type}\r\n"
        f"Content-Length: {len(body)}\r\n"
        "Cache-Control: no-store\r\n"
        "Connection: close\r\n\r\n"
    ).encode("latin-1") + body


@dataclass
class _ProxyInstance:
    target_port: int
    proxy_port: int
    server: asyncio.base_events.Server


class PreviewProxyRegistry:
    """Owns one injection proxy per preview target port."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._instances: dict[int, _ProxyInstance] = {}

    async def ensure(self, target_port: int) -> int:
        """Start (or reuse) the proxy for *target_port*; returns proxy port."""
        if not (1 <= target_port <= 65535):
            raise PreviewProxyError(f"invalid port: {target_port}")
        with self._lock:
            existing = self._instances.get(target_port)
            if existing is not None and existing.server.is_serving():
                return existing.proxy_port
        handler = _ConnectionHandler(target_port)
        server = await asyncio.start_server(
            handler.handle, host="127.0.0.1", port=0
        )
        proxy_port = server.sockets[0].getsockname()[1]
        with self._lock:
            self._instances[target_port] = _ProxyInstance(
                target_port=target_port, proxy_port=proxy_port, server=server
            )
        logger.info(
            "preview proxy started: 127.0.0.1:{} -> 127.0.0.1:{}",
            proxy_port,
            target_port,
        )
        return proxy_port

    def status(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                {
                    "targetPort": inst.target_port,
                    "proxyPort": inst.proxy_port,
                    "running": inst.server.is_serving(),
                }
                for inst in self._instances.values()
            ]

    async def stop(self, target_port: int) -> bool:
        with self._lock:
            inst = self._instances.pop(target_port, None)
        if inst is None:
            return False
        inst.server.close()
        with contextlib.suppress(Exception):
            await inst.server.wait_closed()
        return True

    async def stop_all(self) -> None:
        with self._lock:
            instances = list(self._instances.values())
            self._instances.clear()
        for inst in instances:
            inst.server.close()
            with contextlib.suppress(Exception):
                await inst.server.wait_closed()


PROXIES = PreviewProxyRegistry()


class _ConnectionHandler:
    def __init__(self, target_port: int) -> None:
        self.target_port = target_port

    async def handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            await self._handle_inner(reader, writer)
        except (
            asyncio.IncompleteReadError,
            asyncio.LimitOverrunError,
            ConnectionError,
            PreviewProxyError,
            OSError,
        ) as e:
            logger.debug("preview proxy connection ended: {}", e)
        finally:
            with contextlib.suppress(Exception):
                writer.close()

    async def _handle_inner(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        head = await _read_head(reader)
        start, headers = parse_head(head)
        parts = start.split(" ")
        if len(parts) < 3:
            writer.write(_simple_response(400, "Bad Request", b"", "text/plain"))
            await writer.drain()
            return
        method, path = parts[0].upper(), parts[1]

        # Probe endpoints served by the proxy itself (same origin as the app).
        if path.split("?", 1)[0] == PROBE_SCRIPT_PATH:
            writer.write(
                _simple_response(
                    200,
                    "OK",
                    PROBE_JS.encode("utf-8"),
                    "application/javascript; charset=utf-8",
                )
            )
            await writer.drain()
            return
        if path.split("?", 1)[0] == PROBE_TELEMETRY_PATH:
            length = int(header_value(headers, "Content-Length") or "0")
            if length > MAX_PROBE_BODY_BYTES:
                writer.write(
                    _simple_response(413, "Payload Too Large", b"", "text/plain")
                )
                await writer.drain()
                return
            body = await reader.readexactly(length) if length else b""
            accepted = TELEMETRY.record_payload(self.target_port, body)
            writer.write(
                _simple_response(
                    200, "OK", f'{{"accepted":{accepted}}}'.encode(), "application/json"
                )
            )
            await writer.drain()
            return

        upgrade = (header_value(headers, "Upgrade") or "").lower()
        if upgrade == "websocket":
            await self._handle_websocket(head, reader, writer)
            return

        await self._handle_http(method, path, headers, reader, writer)

    async def _connect_upstream(
        self,
    ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        return await asyncio.wait_for(
            asyncio.open_connection("127.0.0.1", self.target_port),
            timeout=UPSTREAM_CONNECT_TIMEOUT_S,
        )

    async def _handle_websocket(
        self,
        head: bytes,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Transparent bidirectional splice for HMR / app WebSockets."""
        up_reader, up_writer = await self._connect_upstream()
        try:
            up_writer.write(self._rewrite_host(head))
            await up_writer.drain()

            async def pipe(
                src: asyncio.StreamReader, dst: asyncio.StreamWriter
            ) -> None:
                try:
                    while True:
                        chunk = await src.read(65536)
                        if not chunk:
                            break
                        dst.write(chunk)
                        await dst.drain()
                except (ConnectionError, OSError):
                    pass
                finally:
                    with contextlib.suppress(Exception):
                        dst.close()

            await asyncio.gather(
                pipe(reader, up_writer), pipe(up_reader, writer)
            )
        finally:
            with contextlib.suppress(Exception):
                up_writer.close()

    def _rewrite_host(self, head: bytes) -> bytes:
        start, headers = parse_head(head)
        out = [start]
        replaced = False
        for name, value in headers:
            if name.lower() == "host":
                out.append(f"Host: 127.0.0.1:{self.target_port}")
                replaced = True
            else:
                out.append(f"{name}: {value}")
        if not replaced:
            out.append(f"Host: 127.0.0.1:{self.target_port}")
        return ("\r\n".join(out) + "\r\n\r\n").encode("latin-1")

    async def _handle_http(
        self,
        method: str,
        path: str,
        headers: list[tuple[str, str]],
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        request_body = b""
        length_header = header_value(headers, "Content-Length")
        if length_header:
            request_body = await reader.readexactly(int(length_header))

        up_reader, up_writer = await self._connect_upstream()
        try:
            out = [f"{method} {path} HTTP/1.1"]
            for name, value in headers:
                if name.lower() in _HOP_BY_HOP or name.lower() == "host":
                    continue
                out.append(f"{name}: {value}")
            out.append(f"Host: 127.0.0.1:{self.target_port}")
            # identity + close keep the injection path simple and reliable.
            out.append("Accept-Encoding: identity")
            out.append("Connection: close")
            if request_body:
                out.append(f"Content-Length: {len(request_body)}")
            up_writer.write(("\r\n".join(out) + "\r\n\r\n").encode("latin-1"))
            if request_body:
                up_writer.write(request_body)
            await up_writer.drain()

            response_head = await _read_head(up_reader)
            status_line, response_headers = parse_head(response_head)
            content_type = (
                header_value(response_headers, "Content-Type") or ""
            ).lower()

            if "text/html" in content_type:
                body = await _read_body(up_reader, response_headers)
                body = inject_probe(body)
                out_headers = [status_line]
                for name, value in response_headers:
                    if name.lower() in (
                        "content-length",
                        "transfer-encoding",
                        "connection",
                        "content-security-policy",
                    ):
                        continue
                    out_headers.append(f"{name}: {value}")
                out_headers.append(f"Content-Length: {len(body)}")
                out_headers.append("Connection: close")
                writer.write(
                    ("\r\n".join(out_headers) + "\r\n\r\n").encode("latin-1")
                )
                writer.write(body)
                await writer.drain()
                return

            # Non-HTML: forward head (minus keep-alive semantics), then stream.
            out_headers = [status_line]
            for name, value in response_headers:
                if name.lower() == "connection":
                    continue
                out_headers.append(f"{name}: {value}")
            out_headers.append("Connection: close")
            writer.write(("\r\n".join(out_headers) + "\r\n\r\n").encode("latin-1"))
            while True:
                chunk = await up_reader.read(65536)
                if not chunk:
                    break
                writer.write(chunk)
                await writer.drain()
        finally:
            with contextlib.suppress(Exception):
                up_writer.close()
