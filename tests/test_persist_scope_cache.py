# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""persist_scope runs on every chat message: repeat calls must stay cheap."""

from __future__ import annotations

import asyncio
import shutil
from unittest import mock

from navin.security.workspace_access import WORKSPACE_SCOPE_METADATA_KEY
from navin.session.manager import SessionManager
from navin.utils import helpers
from navin.webui.workspaces import WebUIWorkspaceController


def _controller(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "main.py").write_text("print(1)\n", encoding="utf-8")
    sessions = SessionManager(tmp_path / "ws")
    controller = WebUIWorkspaceController(
        session_manager=sessions, default_workspace=root, default_restrict_to_workspace=False,
    )
    return controller, sessions, root


def test_scaffold_runs_once_per_workspace_and_again_when_removed(tmp_path):
    controller, _sessions, root = _controller(tmp_path)
    scope = controller.default_scope()
    with mock.patch.object(
        helpers, "ensure_project_scaffold", wraps=helpers.ensure_project_scaffold
    ) as scaffold:
        for _ in range(5):
            asyncio.run(controller.persist_scope_async("c1", scope))
        assert scaffold.call_count == 1
        assert (root / ".navin").is_dir()
        shutil.rmtree(root / ".navin")
        controller.persist_scope("c1", scope)
        assert scaffold.call_count == 2
        assert (root / ".navin").is_dir()


def test_unchanged_scope_is_not_saved_again(tmp_path):
    controller, sessions, _root = _controller(tmp_path)
    scope = controller.default_scope()
    with mock.patch.object(sessions, "save_async", wraps=sessions.save_async) as save_async:
        asyncio.run(controller.persist_scope_async("c1", scope))
        asyncio.run(controller.persist_scope_async("c1", scope))
    assert save_async.call_count == 1
    session = sessions.get_or_create("websocket:c1")
    assert session.metadata["webui"] is True
    assert session.metadata[WORKSPACE_SCOPE_METADATA_KEY] == scope.metadata()
