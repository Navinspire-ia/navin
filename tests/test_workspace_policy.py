import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

from navin.agent import workspace_policy as wp


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _repo(tmp_path: Path):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "a@b.c")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "a.py").write_text("x\n")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "init")
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=tmp_path, capture_output=True, text=True,
    ).stdout.strip()


def test_path_rules():
    p = wp.WorkspacePolicy(allowed_paths=("src/**", "tests/*.py"), denied_paths=("src/secret/**",))
    assert p.path_allowed("src/a/b.py")
    assert p.path_allowed("tests/t.py")
    assert not p.path_allowed("src/secret/k")
    assert not p.path_allowed("docs/a.md")
    assert not p.path_allowed("../x")
    assert not p.path_allowed("/etc/passwd")
    assert wp.WorkspacePolicy().path_allowed("anything")


def test_command_rules():
    p = wp.WorkspacePolicy(allowed_commands=("pytest", "ruff check"))
    assert p.command_allowed("pytest -q tests/x.py")
    assert p.command_allowed("ruff check .")
    assert not p.command_allowed("ruff format .")
    assert not p.command_allowed("pytest; rm -rf /")
    assert wp.WorkspacePolicy().command_allowed("whatever")


def test_detect_conflicts_report():
    report = wp.detect_conflicts({"t1": ["a", "b"], "t2": ["b", "c"], "t3": ["c"]})
    assert report.overlaps == {"b": ["t1", "t2"], "c": ["t2", "t3"]}
    assert "b: t1, t2" in report.render()
    assert not wp.detect_conflicts({"t1": ["a"], "t2": ["b"]}).has_conflicts


def test_gate_blocks_outside_path(tmp_path):
    base = _repo(tmp_path)
    (tmp_path / "other.txt").write_text("n")
    wp.set_policy("t", wp.WorkspacePolicy(allowed_paths=("*.py",)))
    note = wp.pre_merge_gate(SimpleNamespace(path=tmp_path, base=base), "t")
    wp.clear_policy()
    assert note and "other.txt" in note


def test_gate_runs_tests(tmp_path):
    base = _repo(tmp_path)
    (tmp_path / "a.py").write_text("y\n")
    co = SimpleNamespace(path=tmp_path, base=base)
    ok = f"{sys.executable} -c pass"
    bad = f"{sys.executable} -c 'import sys; sys.exit(1)'"
    wp.set_policy("ok", wp.WorkspacePolicy(test_commands=(ok,)))
    wp.set_policy("bad", wp.WorkspacePolicy(test_commands=(bad,)))
    assert wp.pre_merge_gate(co, "ok") is None
    assert "Test failed" in wp.pre_merge_gate(co, "bad")
    assert wp.pre_merge_gate(co, "none") is None
    wp.clear_policy()


def test_gate_crash_blocks_merge(tmp_path, monkeypatch):
    co = SimpleNamespace(path=tmp_path, base="HEAD")
    assert wp.guarded_pre_merge_gate(co, "absent") is None

    def boom(*_args, **_kwargs):
        raise OSError("disk gone")

    monkeypatch.setattr(wp, "pre_merge_gate", boom)
    note = wp.guarded_pre_merge_gate(co, "t")
    assert note and "OSError: disk gone" in note and "not merged" in note


def test_conflicts_for_real_checkouts(tmp_path):
    base = _repo(tmp_path)
    (tmp_path / "a.py").write_text("z\n")
    co = SimpleNamespace(path=tmp_path, base=base)
    assert wp.conflicts_for({"t1": co, "t2": co}).overlaps == {"a.py": ["t1", "t2"]}


def _spawn_call(manager, **kwargs):
    import asyncio
    from unittest import mock

    from navin.agent.tools.context import (
        RequestContext,
        bind_request_context,
        reset_request_context,
    )
    from navin.agent.tools.spawn import SpawnTool

    async def run():
        token = bind_request_context(RequestContext(
            channel="cli", chat_id="x", message_id=None, session_key="cli:x",
            runtime=mock.Mock(), metadata={},
        ))
        try:
            return await SpawnTool(manager).execute(**kwargs)
        finally:
            reset_request_context(token)

    return asyncio.run(run())


