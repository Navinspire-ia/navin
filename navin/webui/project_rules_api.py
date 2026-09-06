"""HTTP helpers for editable ``.navin/rules/*.md`` project rules."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from navin.agent.project_rules import list_navin_rules, write_navin_rule
from navin.security.workspace_access import WorkspaceScope
from navin.utils.helpers import ensure_project_scaffold


class ProjectRulesError(ValueError):
    def __init__(self, message: str, *, status: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


def project_rules_payload(scope: WorkspaceScope) -> dict[str, Any]:
    root = Path(scope.project_path).expanduser()
    if not root.is_dir():
        raise ProjectRulesError(f"project not found: {root}", status=404)
    ensure_project_scaffold(root, silent=True)
    rules = list_navin_rules(root)
    return {
        "project_path": str(root),
        "rules_dir": ".navin/rules",
        "rules": rules,
        "total": len(rules),
    }


def save_project_rule_payload(
    scope: WorkspaceScope,
    *,
    name: str,
    content: str,
) -> dict[str, Any]:
    root = Path(scope.project_path).expanduser()
    if not root.is_dir():
        raise ProjectRulesError(f"project not found: {root}", status=404)
    ensure_project_scaffold(root, silent=True)
    try:
        rule = write_navin_rule(root, name=name, content=content)
    except ValueError as exc:
        raise ProjectRulesError(str(exc), status=400) from exc
    return {"saved": True, "rule": rule}
