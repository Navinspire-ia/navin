# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""An API that answers 404 at "/" is up; a 500 or no answer is not."""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from navin.agent.tools.preview_server import http_reachable


def _serve(status: int):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            self.send_response(status)
            self.end_headers()

        def log_message(self, *_args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


@pytest.mark.parametrize(("status", "up"), [(200, True), (302, True), (404, True), (401, True), (500, False), (503, False)])
def test_status_codes(status: int, up: bool) -> None:
    server = _serve(status)
    try:
        assert http_reachable(f"http://127.0.0.1:{server.server_port}/") is up
    finally:
        server.shutdown()


def test_nothing_listening() -> None:
    assert http_reachable("http://127.0.0.1:9/") is False
