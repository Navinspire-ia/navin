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
from typing import Any

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


class SlowRouteClientGoneTests(unittest.TestCase):
    """A client that leaves while a slow HTTP route runs (the UI's 20 s timeout).

    Desktop log: 524 "[websockets] unexpected internal error", AssertionError in
    protocol.send_eof, 522 of them right after a slow /github/pr. The peer's
    close fails the protocol (EOF sent) while process_request still runs; the
    late send_response sends EOF again.
    """

    def _serve_and_leave(self, create_connection: Any) -> tuple[list[str], bytes]:
        import logging

        records: list[str] = []

        class Collect(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                records.append(record.getMessage())

        log = logging.getLogger(f"test.ws.slow.{id(records)}")
        log.addHandler(Collect(level=logging.ERROR))
        log.propagate = False
        fast_answer = b""

        async def scenario() -> None:
            nonlocal fast_answer

            async def process_request(connection, request):  # type: ignore[no-untyped-def]
                if request.path == "/slow":
                    await asyncio.sleep(0.3)
                return Response(200, "OK", Headers({"Content-Length": "2"}), b"ok")

            async def handler(connection):  # type: ignore[no-untyped-def]
                await connection.wait_closed()

            kwargs: dict[str, Any] = {"process_request": process_request, "logger": log}
            if create_connection is not None:
                kwargs["create_connection"] = create_connection
            server = await serve(handler, "127.0.0.1", 0, **kwargs)
            port = server.sockets[0].getsockname()[1]
            try:
                _reader, writer = await asyncio.open_connection("127.0.0.1", port)
                writer.write(_request_bytes("GET", "/slow"))
                await writer.drain()
                await asyncio.sleep(0.05)
                writer.close()  # the UI gave up before the route answered
                await asyncio.sleep(0.6)
                reader, writer = await asyncio.open_connection("127.0.0.1", port)
                writer.write(_request_bytes("GET", "/fast"))
                await writer.drain()
                fast_answer = await asyncio.wait_for(reader.read(), 5)
                writer.close()
            finally:
                server.close()
                await server.wait_closed()

        asyncio.run(scenario())
        return records, fast_answer

    def test_the_library_alone_reproduces_the_assertion(self) -> None:
        records, _ = self._serve_and_leave(None)
        self.assertIn("unexpected internal error", records)

    def test_the_gateway_connection_drops_the_late_answer_quietly(self) -> None:
        from navin.channels.websocket import _WebuiServerConnection

        records, fast_answer = self._serve_and_leave(_WebuiServerConnection)
        self.assertEqual(records, [])
        # A caller still there gets its answer as before.
        self.assertIn(b"200 OK", fast_answer)
        self.assertTrue(fast_answer.endswith(b"ok"))


if __name__ == "__main__":
    unittest.main()
