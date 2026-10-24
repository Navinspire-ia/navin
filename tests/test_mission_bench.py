from __future__ import annotations

import json
import subprocess
from pathlib import Path

from navin.evals import mission_bench as mb


def _g(repo: Path, *a: str) -> None:
    subprocess.run(["git", *a], cwd=repo, check=True, capture_output=True)


def _make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "r"
    (repo / "tests").mkdir(parents=True)
    _g(repo, "init", "-q")
    _g(repo, "config", "user.email", "a@b.c")
    _g(repo, "config", "user.name", "t")
    (repo / "calc.py").write_text("def add(a, b):\n    return 0\n")
    _g(repo, "add", ".")
    _g(repo, "commit", "-qm", "init")
    (repo / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    (repo / "tests" / "test_calc.py").write_text(
        "import sys\nsys.path.insert(0, '.')\nfrom calc import add\n\n\n"
        "def test_add():\n    assert add(1, 2) == 3\n"
    )
    _g(repo, "add", ".")
    _g(repo, "commit", "-qm", "Fix add to really add\n\nIt returned 0.")
    return repo


def test_extract_missions(tmp_path):
    repo = _make_repo(tmp_path)
    ms = mb.extract_missions(repo)
    assert len(ms) == 1
    assert ms[0].request.startswith("Fix add")
    assert ms[0].test_files == ("tests/test_calc.py",)
    assert ms[0].source_files == ("calc.py",)


def test_dry_run_succeeds_and_cleans_worktree(tmp_path):
    repo = _make_repo(tmp_path)
    rep = mb.run_bench(repo, dry_run=True, limit=2)
    assert len(rep.results) == 1
    assert rep.results[0].success, rep.results[0].error
    assert "worktree" not in subprocess.run(
        ["git", "worktree", "list"], cwd=repo, capture_output=True, text=True
    ).stdout.replace(str(repo), "")
    data = rep.to_json()
    json.dumps(data)
    assert data["matrix"][0]["usd_per_success"] == 0.0
    assert "| dry | oracle | 1/1" in rep.to_markdown()


def test_noop_solver_fails_and_matrix(tmp_path):
    repo = _make_repo(tmp_path)

    def noop(m, w, model, strat):
        return mb.SolverUsage(tokens=100, usd=0.5)

    rep = mb.run_bench(
        repo, solver=noop, models=["a", "b"], strategies=["s1"], limit=1
    )
    assert [r.success for r in rep.results] == [False, False]
    cells = mb.summarize(rep.results)
    assert len(cells) == 2
    assert cells[0].usd_per_success is None and cells[0].tokens == 100
    assert rep.results[0].tests_failed >= 1


def test_cli_requires_solver(tmp_path, capsys):
    import pytest

    with pytest.raises(SystemExit):
        mb.main(["--repo", str(tmp_path)])


def test_bench_api_latest(tmp_path):
    import pytest

    from navin.webui.bench_api import BenchError, handle_bench_action

    assert handle_bench_action("latest", {}, tmp_path) == {"report": None}
    (tmp_path / "latest.json").write_text('{"matrix": []}')
    assert handle_bench_action("latest", {}, tmp_path)["report"] == {"matrix": []}
    with pytest.raises(BenchError):
        handle_bench_action("nope", {}, tmp_path)


def test_dry_run_writes_latest_read_by_route(tmp_path, monkeypatch):
    from navin.webui import bench_api

    repo = _make_repo(tmp_path)
    monkeypatch.setattr(bench_api, "_root", lambda: tmp_path / "bench")
    assert mb.main(["--repo", str(repo), "--dry-run"]) == 0
    got = bench_api.handle_bench_action("latest", {})
    assert got["report"] is not None
    assert got["report"]["dry_run"] is True
    assert got["report"]["matrix"]
