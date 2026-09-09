# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for the Cursor / VS Code migration importer."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from navin.config.loader import load_config, save_config
from navin.config.schema import Config
from navin.webui.migration_api import (
    import_copilot_instructions,
    import_mcp_servers,
    run_import,
    scan_migration_sources,
)


@pytest.fixture()
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    cursor_dir = tmp_path / "cursor-home"
    cursor_dir.mkdir()
    monkeypatch.setenv("NAVIN_CURSOR_HOME", str(cursor_dir))
    project = tmp_path / "proj"
    project.mkdir()
    config_path = tmp_path / "config.json"
    save_config(Config(), config_path)
    return cursor_dir, project, config_path


def _write_mcp(path: Path, servers: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"mcpServers": servers}), encoding="utf-8")


def test_scan_reports_all_sources(env) -> None:
    cursor_dir, project, _config = env
    _write_mcp(cursor_dir / "mcp.json", {"github": {"command": "npx"}})
    _write_mcp(project / ".cursor" / "mcp.json", {"linear": {"url": "https://x/mcp"}})
    (project / ".cursorrules").write_text("be nice", encoding="utf-8")
    rules_dir = project / ".cursor" / "rules"
    rules_dir.mkdir(parents=True)
    (rules_dir / "style.mdc").write_text("---\nalwaysApply: true\n---\nrule", encoding="utf-8")
    gh = project / ".github"
    gh.mkdir()
    (gh / "copilot-instructions.md").write_text("use tabs", encoding="utf-8")
    (project / ".vscode").mkdir()
    (project / ".vscode" / "settings.json").write_text("{}", encoding="utf-8")

    scan = scan_migration_sources(project)
    origins = {row["origin"]: row["servers"] for row in scan["mcp"]}
    assert origins == {"global": ["github"], "project": ["linear"]}
    assert ".cursorrules" in scan["nativeRules"]
    assert ".cursor/rules/style.mdc" in scan["nativeRules"]
    assert scan["copilotInstructions"] is not None
    assert scan["vscodeSettings"] is not None


def test_scan_empty_project(env) -> None:
    _cursor, project, _config = env
    scan = scan_migration_sources(project)
    assert scan == {
        "mcp": [],
        "nativeRules": [],
        "copilotInstructions": None,
        "vscodeSettings": None,
    }


def test_import_mcp_merges_without_overwriting(env) -> None:
    cursor_dir, project, config_path = env
    # Pre-existing server in Navin config must win.
    config = load_config(config_path)
    from navin.config.schema import MCPServerConfig

    config.tools.mcp_servers = {
        "github": MCPServerConfig(command="existing-cmd")
    }
    save_config(config, config_path)

    _write_mcp(
        cursor_dir / "mcp.json",
        {
            "github": {"command": "npx", "args": ["-y", "gh-mcp"]},
            "sentry": {"url": "https://sentry/mcp", "headers": {"A": "b"}},
        },
    )
    _write_mcp(
        project / ".cursor" / "mcp.json",
        {
            "local-db": {
                "command": "uvx",
                "args": ["db-mcp"],
                "env": {"DB_URL": "postgres://x"},
            },
            "broken": "not a dict",
        },
    )

    report = import_mcp_servers(project, config_path=config_path)
    assert sorted(report["imported"]) == ["local-db", "sentry"]
    assert report["skipped"] == ["github"]
    assert report["invalid"] == ["broken"]

    saved = load_config(config_path)
    assert saved.tools.mcp_servers["github"].command == "existing-cmd"
    assert saved.tools.mcp_servers["sentry"].url == "https://sentry/mcp"
    assert saved.tools.mcp_servers["local-db"].env == {"DB_URL": "postgres://x"}


def test_import_mcp_ignores_unknown_fields(env) -> None:
    cursor_dir, project, config_path = env
    _write_mcp(
        cursor_dir / "mcp.json",
        {"fancy": {"command": "npx", "someCursorOnlyField": {"x": 1}}},
    )
    report = import_mcp_servers(project, config_path=config_path)
    assert report["imported"] == ["fancy"]
    assert load_config(config_path).tools.mcp_servers["fancy"].command == "npx"


def test_import_mcp_no_sources_is_noop(env) -> None:
    _cursor, project, config_path = env
    report = import_mcp_servers(project, config_path=config_path)
    assert report == {"imported": [], "skipped": [], "invalid": []}


def test_import_copilot_instructions(env) -> None:
    _cursor, project, _config = env
    gh = project / ".github"
    gh.mkdir()
    (gh / "copilot-instructions.md").write_text(
        "Always write tests.", encoding="utf-8"
    )
    report = import_copilot_instructions(project)
    assert report["imported"] is True
    rule = project / ".navin" / "rules" / "copilot-instructions.md"
    assert rule.is_file()
    assert "Always write tests." in rule.read_text(encoding="utf-8")

    # Second run: already imported, never overwrites.
    rule.write_text("user edited", encoding="utf-8")
    again = import_copilot_instructions(project)
    assert again["imported"] is False
    assert rule.read_text(encoding="utf-8") == "user edited"


def test_import_copilot_missing_or_empty(env) -> None:
    _cursor, project, _config = env
    assert import_copilot_instructions(project)["reason"] == "not found"
    gh = project / ".github"
    gh.mkdir()
    (gh / "copilot-instructions.md").write_text("  \n", encoding="utf-8")
    assert import_copilot_instructions(project)["reason"] == "empty"


def test_run_import_combined(env) -> None:
    cursor_dir, project, config_path = env
    _write_mcp(cursor_dir / "mcp.json", {"tool": {"command": "npx"}})
    gh = project / ".github"
    gh.mkdir()
    (gh / "copilot-instructions.md").write_text("rules!", encoding="utf-8")
    report = run_import(project, config_path=config_path)
    assert report["mcp"]["imported"] == ["tool"]
    assert report["copilot"]["imported"] is True
