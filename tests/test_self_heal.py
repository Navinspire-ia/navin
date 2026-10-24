import asyncio
import json
import subprocess
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

from navin.agent import self_heal
from navin.agent.self_heal import (
    CommandResult,
    agent_fixer,
    build_plan,
    extract_failing_command,
    heal,
    heal_in_worktree,
    isolated_checkout,
)
from navin.agent.tools import forge as forge_tool

LOG = "setup\n##[group]Run ruff check .\nE501 line too long\n"
JOBS = {"jobs": [{"name": "lint", "log": LOG}]}


def test_extract_command_and_pytest():
    assert extract_failing_command(LOG) == "ruff check ."
    log = "$ python -m pytest\nFAILED tests/test_a.py::t - boom\n"
    assert extract_failing_command(log) == "python -m pytest tests/test_a.py"
    assert extract_failing_command("nothing") == ""


def test_build_plan_kinds():
    assert build_plan("ruff check .").kind == "lint"
    assert build_plan("pytest -x").kind == "test"
    assert build_plan("foo").kind == "unknown"


def test_no_failure():
    r = heal("b", fetch_logs=lambda b: {"jobs": [], "note": "green"})
    assert r.status == "no_failure"


def test_not_reproduced():
    r = heal("b", fetch_logs=lambda b: JOBS, run_command=lambda c: CommandResult(0))
    assert r.status == "not_reproduced"


def test_plan_only_without_fix():
    r = heal("b", fetch_logs=lambda b: JOBS, run_command=lambda c: CommandResult(1))
    assert r.status == "plan_ready" and r.reproduced and r.pr is None


def test_pr_opened_only_after_verification():
    codes = iter([1, 0])
    opened = []

    def opener(spec):
        opened.append(spec)
        return {"url": "x"}

    r = heal(
        "fix/b",
        fetch_logs=lambda b: JOBS,
        run_command=lambda c: CommandResult(next(codes)),
        apply_fix=lambda p: True,
        open_pr=opener,
    )
    assert r.status == "pr_opened" and r.verified
    assert opened[0]["draft"] is True and opened[0]["head"] == "fix/b"


def test_default_fixer_gets_prompt_and_runs_in_repo(tmp_path):
    seen = []

    def fake_agent(prompt, root):
        seen.append((prompt, root))
        return SimpleNamespace(content="ok", error=None)

    codes = iter([1, 0])
    r = heal(
        "b",
        fetch_logs=lambda b: JOBS,
        run_command=lambda c: CommandResult(next(codes), "E501 boom"),
        apply_fix=agent_fixer(tmp_path, fake_agent),
    )
    assert r.status == "pr_prepared"
    prompt, root = seen[0]
    assert root == tmp_path
    assert "ruff check ." in prompt and "E501 boom" in prompt and "Repair plan" in prompt


def test_fixer_failure_reports_error_and_no_pr():
    opened = []

    def bad_agent(prompt, root):
        return SimpleNamespace(content="", error="model down")

    r = heal(
        "b",
        fetch_logs=lambda b: JOBS,
        run_command=lambda c: CommandResult(1),
        apply_fix=agent_fixer(Path("."), bad_agent),
        open_pr=lambda s: opened.append(s) or {},
    )
    assert r.status == "fix_error" and not opened and not r.pr
    assert "model down" in r.notes[-1]


def test_cli_fix_flag_enables_fixer(monkeypatch, capsys, tmp_path):
    calls = []

    @contextmanager
    def fake_checkout(root, ref="HEAD"):
        yield tmp_path

    monkeypatch.setattr(self_heal, "isolated_checkout", fake_checkout)
    monkeypatch.setattr(self_heal, "_forge_fetch", lambda root: lambda b: JOBS)
    monkeypatch.setattr(self_heal, "_local_run", lambda c, cwd=None: CommandResult(1))

    def fake_fixer(root, run_agent=None):
        calls.append(root)
        return lambda plan: False

    monkeypatch.setattr(self_heal, "agent_fixer", fake_fixer)
    self_heal.main([])
    assert not calls
    self_heal.main(["--fix"])
    assert calls == [tmp_path]
    capsys.readouterr()