class _RecordingManager:
    def __init__(self):
        self.calls = []

    async def spawn(self, **kwargs):
        self.calls.append(kwargs)
        task_id = "pol1"
        spec = kwargs.get("workspace_policy_spec")
        if spec:
            wp.set_policy(task_id, wp.policy_from_dict(spec))
        return f"started {task_id}"


def test_spawn_policy_requires_isolate():
    manager = _RecordingManager()
    out = _spawn_call(manager, task="t", allowed_paths="a.py")
    assert "isolate=true" in str(out)
    assert manager.calls == []


def test_spawn_with_allowed_paths_blocks_merge(tmp_path):
    base = _repo(tmp_path)
    manager = _RecordingManager()
    _spawn_call(
        manager, task="t", isolate=True, allowed_paths="a.py, src/**",
        test_commands="echo one;;echo two",
    )
    spec = manager.calls[0]["workspace_policy_spec"]
    assert spec["allowed_paths"] == ["a.py", "src/**"]
    assert spec["test_commands"] == ["echo one", "echo two"]
    try:
        (tmp_path / "outside.txt").write_text("n")
        note = wp.guarded_pre_merge_gate(SimpleNamespace(path=tmp_path, base=base), "pol1")
        assert note is not None and "outside.txt" in note
    finally:
        wp.clear_policy("pol1")


def _manager():
    from navin.agent.subagent import SubagentManager

    return SubagentManager.__new__(SubagentManager)


def test_conflict_note_between_active_checkouts(tmp_path):
    one, two = tmp_path / "one", tmp_path / "two"
    one.mkdir()
    two.mkdir()
    base_one, base_two = _repo(one), _repo(two)
    (one / "a.py").write_text("1\n")
    (two / "a.py").write_text("2\n")
    m = _manager()
    co_one = SimpleNamespace(path=one, base=base_one, task_id="one")
    co_two = SimpleNamespace(path=two, base=base_two, task_id="two")
    m._track_worktree("one", "first", co_one)
    m._track_worktree("two", "second", co_two)
    note = m._conflict_note("one", co_one)
    assert "a.py: one, two" in note
    m._untrack_worktree("two")
    assert m._conflict_note("one", co_one) == ""


def test_spawn_worktrees_action(tmp_path):
    base = _repo(tmp_path)
    (tmp_path / "a.py").write_text("z\n")
    m = _manager()
    m._track_worktree("wt1", "lbl", SimpleNamespace(path=tmp_path, base=base, task_id="wt1"))
    wp.set_policy("wt1", wp.policy_from_dict({"allowed_paths": ["a.py"]}))
    try:
        out = _spawn_call(m, action="worktrees")
    finally:
        wp.clear_policy("wt1")
    assert "[wt1] lbl" in out and str(tmp_path) in out and base in out
    assert "modified: a.py" in out and "'allowed_paths': ['a.py']" in out
    m._untrack_worktree("wt1")
    assert _spawn_call(m, action="worktrees") == "No isolated subagent is active."


def test_exec_guard_applies_subagent_command_policy():
    from unittest import mock

    from navin.agent.tools.context import (
        RequestContext,
        bind_request_context,
        reset_request_context,
    )
    from navin.agent.tools.shell import _workspace_policy_block

    wp.set_policy("cmd1", wp.policy_from_dict({"allowed_commands": ["pytest"]}))
    token = bind_request_context(RequestContext(
        channel="cli", chat_id="x", message_id=None, session_key="cli:x",
        runtime=mock.Mock(), metadata={"subagent": True, "subagent_task_id": "cmd1"},
    ))
    try:
        assert _workspace_policy_block("pytest -q tests") is None
        refused = _workspace_policy_block("rm -rf src")
        assert refused is not None and "pytest" in refused
    finally:
        reset_request_context(token)
        wp.clear_policy("cmd1")
    assert _workspace_policy_block("rm -rf src") is None
