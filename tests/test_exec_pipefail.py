# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""`failing | tail` must not report the filter's exit code 0."""

from __future__ import annotations

import asyncio

import pytest

from navin.agent.tools.shell import ExecTool, _wants_pipefail


@pytest.mark.parametrize(("command", "expected"), [
    ("python -m pytest tests/ -q 2>&1 | tail -20", True),
    ("cd backend && npm test 2>&1 | tee out.log", True),
    ("pytest -q | grep -E 'passed|failed'", True),
    ("ps aux | grep -q node", False),
    ("npm run build | head -5", False),
    ("npm run build", False),
    ("a || b", False),
    ("set -o pipefail; x | tail", False),
])
def test_when_pipefail_is_set(command: str, expected: bool) -> None:
    assert _wants_pipefail(command) is expected


def test_failing_command_piped_to_tail_reports_failure(tmp_path) -> None:
    tool = ExecTool(timeout=30, working_dir=str(tmp_path))
    out = str(asyncio.run(tool.execute(command="(echo boom; exit 3) 2>&1 | tail -5", working_dir=str(tmp_path))))
    assert "boom" in out and "Exit code: 3" in out
    ok = str(asyncio.run(tool.execute(command="echo fine | tail -1", working_dir=str(tmp_path))))
    assert "Exit code: 0" in ok or "Exit code" not in ok


def test_failed_command_gets_a_likely_cause(tmp_path) -> None:
    tool = ExecTool(timeout=30, working_dir=str(tmp_path))
    out = str(asyncio.run(tool.execute(
        command="echo 'error[E0432]: unresolved import `serde`' >&2; exit 101", working_dir=str(tmp_path),
    )))
    assert "Exit code: 101" in out and "Likely cause:" in out and "Rust crate" in out
    ok = str(asyncio.run(tool.execute(command="echo 'mentions ModuleNotFoundError in a log'", working_dir=str(tmp_path))))
    assert "Likely cause" not in ok  # only failures get a diagnosis
