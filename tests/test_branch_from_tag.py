"""Creating a branch from a tag is one git call, and the checked-out tree is not
counted as the agent's edits (no test run demanded for code it did not write)."""

from __future__ import annotations

import asyncio
import subprocess

from navin.agent import code_validation as cv
from navin.agent.tools.git import GitTool


def _git(root, *args):
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True).stdout.strip()


def _repo(tmp_path):
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "t@t")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "app.py").write_text("V = 1\n")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "v1")
    _git(tmp_path, "tag", "release-1.0")
    (tmp_path / "app.py").write_text("V = 2\n")
    _git(tmp_path, "commit", "-qam", "v2")
    return _git(tmp_path, "rev-parse", "release-1.0")


def test_switch_create_starts_the_branch_at_the_tag(tmp_path):
    tagged = _repo(tmp_path)
    tool = GitTool(workspace=tmp_path)
    result = asyncio.run(tool.execute(action="switch", name="preprod", create=True, commit="release-1.0"))
    assert "Error" not in str(result)
    assert _git(tmp_path, "rev-parse", "HEAD") == tagged
    assert _git(tmp_path, "branch", "--show-current") == "preprod"
    assert (tmp_path / "app.py").read_text() == "V = 1\n"


def test_commit_without_create_is_refused(tmp_path):
    _repo(tmp_path)
    result = asyncio.run(GitTool(workspace=tmp_path).execute(action="switch", name="main", commit="release-1.0"))
    assert "create=true" in str(result)


def test_tree_moves_are_recognised():
    assert cv.moves_worktree("git", {"action": "switch", "name": "x"})
    assert cv.moves_worktree("exec", {"command": "git checkout -b preprod lynara-saas-v1.2.5"})
    assert cv.moves_worktree("exec", {"command": "cd repo && git reset --hard v1"})
    assert not cv.moves_worktree("exec", {"command": "git status"})
    assert not cv.moves_worktree("git", {"action": "commit"})


def test_a_checked_out_tree_is_not_counted_as_edits(tmp_path):
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    (tmp_path / "test_x.py").write_text("def test_x():\n    assert True\n")
    (tmp_path / "app.py").write_text("V = 1\n")
    state = cv.CodeValidationState(root=tmp_path)
    state.observe_workspace(cv.workspace_code_snapshot(tmp_path), root=tmp_path)
    (tmp_path / "app.py").write_text("V = 2  # the other branch\n")
    state.rebaseline(cv.workspace_code_snapshot(tmp_path), root=tmp_path)
    assert state.pending is False and state.revision == 0
    (tmp_path / "app.py").write_text("V = 3\n")  # a real edit afterwards still counts
    state.observe_workspace(cv.workspace_code_snapshot(tmp_path), root=tmp_path)
    assert state.pending is True
