# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""A .navin-session can be uploaded from the browser, not only picked by path."""

from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path
from unittest.mock import patch

from navin.session.bundle import export_session_bundle
from navin.session.manager import SessionManager
from navin.webui.ws_http import GatewayHTTPHandler


def _headers(data: bytes) -> dict[str, str]:
    b64 = base64.b64encode(data).decode()
    out = {"x-navin-file-body-0": ""}
    for index, start in enumerate(range(0, len(b64), 6000)):
        out[f"x-navin-file-body-{index}"] = b64[start:start + 6000]
    return out


class _Request:
    def __init__(self, path: str, headers: dict[str, str] | None = None) -> None:
        self.path = path
        self.headers = headers or {}


def _handler(sessions_root: Path) -> GatewayHTTPHandler:
    handler = object.__new__(GatewayHTTPHandler)
    handler.check_api_token = lambda request: True
    handler.workspace_controls_available = lambda connection: True
    handler.session_manager = SessionManager(sessions_root)
    return handler


def test_uploaded_slices_rebuild_the_session(tmp_path):
    src = tmp_path / "project"
    src.mkdir()
    (src / "main.py").write_text("print('ok')\n" + "#" * 300_000)
    manager = SessionManager(src)
    session = manager.get_or_create("websocket:chat-1")
    session.add_message("user", "hello")
    manager.save(session)
    bundle = export_session_bundle(src, "websocket:chat-1", tmp_path / "shared.navin-session").path.read_bytes()

    handler = _handler(tmp_path / "home")
    with patch("navin.config.paths.get_runtime_subdir", lambda name: tmp_path / "rt" / name):
        upload = "a1b2c3d4e5f60718"
        slices = [bundle[i:i + 100_000] for i in range(0, len(bundle), 100_000)]
        for index, piece in enumerate(slices):
            response = asyncio.run(handler._handle_webui_session_bundle(
                None, _Request(f"/api/webui/sessions/bundle/upload?upload_id={upload}&index={index}", _headers(piece)), "upload"))
            assert response.status_code == 200, response.body
        into = tmp_path / "opened"
        response = asyncio.run(handler._handle_webui_session_bundle(
            None, _Request(f"/api/webui/sessions/bundle/open?upload_id={upload}&into={into}"), "open"))
        assert response.status_code == 200, response.body
        payload = json.loads(response.body)
        assert payload["chat_id"]
        assert (into / "main.py").read_text().startswith("print('ok')")
        # The reassembled upload does not linger.
        assert not (tmp_path / "rt" / "tmp" / "bundle-uploads" / f"{upload}.navin-session").exists()


def test_out_of_order_slices_are_refused(tmp_path):
    handler = _handler(tmp_path / "home")
    with patch("navin.config.paths.get_runtime_subdir", lambda name: tmp_path / "rt" / name):
        response = asyncio.run(handler._handle_webui_session_bundle(
            None, _Request("/api/webui/sessions/bundle/upload?upload_id=ffffffffffffffff&index=3", _headers(b"x")), "upload"))
    assert response.status_code == 400
