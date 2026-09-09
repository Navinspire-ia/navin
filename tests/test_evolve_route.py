# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""The Evolve routes, and what the gateway answers when one of them breaks.

The WebUI gateway is a ``websockets`` server answering HTTP from its handshake
hook. An exception escaping a route makes the library reply 500 with "Failed to
open a WebSocket connection. See server log for more information." - a sentence
about a WebSocket the dashboard never opened, printed verbatim in the panel.
These tests pin both halves: the routes exist, and a broken one comes back as a
shaped JSON error.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import patch

import pytest

from navin.webui.stall_watch import StallReporter
from navin.webui.ws_http import GatewayHTTPHandler


class _Request:
    def __init__(self, path: str) -> None:
        self.path = path
        self.headers: dict[str, str] = {}


class _Log:
    """Enough logger for the router; records what it was told."""

    def __init__(self) -> None:
        self.exceptions: list[str] = []

    def exception(self, message, *args) -> None:
        self.exceptions.append(str(message))

    def warning(self, message, *args) -> None:
        pass


def _handler() -> GatewayHTTPHandler:
    handler = object.__new__(GatewayHTTPHandler)
    handler.check_api_token = lambda request: True
    handler._log = _Log()
    # dispatch() awaits every route through the stall reporter; a handler
    # built without __init__ needs one too.
    handler._stalls = StallReporter(handler._log)
    return handler


EVOLVE_ROUTES = [
    ("/api/webui/evolve/overview", "overview"),
    ("/api/webui/evolve/status", "status"),
    ("/api/webui/evolve/enqueue", "enqueue"),
    ("/api/webui/evolve/cancel", "cancel"),
    ("/api/webui/evolve/daemon/start", "daemon_start"),
    ("/api/webui/evolve/daemon/stop", "daemon_stop"),
    ("/api/webui/evolve/verify", "verify"),
    ("/api/webui/evolve/merge", "merge"),
    ("/api/webui/evolve/pr", "pr"),
    ("/api/webui/evolve/rollback", "rollback"),
    ("/api/webui/evolve/docs", "docs"),
    ("/api/webui/evolve/autorun", "autorun"),
]


@pytest.mark.parametrize("path,action", EVOLVE_ROUTES)
def test_every_evolve_route_reaches_its_action(path: str, action: str) -> None:
    handler = _handler()
    seen: dict[str, str] = {}

    async def capture(request, got_action):
        seen["action"] = got_action
        return "response"

    handler._handle_webui_evolve = capture
    response = asyncio.run(handler._dispatch_misc_routes(None, _Request(path), path))
    assert response == "response", f"{path} is not registered"
    assert seen["action"] == action


def test_a_shaped_api_error_keeps_its_status_and_message() -> None:
    from navin.webui import evolve_api

    handler = _handler()
    with patch.object(
        evolve_api,
        "overview",
        side_effect=evolve_api.EvolveApiError(404, "no such directory: /gone"),
    ):
        response = asyncio.run(
            handler._handle_webui_evolve(_Request("/api/webui/evolve/overview?path=/gone"), "overview")
        )
    assert response.status_code == 404
    assert b"no such directory" in response.body


def test_an_unexpected_crash_becomes_json_not_websockets_boilerplate() -> None:
    """The regression this whole file exists for: a route raising anything
    other than EvolveApiError used to leave the gateway's handshake hook."""
    handler = _handler()

    async def explode(connection, request, got):
        raise AttributeError("module 'socket' has no attribute 'AF_UNIX'")

    handler._dispatch_resolved = explode
    response = asyncio.run(
        handler.dispatch(None, _Request("/api/webui/evolve/overview?path=/x"))
    )

    assert response.status_code == 500
    payload = json.loads(response.body.decode("utf-8"))
    assert payload["error"] == "internal server error"
    assert payload["path"] == "/api/webui/evolve/overview"
    assert b"WebSocket" not in response.body
    assert handler._log.exceptions, "the traceback must still reach the server log"


class TestTheStatusRouteOnEveryPlatform:
    """The daemon is reached over a loopback port now, so no platform is
    turned away before the attempt. What the panel gets back is a state, on
    Windows exactly as on Linux."""

    def test_a_workspace_without_a_daemon_answers_not_running(self, tmp_path) -> None:
        handler = _handler()
        response = asyncio.run(
            handler._handle_webui_evolve(
                _Request(f"/api/webui/evolve/status?path={tmp_path}"), "status"
            )
        )
        assert response.status_code == 200
        payload = json.loads(response.body.decode("utf-8"))
        assert payload == {
            "online": False,
            "status": None,
            "supported": True,
            "reason": "not_running",
        }

    def test_an_interpreter_without_af_unix_gets_the_same_answer(self, tmp_path) -> None:
        """The Windows shape. It used to leave the route as an
        ``AttributeError`` and come back as WebSocket boilerplate."""
        import socket

        from navin.webui import evolve_api

        # The loop is built first: asyncio's own wakeup pipe is a socketpair,
        # which on Linux needs the very constant this test removes.
        loop = asyncio.new_event_loop()
        saved = getattr(socket, "AF_UNIX", None)
        if saved is not None:
            delattr(socket, "AF_UNIX")
        try:
            handler = _handler()
            response = loop.run_until_complete(
                handler._handle_webui_evolve(
                    _Request(f"/api/webui/evolve/status?path={tmp_path}"), "status"
                )
            )
        finally:
            if saved is not None:
                socket.AF_UNIX = saved
            loop.close()
        assert response.status_code == 200
        payload = json.loads(response.body.decode("utf-8"))
        assert payload["supported"] is True
        assert payload["reason"] == evolve_api.DAEMON_REASON_NOT_RUNNING
        assert b"WebSocket" not in response.body

    def test_starting_a_daemon_without_a_binary_names_the_binary(self, tmp_path) -> None:
        """The only 501 left on this route is a missing engine, never a
        platform verdict."""
        from navin.webui import evolve_api

        handler = _handler()
        with patch.object(evolve_api, "resolve_engine_bin", return_value=None):
            response = asyncio.run(
                handler._handle_webui_evolve(
                    _Request(f"/api/webui/evolve/daemon/start?path={tmp_path}"),
                    "daemon_start",
                )
            )
        assert response.status_code == 501
        assert b"navin-engine binary not found" in response.body
        assert b"Unix domain socket" not in response.body


def test_a_route_that_answers_normally_is_untouched_by_the_guard() -> None:
    handler = _handler()

    async def answer(connection, request, got):
        return "ok"

    handler._dispatch_resolved = answer
    assert asyncio.run(handler.dispatch(None, _Request("/health"))) == "ok"


def test_cancelling_the_request_is_not_swallowed_as_a_500() -> None:
    """A client that hangs up cancels the handshake task; that is not a bug
    to report as an internal error."""
    handler = _handler()

    async def cancelled(connection, request, got):
        raise asyncio.CancelledError

    handler._dispatch_resolved = cancelled
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(handler.dispatch(None, _Request("/api/webui/evolve/overview")))
