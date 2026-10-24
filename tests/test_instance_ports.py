# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Two Navins on one machine (Windows + WSL, desktop + CLI) never share a port.

A port held by this installation is attached to; a port held by another Navin
(another instance id) or another program moves this one to the next free pair.
"""

from __future__ import annotations

import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from navin import instance


def _health_server(payload: dict | None):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - http.server API
            body = json.dumps(payload).encode() if payload is not None else b"<html>other app</html>"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, server.server_address[1]


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_the_installation_id_is_created_once_and_kept(tmp_path):
    config = tmp_path / ".navin" / "config.json"
    instance._cached.clear()
    first = instance.instance_id(config)
    instance._cached.clear()
    assert instance.instance_id(config) == first == instance.read_instance_id(config)
    assert (tmp_path / ".navin" / "instance-id").read_text() == first


def test_who_answers_on_a_port(tmp_path):
    config = tmp_path / "config.json"
    instance._cached.clear()
    mine = instance.instance_id(config)
    for payload, expected in (
        ({"status": "ok", "instance": mine}, True),
        ({"status": "ok", "instance": "someone-else"}, False),
        ({"status": "ok"}, True),  # a Navin from before ids: assumed ours, as before
        (None, None),  # not a Navin at all
    ):
        server, port = _health_server(payload)
        try:
            assert instance.is_own_navin("127.0.0.1", port, config) is expected
        finally:
            server.shutdown()
    assert instance.is_own_navin("127.0.0.1", _free_port(), config) is None


def test_ports_move_away_from_another_navin_but_stay_with_this_one(tmp_path):
    config = tmp_path / "config.json"
    instance._cached.clear()
    mine = instance.instance_id(config)

    other, other_port = _health_server({"status": "ok", "instance": "wsl-navin"})
    try:
        gateway = _free_port()
        webui, moved_gateway, reason = instance.ports_for_this_navin(other_port, gateway, config)
        assert reason == "another_navin"
        assert webui != other_port and instance.port_free(webui) and instance.port_free(moved_gateway)
    finally:
        other.shutdown()

    own, own_port = _health_server({"status": "ok", "instance": mine})
    try:
        gateway = _free_port()
        assert instance.ports_for_this_navin(own_port, gateway, config) == (own_port, gateway, "")
    finally:
        own.shutdown()

    free_webui, free_gateway = _free_port(), _free_port()
    assert instance.ports_for_this_navin(free_webui, free_gateway, config) == (free_webui, free_gateway, "")


def test_health_answers_with_this_installation(tmp_path, monkeypatch):
    monkeypatch.setattr("navin.config.loader._current_config_path", tmp_path / "config.json")
    instance._cached.clear()
    payload = instance.health_payload()
    assert payload["status"] == "ok" and payload["instance"] == instance.read_instance_id(tmp_path / "config.json")
