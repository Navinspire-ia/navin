# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for the localhost ``/api/debug/ui-zoom`` debug route.

The handler uses :mod:`navin.webui.http_utils` and
:mod:`navin.webui.file_preview` for response shaping and snapshot body
decoding. To keep this test runnable without the full project import chain
(``navin.utils`` pulls in ``loguru``/``tiktoken``), we install lightweight
stand-ins for those two modules into ``sys.modules`` before the first call
to :func:`handle_ui_zoom_debug`. The stand-ins behave like the real helpers
for the surface this code uses.
"""

from __future__ import annotations

import base64
import json
import sys
import types
import unittest
from typing import Any


# ---------------------------------------------------------------------------
# Stubbed helpers used by handle_ui_zoom_debug
# ---------------------------------------------------------------------------


_FILE_BODY_PREFIX = "X-Navin-File-Body-"


def _build_stubs() -> tuple[types.ModuleType, types.ModuleType]:
    """Return (``http_utils_stub``, ``file_preview_stub``) modules."""

    http_utils = types.ModuleType("navin.webui.http_utils")

    def http_error(status: int, message: str | None = None) -> dict[str, Any]:
        return {"__error__": True, "status": status, "message": message}

    def http_json_response(data: dict[str, Any], *, status: int = 200) -> dict[str, Any]:
        return {"__response__": True, "status": status, "data": data}
    def parse_query(path_with_query: str) -> dict[str, list[str]]:
        # Mirror navin.webui.http_utils.parse_query which prepends a fake
        # scheme so urlparse splits path/query the way WebSocket request
        # targets behave.
        from urllib.parse import parse_qs, urlparse

        parsed = urlparse("ws://x" + (path_with_query or ""))
        return parse_qs(parsed.query, keep_blank_values=True)

    def query_first(query: Any, key: str) -> str | None:
        values = query.get(key)
        return values[0] if values else None

    http_utils.http_error = http_error
    http_utils.http_json_response = http_json_response
    http_utils.parse_query = parse_query
    http_utils.query_first = query_first

    file_preview = types.ModuleType("navin.webui.file_preview")

    class WebUIFilePreviewError(Exception):
        def __init__(self, status: int, message: str) -> None:
            super().__init__(message)
            self.status = status
            self.message = message

    def file_body_from_headers(headers: Any) -> str:
        chunks: list[str] = []
        for index in range(64):
            value: Any = None
            if headers is not None:
                value = headers.get(f"{_FILE_BODY_PREFIX}{index}")
                if value is None:
                    value = headers.get(f"{_FILE_BODY_PREFIX}{index}".upper())
            if value is None:
                break
            chunks.append(str(value))
        if not chunks:
            raise WebUIFilePreviewError(400, "missing file content")
        joined = "".join(chunks)
        # Header encoding in production is base64.
        try:
            decoded = base64.b64decode(joined.encode("ascii"), validate=True)
        except Exception:
            return joined
        try:
            return decoded.decode("utf-8")
        except UnicodeDecodeError:
            return decoded.decode("utf-8", errors="replace")

    file_preview.WebUIFilePreviewError = WebUIFilePreviewError
    file_preview.file_body_from_headers = file_body_from_headers

    return http_utils, file_preview


from navin.webui import ui_zoom_debug
from navin.webui.ui_zoom_debug import (
    ALLOWED_ACTIONS,
    ALLOWED_TARGETS,
    _build_command_from_body,
    _build_command_from_query,
    _reset_for_tests,
    handle_ui_zoom_debug,
)

# The handler imports the two helper modules lazily, so the stand-ins only need
# to sit in ``sys.modules`` while this module's tests run. Installing them at
# import time with ``setdefault`` leaked them into the rest of the pytest
# session: any module collected afterwards that imports ``navin.webui.ws_http``
# failed because the stub lacks ``content_disposition_attachment``.
_HTTP_STUB, _FILE_STUB = _build_stubs()
_STUBBED_MODULES = {
    "navin.webui.http_utils": _HTTP_STUB,
    "navin.webui.file_preview": _FILE_STUB,
}
_REAL_MODULES: dict[str, types.ModuleType | None] = {}


def setUpModule() -> None:
    for name, stub in _STUBBED_MODULES.items():
        _REAL_MODULES[name] = sys.modules.get(name)
        sys.modules[name] = stub


def tearDownModule() -> None:
    for name, real in _REAL_MODULES.items():
        if real is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = real
    _REAL_MODULES.clear()


def _make_request(
    *,
    method: str = "GET",
    path: str = "/api/debug/ui-zoom",
    body: dict[str, Any] | None = None,
) -> types.SimpleNamespace:
    """Build a minimal request stub compatible with the handler."""
    headers: dict[str, str] = {}
    if body is not None:
        encoded = base64.b64encode(json.dumps(body).encode("utf-8")).decode("ascii")
        # Single-chunk is fine for tests.
        headers[f"{_FILE_BODY_PREFIX}0"] = encoded
    return types.SimpleNamespace(method=method, path=path, headers=headers)


class UiZoomDebugStoreTests(unittest.TestCase):
    """Exercise the in-memory store directly."""

    def setUp(self) -> None:
        _reset_for_tests()

    def test_reset_clears_state(self) -> None:
        # Drive a command and a snapshot, then reset and confirm zero state.
        req = _make_request(path="/api/debug/ui-zoom?zoom=2.0")
        handle_ui_zoom_debug(req)
        req = _make_request(method="POST", body={"snapshot": {"a": 1}})
        handle_ui_zoom_debug(req)

        _reset_for_tests()

        state = ui_zoom_debug._current_response()
        self.assertEqual(state["seq"], 0)
        self.assertIsNone(state["command"])
        self.assertIsNone(state["snapshot"])
        self.assertEqual(state["updatedAt"], 0.0)
        self.assertTrue(state["ok"])


class UiZoomDebugHandlerTests(unittest.TestCase):
    """Exercise :func:`handle_ui_zoom_debug` end-to-end with stubs."""

    def setUp(self) -> None:
        _reset_for_tests()

    # ------------------------------------------------------------------
    # GET: command queue
    # ------------------------------------------------------------------

    def test_get_zoom_queues_command_with_seq(self) -> None:
        response = handle_ui_zoom_debug(_make_request(path="/api/debug/ui-zoom?zoom=2.5"))
        self.assertFalse(response.get("__error__"), response)
        self.assertEqual(response["status"], 200)

        data = response["data"]
        self.assertTrue(data["ok"])
        self.assertEqual(data["seq"], 1)
        command = data["command"]
        self.assertIsNotNone(command)
        self.assertEqual(command["zoom"], 2.5)
        self.assertEqual(command["seq"], 1)
        self.assertIsNone(command["action"])
        self.assertIsNone(command["target"])
        self.assertFalse(command["hud"])
        self.assertGreater(command["issuedAt"], 0)
        self.assertGreater(data["updatedAt"], 0)

    def test_get_without_command_does_not_bump_seq(self) -> None:
        first = handle_ui_zoom_debug(_make_request(path="/api/debug/ui-zoom?zoom=2.5"))
        first_seq = first["data"]["seq"]
        self.assertEqual(first_seq, 1)

        # Plain GET with no fields returns the same command and does not bump seq.
        repeat = handle_ui_zoom_debug(_make_request())
        self.assertEqual(repeat["data"]["seq"], first_seq)
        self.assertEqual(repeat["data"]["command"]["zoom"], 2.5)

        # GET that has command fields but only whitespace should still be a plain read.
        third = handle_ui_zoom_debug(_make_request(path="/api/debug/ui-zoom?zoom="))
        self.assertEqual(third["data"]["seq"], first_seq)

    def test_get_two_commands_bump_seq_twice(self) -> None:
        a = handle_ui_zoom_debug(_make_request(path="/api/debug/ui-zoom?zoom=2.5"))
        b = handle_ui_zoom_debug(_make_request(path="/api/debug/ui-zoom?zoom=3.0"))
        self.assertEqual(a["data"]["seq"], 1)
        self.assertEqual(b["data"]["seq"], 2)
        self.assertEqual(b["data"]["command"]["zoom"], 3.0)

    def test_invalid_zoom_returns_400(self) -> None:
        for bad in ("abc", "0.05", "10", "nan", "inf"):
            with self.subTest(zoom=bad):
                _reset_for_tests()
                response = handle_ui_zoom_debug(
                    _make_request(path=f"/api/debug/ui-zoom?zoom={bad}")
                )
                self.assertTrue(response.get("__error__"), response)
                self.assertEqual(response["status"], 400)
                self.assertIn("zoom", response["message"])

    def test_invalid_action_returns_400(self) -> None:
        response = handle_ui_zoom_debug(
            _make_request(path="/api/debug/ui-zoom?action=invalid")
        )
        self.assertTrue(response.get("__error__"))
        self.assertEqual(response["status"], 400)
        self.assertIn("action", response["message"])

    def test_invalid_target_returns_400(self) -> None:
        response = handle_ui_zoom_debug(
            _make_request(path="/api/debug/ui-zoom?target=model-x")
        )
        self.assertTrue(response.get("__error__"))
        self.assertEqual(response["status"], 400)
        self.assertIn("target", response["message"])

    def test_dump_action_does_not_require_zoom(self) -> None:
        response = handle_ui_zoom_debug(
            _make_request(path="/api/debug/ui-zoom?action=dump")
        )
        self.assertFalse(response.get("__error__"), response)
        data = response["data"]
        self.assertEqual(data["seq"], 1)
        self.assertEqual(data["command"]["action"], "dump")
        self.assertIsNone(data["command"]["zoom"])

    def test_open_with_target_session(self) -> None:
        response = handle_ui_zoom_debug(
            _make_request(path="/api/debug/ui-zoom?action=open&target=session")
        )
        data = response["data"]
        self.assertEqual(data["command"]["action"], "open")
        self.assertEqual(data["command"]["target"], "session")

    def test_click_with_selector(self) -> None:
        response = handle_ui_zoom_debug(
            _make_request(
                path="/api/debug/ui-zoom?action=click&selector=.ok-button"
            )
        )
        data = response["data"]
        self.assertEqual(data["command"]["action"], "click")
        self.assertEqual(data["command"]["selector"], ".ok-button")

    def test_hud_query_flag(self) -> None:
        response = handle_ui_zoom_debug(
            _make_request(path="/api/debug/ui-zoom?hud=1")
        )
        data = response["data"]
        self.assertTrue(data["command"]["hud"])

    def test_missing_method_is_treated_as_get(self) -> None:
        # No method attribute at all → GET fallback.
        req = types.SimpleNamespace(
            path="/api/debug/ui-zoom?zoom=1.5", headers={}
        )
        response = handle_ui_zoom_debug(req)
        self.assertFalse(response.get("__error__"))
        self.assertEqual(response["data"]["command"]["zoom"], 1.5)

    # ------------------------------------------------------------------
    # POST: snapshot and command
    # ------------------------------------------------------------------

    def test_get_snapshot_via_body_headers_stores_snapshot(self) -> None:
        snapshot = {"splits": True, "zoom": 1.25}
        response = handle_ui_zoom_debug(
            _make_request(method="GET", body={"snapshot": snapshot})
        )
        self.assertFalse(response.get("__error__"), response)
        data = response["data"]
        self.assertEqual(data["snapshot"], snapshot)
        self.assertIsNone(data["command"])
        self.assertEqual(data["seq"], 0)

    def test_post_snapshot_stores_snapshot(self) -> None:
        snapshot = {"layout": {"x": 1, "y": 2}, "menus": ["file", "edit"]}
        response = handle_ui_zoom_debug(
            _make_request(method="POST", body={"snapshot": snapshot})
        )
        self.assertFalse(response.get("__error__"), response)
        data = response["data"]
        self.assertEqual(data["snapshot"], snapshot)
        self.assertIsNone(data["command"])
        self.assertEqual(data["seq"], 0)  # snapshot alone does not bump seq.

    def test_post_command_queues_command(self) -> None:
        response = handle_ui_zoom_debug(
            _make_request(method="POST", body={"zoom": 1.75, "action": "in"})
        )
        data = response["data"]
        self.assertEqual(data["seq"], 1)
        self.assertEqual(data["command"]["zoom"], 1.75)
        self.assertEqual(data["command"]["action"], "in")

    def test_post_with_invalid_zoom_returns_400(self) -> None:
        response = handle_ui_zoom_debug(
            _make_request(method="POST", body={"zoom": "abc"})
        )
        self.assertTrue(response.get("__error__"))
        self.assertEqual(response["status"], 400)

    def test_post_invalid_json_returns_400(self) -> None:
        encoded = base64.b64encode(b"not-json").decode("ascii")
        req = types.SimpleNamespace(
            method="POST",
            path="/api/debug/ui-zoom",
            headers={f"{_FILE_BODY_PREFIX}0": encoded},
        )
        response = handle_ui_zoom_debug(req)
        self.assertTrue(response.get("__error__"))
        self.assertEqual(response["status"], 400)

    def test_post_snapshot_and_command_together(self) -> None:
        snapshot = {"ok": True}
        response = handle_ui_zoom_debug(
            _make_request(
                method="POST",
                path="/api/debug/ui-zoom?zoom=1.1",
                body={"snapshot": snapshot},
            )
        )
        data = response["data"]
        self.assertEqual(data["snapshot"], snapshot)
        self.assertEqual(data["seq"], 1)
        self.assertEqual(data["command"]["zoom"], 1.1)


class UiZoomDebugBuildHelpersTests(unittest.TestCase):
    def test_build_command_from_query_empty(self) -> None:
        command, err = _build_command_from_query({})
        self.assertIsNone(command)
        self.assertIsNone(err)

    def test_build_command_from_query_zoom_only(self) -> None:
        command, err = _build_command_from_query({"zoom": ["2.5"]})
        self.assertIsNone(err)
        self.assertEqual(command["zoom"], 2.5)

    def test_build_command_from_query_rejects_bad_action(self) -> None:
        command, err = _build_command_from_query({"action": ["nope"]})
        self.assertIsNone(command)
        self.assertIsNotNone(err)

    def test_build_command_from_body_snapshot_only(self) -> None:
        command, err = _build_command_from_body({"snapshot": {"a": 1}})
        self.assertIsNone(command)
        self.assertIsNone(err)


class UiZoomDebugContractTests(unittest.TestCase):
    def test_allowed_actions_match_spec(self) -> None:
        self.assertEqual(
            ALLOWED_ACTIONS,
            frozenset({"in", "out", "reset", "open", "click", "escape", "dump", "outside"}),
        )

    def test_allowed_targets_match_spec(self) -> None:
        self.assertEqual(ALLOWED_TARGETS, frozenset({"session", "effort", "model"}))


if __name__ == "__main__":
    unittest.main()
