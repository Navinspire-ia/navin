# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Direct desk HTTP requests load skills from the selected chat's project."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from navin.agent.skill_routing import build_action_skill_context
from navin.agent.tools.context import current_request_context
from navin.webui.ws_http import GatewayHTTPHandler


def _request(path: str, body: dict) -> SimpleNamespace:
    encoded = base64.b64encode(json.dumps(body).encode()).decode()
    return SimpleNamespace(path=path, headers={"x-navin-file-body-0": encoded})


def _handler(project: Path, default: Path) -> GatewayHTTPHandler:
    handler = object.__new__(GatewayHTTPHandler)
    handler.check_api_token = lambda request: True
    handler.disabled_skills = {"proofreader"}
    handler._log = Mock()
    handler.workspaces = SimpleNamespace(
        scope_for_session_key=Mock(return_value=SimpleNamespace(project_path=project)),
        default_scope=Mock(return_value=SimpleNamespace(project_path=default)),
    )
    return handler


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("module", "method", "target", "action", "skill"),
    [
        ("tenders", "_handle_tenders", "navin.webui.tenders_api.handle_tenders_action", "write", "rfp-writer"),
        ("career", "_handle_career", "navin.webui.career_api.handle_career_action", "prepare", "cv-tailoring"),
        ("marketing", "_handle_marketing_desk", "navin.webui.marketing_desk_api.handle_marketing_action", "content", "copywriting-agent"),
    ],
)
async def test_direct_action_reads_the_project_skill_body(tmp_path, module, method, target, action, skill):
    project, default = tmp_path / "selected", tmp_path / "default"
    skill_path = project / ".navin" / "skills" / skill / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text(f"---\nname: {skill}\ndescription: Project drafting rules\n---\nUse the PROJECT_SOURCE_SENTINEL.\n")
    handler = _handler(project, default)
    previous = current_request_context()

    def run(actual_action, body):
        assert actual_action == action
        assert current_request_context().session_key == "websocket:quality"
        route = build_action_skill_context(module, action)
        assert "PROJECT_SOURCE_SENTINEL" in route.prompt
        assert str(project) == route.metadata["workspace"]
        assert "proofreader" not in route.loaded
        return {"generation": route.metadata}

    with patch(target, side_effect=run):
        response = await getattr(handler, method)(
            _request(f"/api/{module}?action={action}&session_key=websocket%3Aquality", {"id": "local-record"})
        )
    assert response.status_code == 200, response.body
    assert skill in json.loads(response.body)["generation"]["loaded"]
    handler.workspaces.scope_for_session_key.assert_called_once_with("websocket:quality")
    assert current_request_context() is previous


@pytest.mark.asyncio
async def test_invalid_desk_session_does_not_run_the_action(tmp_path):
    handler = _handler(tmp_path, tmp_path)
    with patch("navin.webui.career_api.handle_career_action") as run:
        response = await handler._handle_career(_request("/api/career?action=prepare&session_key=telegram%3Aforeign", {}))
    assert response.status_code == 400
    run.assert_not_called()


@pytest.mark.asyncio
async def test_meeting_call_keeps_and_restores_its_project_context(tmp_path):
    handler = _handler(tmp_path / "selected", tmp_path / "default")
    previous = current_request_context()

    async def report(**kwargs):
        context = current_request_context()
        assert context.workspace == tmp_path / "selected"
        assert context.metadata["action"] == "report"
        assert context.session_key == "websocket:quality"
        return {"report": "Compte rendu", "model": "test"}

    with patch("navin.webui.ws_http.meeting_report_payload", new=AsyncMock(side_effect=report)):
        response = await handler._handle_meeting(
            _request("/api/meeting?mode=report", {"transcript": "Camille: Rendez-vous vendredi."}),
            "websocket%3Aquality",
        )
    assert response.status_code == 200, response.body
    assert current_request_context() is previous
