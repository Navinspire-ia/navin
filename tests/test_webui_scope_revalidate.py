# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Session scopes for WebUI routes: served from memory, re-checked off the loop.

Every desktop HTTP route resolves its session's project scope. On a WSL
project seen from Windows each check is a 9P round trip, and the 3 s review
poll made it on the event loop each time the 5 s cache expired.
"""

from __future__ import annotations

import shutil
import threading
import time
from pathlib import Path
from typing import Any
from unittest import mock

from navin.security.workspace_access import WORKSPACE_SCOPE_METADATA_KEY
from navin.webui import workspaces as ws
from navin.webui.workspaces import WebUIWorkspaceController


class _Sessions:
    def __init__(self) -> None:
        self.metadata: dict[str, dict[str, Any]] = {}

    def read_session_metadata(self, key: str) -> dict[str, Any]:
        return {"metadata": self.metadata.get(key, {})}


def _setup(tmp_path: Path) -> tuple[WebUIWorkspaceController, _Sessions, Path]:
    default = tmp_path / "default"
    default.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    sessions = _Sessions()
    sessions.metadata["websocket:c1"] = {
        WORKSPACE_SCOPE_METADATA_KEY: {"project_path": str(project), "access_mode": "full"},
    }
    controller = WebUIWorkspaceController(
        session_manager=sessions,
        default_workspace=default,
        default_restrict_to_workspace=False,
    )
    return controller, sessions, project


def _age(controller: WebUIWorkspaceController, key: str, seconds: float) -> None:
    at, signature, scope = controller._scope_cache[key]
    controller._scope_cache[key] = (at - seconds, signature, scope)


def _wait_refresh(controller: WebUIWorkspaceController) -> None:
    deadline = time.monotonic() + 5
    while controller._scope_refreshing and time.monotonic() < deadline:
        time.sleep(0.01)


def test_fresh_scope_is_served_from_memory(tmp_path):
    controller, _sessions, project = _setup(tmp_path)
    with mock.patch.object(
        ws, "validate_workspace_scope_payload", wraps=ws.validate_workspace_scope_payload
    ) as validate:
        first = controller.scope_for_session_key("websocket:c1")
        second = controller.scope_for_session_key("websocket:c1")
    assert first.project_path == project.resolve()
    assert second is first
    assert validate.call_count == 1


def test_expired_scope_answers_at_once_and_revalidates_in_a_thread(tmp_path):
    controller, _sessions, _project = _setup(tmp_path)
    first = controller.scope_for_session_key("websocket:c1")
    _age(controller, "websocket:c1", ws._SCOPE_FRESH_S + 1)

    gate = threading.Event()
    calling_threads: list[str] = []
    real = ws.validate_workspace_scope_payload

    def slow_validate(*args: Any, **kwargs: Any):
        calling_threads.append(threading.current_thread().name)
        gate.wait(5)
        return real(*args, **kwargs)

    with mock.patch.object(ws, "validate_workspace_scope_payload", side_effect=slow_validate):
        started = time.monotonic()
        stale = controller.scope_for_session_key("websocket:c1")
        again = controller.scope_for_session_key("websocket:c1")
        assert time.monotonic() - started < 1.0
        gate.set()
        _wait_refresh(controller)

    assert stale is first and again is first
    # One background check for the burst, never on the caller's thread.
    assert calling_threads == ["webui-scope-refresh"]
    refreshed_at = controller._scope_cache["websocket:c1"][0]
    assert time.monotonic() - refreshed_at < ws._SCOPE_FRESH_S


def test_a_deleted_project_falls_back_after_the_background_check(tmp_path):
    controller, _sessions, project = _setup(tmp_path)
    controller.scope_for_session_key("websocket:c1")
    shutil.rmtree(project)
    from navin.security import workspace_access as wa

    wa._PROJECT_DIR_CACHE.clear()
    _age(controller, "websocket:c1", ws._SCOPE_FRESH_S + 1)
    controller.scope_for_session_key("websocket:c1")
    _wait_refresh(controller)
    assert controller.scope_for_session_key("websocket:c1").project_path == (
        tmp_path / "default"
    ).resolve()


def test_a_changed_scope_is_validated_on_the_spot(tmp_path):
    controller, sessions, _project = _setup(tmp_path)
    controller.scope_for_session_key("websocket:c1")
    other = tmp_path / "other"
    other.mkdir()
    sessions.metadata["websocket:c1"] = {
        WORKSPACE_SCOPE_METADATA_KEY: {"project_path": str(other), "access_mode": "full"},
    }
    assert controller.scope_for_session_key("websocket:c1").project_path == other.resolve()