def test_fix_failed_never_opens_pr():
    opened = []
    r = heal(
        "b",
        fetch_logs=lambda b: JOBS,
        run_command=lambda c: CommandResult(1),
        apply_fix=lambda p: True,
        open_pr=lambda s: opened.append(s) or {},
    )
    assert r.status == "fix_failed" and not opened


def _git_repo(path: Path) -> Path:
    def git(*args):
        subprocess.run(["git", *args], cwd=path, check=True, capture_output=True)

    path.mkdir()
    git("init", "-q")
    git("config", "user.email", "t@t")
    git("config", "user.name", "t")
    (path / "app.py").write_text("x = 1\n")
    git("add", "app.py")
    git("commit", "-q", "-m", "init")
    return path


def _worktrees(root: Path) -> str:
    return subprocess.run(
        ["git", "worktree", "list"], cwd=root, check=True, capture_output=True, text=True
    ).stdout


def test_isolated_checkout_creates_then_removes_worktree(tmp_path):
    root = _git_repo(tmp_path / "repo")
    with isolated_checkout(root) as wt:
        assert (wt / "app.py").read_text() == "x = 1\n"
        assert str(wt) in _worktrees(root)
    assert not wt.exists()
    assert str(wt) not in _worktrees(root)


def test_fix_in_worktree_never_touches_source_tree(tmp_path):
    root = _git_repo(tmp_path / "repo")
    seen = []

    def fake_agent(prompt, workdir):
        seen.append(workdir)
        (workdir / "app.py").write_text("x = 2\n")
        return SimpleNamespace(content="ok", error=None)

    jobs = {"jobs": [{"name": "t", "log": "$ grep -q 'x = 2' app.py\n"}]}
    report = heal_in_worktree(root, fix=True, fetch_logs=lambda b: jobs, run_agent=fake_agent)
    assert report.status == "pr_prepared" and report.reproduced and report.verified
    assert seen and seen[0] != root and not seen[0].exists()
    assert (root / "app.py").read_text() == "x = 1\n"


def test_forge_ci_heal_calls_heal_in_worktree(monkeypatch, tmp_path):
    calls = []

    def fake_heal(root, branch, *, fix, open_pr):
        calls.append((root, branch, fix, open_pr))
        return self_heal.HealReport(status="plan_ready", branch=branch, command="ruff check .")

    monkeypatch.setattr(self_heal, "heal_in_worktree", fake_heal)
    tool = object.__new__(forge_tool.ForgeTool)
    tool._root_or_error = lambda: (tmp_path, "")
    out = asyncio.run(tool.execute(action="ci_heal", branch="feat/x"))
    assert calls == [(tmp_path, "feat/x", False, False)]
    data = json.loads(out)
    assert data["status"] == "plan_ready" and data["command"] == "ruff check ."
    assert "ci_heal" in forge_tool._ACTIONS


def test_forge_ci_heal_open_pr_needs_fix(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(self_heal, "heal_in_worktree", lambda *a, **k: calls.append(a))
    tool = object.__new__(forge_tool.ForgeTool)
    tool._root_or_error = lambda: (tmp_path, "")
    out = asyncio.run(tool.execute(action="ci_heal", open_pr=True))
    assert "needs fix=true" in str(getattr(out, "content", out)) and not calls


def test_isolated_checkout_bad_ref_raises_and_cleans(tmp_path):
    root = _git_repo(tmp_path / "repo")
    try:
        with isolated_checkout(root, "no-such-ref"):
            raise AssertionError("body must not run")
    except RuntimeError as exc:
        assert "worktree add failed" in str(exc)
    assert _worktrees(root).count("\n") == 1
