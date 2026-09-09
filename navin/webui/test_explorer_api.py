# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""HTTP payloads for the IDE Test Explorer panel.

Two modes over one endpoint: ``collect`` lists suites and their individual
tests (via the same runner table the agent's ``test_run`` tool uses, so a
test shown here runs with identical arguments), and ``run`` executes a
suite or a single target and returns the parsed outcome - pass/fail/skip
counts plus structured failures, never a raw log dump.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from navin.security.workspace_access import WorkspaceScope

_KNOWN_RUNNERS = frozenset(
    {"pytest", "vitest", "jest", "go_test", "cargo_test", "unittest"}
)


class TestExplorerError(ValueError):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def _project_root(scope: WorkspaceScope) -> Path:
    raw = getattr(scope, "project_path", None)
    # WorkspaceScope.project_path is a Path; older callers pass a string.
    # Path.strip does not exist, and that AttributeError used to 500 Collect.
    if raw is None:
        raise TestExplorerError(400, "no project folder for this session")
    text = str(raw).strip()
    if not text:
        raise TestExplorerError(400, "no project folder for this session")
    path = Path(text).expanduser()
    if not path.is_dir():
        raise TestExplorerError(400, "no project folder for this session")
    return path.resolve()


def collect_payload(scope: WorkspaceScope) -> dict[str, Any]:
    from navin.quality.test_explorer import collect_tests

    root = _project_root(scope)
    try:
        return collect_tests(root)
    except Exception as exc:
        return {"root": str(root), "suites": [], "error": str(exc)}


def run_payload(
    scope: WorkspaceScope,
    *,
    runner: str,
    target: str | None = None,
) -> dict[str, Any]:
    from navin.quality.testing import run_tests

    root = _project_root(scope)
    name = (runner or "").strip()
    if name not in _KNOWN_RUNNERS:
        raise TestExplorerError(400, f"unknown runner: {name!r}")
    clean_target = (target or "").strip() or None
    if clean_target and ("\x00" in clean_target or len(clean_target) > 2000):
        raise TestExplorerError(400, "invalid target")
    outcomes = run_tests(root, runners=[name], target=clean_target)
    return {
        "root": str(root),
        "runner": name,
        "target": clean_target,
        "outcomes": [outcome.to_json() for outcome in outcomes],
    }
