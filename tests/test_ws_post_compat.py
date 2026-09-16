# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Plain HTTP POST/DELETE must reach the webui API served on the ws port.

Regression for the "500 on /api/webui/sessions/import" report: websockets
>= 14 rejects any non-GET method inside ``Request.parse``, before
``process_request`` can answer the plain HTTP routes served on the same
port. The browser then saw a failed handshake (500 through the dev proxy).
"""

from __future__ import annotations

import asyncio
import unittest
from collections.abc import Callable, Generator

from websockets.asyncio.server import serve
from websockets.datastructures import Headers
from websockets.http11 import Request, Response


def _reader(*lines: bytes) -> Callable[[int], Generator[None, None, bytes]]:
    """Mimic the connection reader websockets hands to ``Request.parse``."""

    it = iter(lines)

    def read_line(_limit: int) -> Generator[None, None, bytes]:
        value = next(it)
        yield
        return value

    return read_line


def _parse(read_line: Callable[[int], Generator[None, None, bytes]]) -> Request:
    """Drive the parse generator the way ``yield from`` would."""

    gen = Request.parse(read_line)  # type: ignore[arg-type]
    while True:
        try:
            gen.send(None)
        except StopIteration as stop:
            return stop.value


def _request_bytes(method: str, target: str, body: bytes = b"") -> bytes:
    head = f"{method} {target} HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n"
    if body:
        head += f"Content-Length: {len(body)}\r\n"
    return head.encode() + b"\r\n" + body


class ParseMethodTests(unittest.TestCase):
    def test_parse_accepts_post_and_records_method(self) -> None:
        from navin.channels.websocket import _install_http_method_compat

        _install_http_method_compat()

        request = _parse(
            _reader(
                b"POST /api/webui/sessions/import?source=claude-code HTTP/1.1\r\n",
                b"Host: localhost\r\n",
                b"\r\n",
            )
        )
        self.assertEqual(getattr(request, "method", "GET"), "POST")
        self.assertEqual(
            request.path, "/api/webui/sessions/import?source=claude-code"
        )

    def test_parse_still_accepts_get(self) -> None:
        from navin.channels.websocket import _install_http_method_compat

        _install_http_method_compat()

        request = _parse(
            _reader(
                b"GET /api/webui/account HTTP/1.1\r\n",
                b"Host: localhost\r\n",
                b"\r\n",
            )
        )
        self.assertEqual(getattr(request, "method", "GET"), "GET")
        self.assertEqual(request.path, "/api/webui/account")

    def test_zero_content_length_is_tolerated(self) -> None:
        """Proxies add "Content-Length: 0" to bodyless POSTs; not a body."""
        from navin.channels.websocket import _install_http_method_compat

        _install_http_method_compat()

        request = _parse(
            _reader(
                b"POST /api/webui/sessions/import?source=claude-code HTTP/1.1\r\n",
                b"Host: localhost\r\n",
                b"Content-Length: 0\r\n",
                b"\r\n",
            )
        )
        self.assertEqual(getattr(request, "method", "GET"), "POST")

    def test_body_still_rejected(self) -> None:
        from navin.channels.websocket import _install_http_method_compat

        _install_http_method_compat()

        with self.assertRaises(ValueError):
            _parse(
                _reader(
                    b"POST /api/x HTTP/1.1\r\n",
                    b"Host: localhost\r\n",
                    b"Content-Length: 3\r\n",
                    b"\r\n",
                )
            )


class PostThroughServerTests(unittest.TestCase):
    """End to end: a real websockets server answers a bodyless POST."""

    def test_post_reaches_process_request(self) -> None:
        from navin.channels.websocket import _install_http_method_compat

        _install_http_method_compat()

        seen: dict[str, str] = {}

        async def scenario() -> None:
            async def process_request(connection, request):  # type: ignore[no-untyped-def]
                seen["method"] = getattr(request, "method", "")
                seen["path"] = request.path
                return Response(200, "OK", Headers(), b"import-ok")

            async def handler(connection):  # type: ignore[no-untyped-def]
                await connection.wait_closed()

            server = await serve(
                handler,
                "127.0.0.1",
                0,
                process_request=process_request,
                open_timeout=10,
            )
            port = server.sockets[0].getsockname()[1]
            raw = b""
            try:
                reader, writer = await asyncio.open_connection("127.0.0.1", port)
                writer.write(
                    _request_bytes(
                        "POST", "/api/webui/sessions/import?source=claude-code"
                    )
                )
                await writer.drain()
                raw = await asyncio.wait_for(reader.read(), 5)
                writer.close()
            finally:
                server.close()
                await server.wait_closed()

            self.assertIn(b"200 OK", raw)
            self.assertIn(b"import-ok", raw)
            self.assertEqual(seen["method"], "POST")
            self.assertEqual(
                seen["path"], "/api/webui/sessions/import?source=claude-code"
            )

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
