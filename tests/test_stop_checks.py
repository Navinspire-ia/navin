"""End-of-turn checks: lint and test what the turn changed (opt-in)."""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest

from navin.agent import stop_checks as sc
from navin.agent import user_hooks
from navin.quality import verify as quality_verify


@pytest.fixture()
def ws(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("NAVIN_STOP_CHECKS", "1")
    monkeypatch.setenv("NAVIN_SECURITY_GUIDANCE", "0")
    monkeypatch.delenv("NAVIN_STOP_CHECKS_TIMEOUT", raising=False)
    sc.STATE.reset()
    workspace = tmp_path / "proj"
    (workspace / ".navin").mkdir(parents=True)
    return workspace


def _report(*, ok=True, lint_errors=0, failed=0, passed=3, unrelated=False):
    outcome = SimpleNamespace(ran=True, passed=passed, failed=failed)
    return SimpleNamespace(
        ok=ok,
        lint_errors=[object()] * lint_errors,
        failed_tests=failed,
        test_outcomes=[outcome],
        render=lambda: "FAIL - tests are failing\n  tests/test_calc.py::test_add",
        failures_look_unrelated=lambda: unrelated,
    )


class _Verify:
    def __init__(self, *reports):
        self.reports = list(reports)
        self.calls: list[list[str]] = []

    def __call__(self, root, paths, **kwargs):
        assert kwargs == {"with_tests": True}
        self.calls.append(list(paths))
        return self.reports.pop(0) if len(self.reports) > 1 else self.reports[0]


def _edit(ws, path="calc.py", content="x = 1\n", tool="write_file", session="s"):
    target = ws / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, "utf-8")
    asyncio.run(user_hooks.post_tool_use_feedback(
        ws, session, tool, {"path": path, "content": content}, "ok",
    ))


def _stop(ws, session="s", subagent=False):
    notices: list[str] = []

    async def notify(text):
        notices.append(text)

    reason = asyncio.run(user_hooks.stop_hook_reason(
        ws, session, "done", stop_hook_active=False, subagent=subagent, notify=notify,
    ))
    return reason, notices


def test_off_by_default(ws, monkeypatch):
    monkeypatch.delenv("NAVIN_STOP_CHECKS")
    fake = _Verify(_report(ok=False, failed=1))
    monkeypatch.setattr(quality_verify, "verify_changes", fake)
    _edit(ws)
    assert _stop(ws) == (None, [])
    assert fake.calls == [] and sc.summary() == ""


def test_green_run_ends_the_turn_with_a_notice(ws, monkeypatch):
    fake = _Verify(_report())
    monkeypatch.setattr(quality_verify, "verify_changes", fake)
    _edit(ws, "pkg/calc.py")
    _edit(ws, "tests/test_calc.py", tool="edit_file")
    reason, notices = _stop(ws)
    assert reason is None
    assert fake.calls == [["pkg/calc.py", "tests/test_calc.py"]]
    assert "passed on 2 changed file(s)" in notices[0] and "3 test(s) passed" in notices[0]
    # Checked files are forgotten: the next turn without edits runs nothing.
    assert _stop(ws) == (None, [])
    assert len(fake.calls) == 1


def test_red_blocks_then_rechecks_after_a_fix(ws, monkeypatch):
    fake = _Verify(_report(ok=False, failed=1, passed=2), _report())
    monkeypatch.setattr(quality_verify, "verify_changes", fake)
    _edit(ws, content="def add(a, b):\n    return a - b\n")
    reason, notices = _stop(ws)
    assert reason and "End-of-turn checks failed" in reason
    assert "tests/test_calc.py::test_add" in reason and "Never weaken" in reason
    assert "1 failing test(s)" in notices[0]
    _edit(ws, content="def add(a, b):\n    return a + b\n")
    reason, notices = _stop(ws)
    assert reason is None and "passed" in notices[0]
    assert len(fake.calls) == 2


