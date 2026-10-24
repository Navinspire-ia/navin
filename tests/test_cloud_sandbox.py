# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Cloud execution (AWS AgentCore via the Navin site): opt-in, explicit, faithful output."""

from __future__ import annotations

import asyncio
import base64
from unittest.mock import AsyncMock, patch

import pytest

from navin.agent.tools import cloud_sandbox
from navin.agent.tools.cloud_sandbox import (
    SandboxFilesTool,
    cloud_target_active,
    format_exec_result,
)
from navin.config.loader import load_config, save_config
from navin.config.schema import Config
from navin.webui.settings_api import WebUISettingsError, update_agent_settings


@pytest.fixture
def config():
    cfg = Config()
    cfg.license.device = "dev-1234567890abcdef"
    save_config(cfg)
    return cfg


def _no_payload(**kwargs):
    return {"execution": load_config().tools.execution.model_dump(), **kwargs}


def test_local_is_the_default_and_aws_needs_acceptance_then_a_license(config):
    assert load_config().tools.execution.target == "local"
    assert cloud_target_active() is False
    with patch("navin.webui.settings_api.settings_payload", side_effect=_no_payload):
        with pytest.raises(WebUISettingsError):
            update_agent_settings({"execution_target": ["aws"]})
        update_agent_settings({"execution_aws_accepted": ["true"]})
        result = update_agent_settings({"execution_target": ["aws"]})
    assert result["requires_restart"] is True
    assert cloud_target_active() is True

    # Withdrawing consent always brings execution back to this computer.
    with patch("navin.webui.settings_api.settings_payload", side_effect=_no_payload):
        update_agent_settings({"execution_aws_accepted": ["false"]})
    assert load_config().tools.execution.target == "local"
    assert cloud_target_active() is False


def test_aws_is_refused_without_a_signed_in_device():
    cfg = Config()
    cfg.tools.execution.aws_accepted = True
    save_config(cfg)
    with patch("navin.webui.settings_api.settings_payload", side_effect=_no_payload), pytest.raises(WebUISettingsError):
        update_agent_settings({"execution_target": ["imaginary"]})
    with patch("navin.webui.settings_api.settings_payload", side_effect=_no_payload), pytest.raises(WebUISettingsError):
        update_agent_settings({"execution_target": ["aws"]})


def test_cloud_output_reads_like_local_exec():
    text = format_exec_result({"stdout": "ok\n", "stderr": "warn", "exitCode": 2})
    assert text.startswith("ok\nSTDERR:\nwarn\nExit code: 2")
    assert "cloud sandbox" in text
    assert "Exit code: 1" in format_exec_result({"isError": True})


def test_exec_runs_in_the_cloud_when_aws_is_chosen(config, tmp_path):
    cfg = load_config()
    cfg.tools.execution.target = "aws"
    cfg.tools.execution.aws_accepted = True
    save_config(cfg)
    from navin.agent.tools.shell import ExecTool

    tool = ExecTool(working_dir=str(tmp_path))
    calls = AsyncMock(return_value={"stdout": "hello from aws\n", "exitCode": 0})
    with patch.object(cloud_sandbox, "sandbox_call", calls):
        out = asyncio.run(tool.execute(command="echo hello"))
    assert "hello from aws" in out and "Exit code: 0" in out
    assert calls.await_args.args[0] == "code"
    assert calls.await_args.args[1] == {"op": "command", "command": "echo hello"}


def test_files_go_up_and_come_back_explicitly_inside_the_workspace(tmp_path):
    (tmp_path / "data.csv").write_text("a,b\n1,2\n")
    tool = SandboxFilesTool(workspace=str(tmp_path))
    sent: list[dict] = []

    async def fake_call(action, payload=None, **_):
        sent.append(payload)
        if payload["op"] == "read":
            return {"content": [{"uri": "file:///out/report.txt", "text": "done"}]}
        return {}

    with patch.object(cloud_sandbox, "sandbox_call", fake_call):
        up = asyncio.run(tool.execute(action="upload", paths=["data.csv"]))
        down = asyncio.run(tool.execute(action="download", paths=["out/report.txt"]))
        escape = asyncio.run(tool.execute(action="upload", paths=["../secret.txt"]))
    assert "data.csv" in up
    assert base64.b64decode(sent[0]["files"][0]["base64"]) == b"a,b\n1,2\n"
    assert (tmp_path / "out" / "report.txt").read_text() == "done" and "out/report.txt" in down
    assert escape.startswith("Error:") and "outside the workspace" in escape


def test_the_answer_is_kept_until_the_config_file_changes(config, monkeypatch):
    import navin.agent.tools.cloud_sandbox as cloud

    loads = []
    real = cloud._full_config
    monkeypatch.setattr(cloud, "_full_config", lambda: loads.append(1) or real())
    cloud._CLOUD_TARGET_CACHE.clear()
    assert [cloud_target_active() for _ in range(5)] == [False] * 5
    assert len(loads) == 1
    cfg = load_config()
    cfg.tools.execution.aws_accepted = True
    cfg.tools.execution.target = "aws"
    save_config(cfg)
    assert cloud_target_active() is True
    assert len(loads) == 2
