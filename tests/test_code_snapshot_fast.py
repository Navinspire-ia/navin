"""The workspace snapshot around exec is cheaper but sees exactly the same files,
and commands that clearly only read skip it."""

from __future__ import annotations

import subprocess
from pathlib import Path, PurePosixPath
from types import SimpleNamespace

import pytest

from navin.agent import code_validation as cv
from navin.agent.runner import _may_edit_workspace

REPO = Path(__file__).resolve().parents[1]


def _reference_snapshot(root: Path) -> dict:
    result = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", "."],
        cwd=root, capture_output=True, check=True,
    )
    out = {}
    for raw in (p.decode() for p in result.stdout.split(b"\0") if p):
        path = raw.replace("\\", "/")
        if cv.development_path(path) and not cv._GENERATED_DIRS.intersection(PurePosixPath(path).parts):
            out[path] = cv._file_stamp(root / raw)
    return out


def test_fast_snapshot_matches_the_reference_on_this_repo():
    assert cv.workspace_code_snapshot(REPO) == _reference_snapshot(REPO)


@pytest.mark.parametrize("name", ["a.", ".bashrc", "a.b.", "x.tar.gz", "a..py", ".env.local", "Dockerfile",
                                  "src/App.TSX", "node_modules/x/index.js", "dist/a.js", "Makefile", "x.py"])
def test_development_file_rule_matches_the_path_rule(name):
    expected = cv.development_path(name) and not cv._GENERATED_DIRS.intersection(PurePosixPath(name).parts)
    assert cv._is_development_file(name) == bool(expected)


@pytest.mark.parametrize("command", [
    "ls -la", "cat src/a.py | head -20", "git status --short", "git diff HEAD~1 -- src",
    "cd webui && ls", "grep -rn foo src | wc -l", "find . -name '*.py'", "sed -n 1,40p a.py",
    "node --version", "rg 'def main' -n",
])
def test_read_only_commands_skip_the_snapshot(command):
    assert cv.exec_cannot_edit({"command": command}) is True


@pytest.mark.parametrize("command", [
    "echo hi > a.txt", "cat a | tee b", "npm test", "python -m pytest -q", "sed -i s/a/b/ x.py",
    "find . -name '*.pyc' -delete", "ls; rm -rf build", "git checkout main", "ls $(pwd)",
    "npm run dev &", "git diff --output=x.patch", "make", "FOO=1 ls", "ls || touch x",
])
def test_anything_else_keeps_the_snapshot(command):
    assert cv.exec_cannot_edit({"command": command}) is False


def test_background_exec_keeps_the_snapshot():
    assert cv.exec_cannot_edit({"command": "ls", "background": True}) is False


def test_runner_watches_editing_calls_only():
    call = lambda name, **arguments: SimpleNamespace(name=name, arguments=arguments)  # noqa: E731
    assert _may_edit_workspace(call("exec", command="npm run build")) is True
    assert _may_edit_workspace(call("exec", command="git status")) is False
    assert _may_edit_workspace(call("project", action="format")) is True
    assert _may_edit_workspace(call("project", action="status")) is False
    assert _may_edit_workspace(call("read_file", path="a.py")) is False


def test_no_test_suite_means_a_passing_check_is_enough(tmp_path):
    (tmp_path / "hello.py").write_text("print('hi')\n")
    state = cv.CodeValidationState(root=tmp_path)
    state.edited({"hello.py"}, require_tests=True)
    assert state.needs_tests is False
    assert "verify is enough" in state.missing()
    state.record(cv.VerificationEvidence(checks_ok=True, summary="ok"))
    assert state.pending is False


def test_a_project_with_tests_still_needs_them(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[tool.pytest.ini_options]\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_app.py").write_text("def test_ok():\n    assert True\n")
    state = cv.CodeValidationState(root=tmp_path)
    state.edited({"app.py"}, require_tests=True)
    assert state.needs_tests is True
