"""``/api/debug/ui-zoom`` is unauthenticated, so the gateway must keep it loopback-only."""

from __future__ import annotations

import asyncio
import types
import unittest
from typing import Any

from websockets.datastructures import Headers
from websockets.http11 import Request as WsRequest

from navin.webui.ui_zoom_debug import _reset_for_tests
from navin.webui.ws_http import GatewayHTTPHandler


def _request(host: str) -> WsRequest:
    return WsRequest(path="/api/debug/ui-zoom?zoom=2.0", headers=Headers([("Host", host)]))


def _connection(remote_host: str) -> Any:
    return types.SimpleNamespace(remote_address=(remote_host, 51515))


def _dispatch(connection: Any, request: WsRequest) -> Any:
    handler = object.__new__(GatewayHTTPHandler)
    return asyncio.run(
        handler._dispatch_resolved(connection, request, "/api/debug/ui-zoom")  # pyright: ignore[reportPrivateUsage]
    )


class UiZoomDebugRouteGateTest(unittest.TestCase):
    def setUp(self) -> None:
        _reset_for_tests()

    def test_lan_peer_is_refused(self) -> None:
        response = _dispatch(_connection("192.168.1.20"), _request("192.168.1.5:18789"))
        self.assertEqual(response.status_code, 403)
        self.assertIn(b"localhost-only", response.body)

    def test_loopback_peer_with_forwarded_lan_host_is_refused(self) -> None:
        # A reverse proxy on the same machine still exposes the route remotely.
        request = WsRequest(
            path="/api/debug/ui-zoom?zoom=2.0",
            headers=Headers([("Host", "127.0.0.1:18789"), ("X-Forwarded-For", "10.0.0.7")]),
        )
        response = _dispatch(_connection("127.0.0.1"), request)
        self.assertEqual(response.status_code, 403)

    def test_loopback_peer_reaches_the_handler(self) -> None:
        response = _dispatch(_connection("127.0.0.1"), _request("127.0.0.1:18789"))
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'"zoom": 2.0', response.body)


if __name__ == "__main__":
    raise SystemExit(unittest.main())