def test_same_red_without_edit_does_not_loop(ws, monkeypatch):
    fake = _Verify(_report(ok=False, lint_errors=2))
    monkeypatch.setattr(quality_verify, "verify_changes", fake)
    _edit(ws)
    assert _stop(ws)[0]
    reason, notices = _stop(ws)
    assert reason is None and "not blocking again" in notices[0]
    assert len(fake.calls) == 1


def test_unrelated_failures_never_block_but_lint_errors_do(ws, monkeypatch):
    fake = _Verify(_report(ok=False, failed=2, unrelated=True))
    monkeypatch.setattr(quality_verify, "verify_changes", fake)
    _edit(ws)
    reason, notices = _stop(ws)
    assert reason is None and "likely pre-existing" in notices[0]
    monkeypatch.setattr(
        quality_verify, "verify_changes", _Verify(_report(ok=False, lint_errors=1, failed=2, unrelated=True)),
    )
    _edit(ws, content="y = 2\n")
    assert _stop(ws)[0]


def test_only_project_files_written_by_file_tools_count(ws, monkeypatch, tmp_path):
    fake = _Verify(_report())
    monkeypatch.setattr(quality_verify, "verify_changes", fake)
    asyncio.run(user_hooks.post_tool_use_feedback(ws, "s", "exec", {"command": "echo > a.py"}, "ok"))
    outside = tmp_path / "outside.py"
    outside.write_text("x = 1\n", "utf-8")
    asyncio.run(user_hooks.post_tool_use_feedback(
        ws, "s", "write_file", {"path": str(outside), "content": "x"}, "ok",
    ))
    assert _stop(ws) == (None, [])
    assert fake.calls == []
    (ws / "b.py").write_text("b = 1\n", "utf-8")
    asyncio.run(user_hooks.post_tool_use_feedback(ws, "s", "apply_patch", {"edits": [
        {"path": "b.py", "action": "replace", "new_text": "b = 1"},
        {"path": "gone.py", "action": "replace", "new_text": ""},
    ]}, "ok"))
    _stop(ws)
    assert fake.calls == [["b.py"]]


def test_slow_checks_time_out_without_blocking(ws, monkeypatch):
    monkeypatch.setenv("NAVIN_STOP_CHECKS_TIMEOUT", "0.05")

    def slow(root, paths, **kwargs):
        time.sleep(0.5)
        return _report(ok=False, failed=1)

    monkeypatch.setattr(quality_verify, "verify_changes", slow)
    _edit(ws)
    reason, notices = _stop(ws)
    assert reason is None and "did not finish" in notices[0]


def test_subagents_check_their_own_edits(ws, monkeypatch):
    fake = _Verify(_report(ok=False, failed=1))
    monkeypatch.setattr(quality_verify, "verify_changes", fake)
    _edit(ws, session="sub")
    reason, _ = _stop(ws, session="sub", subagent=True)
    assert reason and fake.calls == [["calc.py"]]
    # Another session's stop does not see those edits.
    assert _stop(ws, session="main") == (None, [])


def test_real_run_reports_the_failing_test(ws):
    (ws / "pyproject.toml").write_text("[project]\nname = 'calc'\nversion = '0'\n", "utf-8")
    _edit(ws, "calc.py", "def add(a, b):\n    return a - b\n")
    _edit(ws, "tests/test_calc.py", "from calc import add\n\n\ndef test_add():\n    assert add(2, 2) == 4\n")
    (ws / "tests" / "conftest.py").write_text(
        "import sys\nfrom pathlib import Path\n\nsys.path.insert(0, str(Path(__file__).parents[1]))\n", "utf-8",
    )
    reason, notices = _stop(ws)
    assert reason and "test_add" in reason, notices
    _edit(ws, "calc.py", "def add(a, b):\n    return a + b\n")
    reason, notices = _stop(ws)
    assert reason is None and "1 test(s) passed" in notices[0], notices


def test_summary_line(ws):
    assert "- end-of-turn checks:" in user_hooks.hooks_summary(ws)
