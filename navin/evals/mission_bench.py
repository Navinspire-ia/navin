# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Navin Bench: replay real Git commits as missions (SWE-bench style).

A mission is mined from a commit: the commit message is the request, the test
files the commit touched are the success criterion. Each run happens in a
throwaway ``git worktree`` checked out at the commit's parent, the commit's
test files are dropped in, a solver (the model under test) edits the sources,
then the tests and the linter are measured.

Metrics per mission: success, tokens, USD, seconds, broken tests, lint delta.
The matrix is model x strategy, with cost per successful mission.

Usage::

    python -m navin.evals.mission_bench --dry-run --limit 3 --out bench.json
    python -m navin.evals.mission_bench --solver pkg.mod:solve --models a,b \
        --strategies navin,baseline

``--dry-run`` calls no model: the "solver" applies the real commit's source
diff (oracle), which validates the harness end to end.

A solver is ``solve(mission, workdir, model, strategy) -> SolverUsage``.
"""

from __future__ import annotations

import argparse
import importlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path

from navin.utils.proc import no_window_kwargs

_TEST_RE = re.compile(r"(^|/)(tests?/|test_[^/]*\.py$|[^/]*_test\.py$)")
_PYTEST_SUMMARY = re.compile(r"(\d+) (failed|passed|error|errors)")


@dataclass(frozen=True)
class Mission:
    commit: str
    parent: str
    request: str
    test_files: tuple[str, ...]
    source_files: tuple[str, ...]


@dataclass
class SolverUsage:
    tokens: int = 0
    usd: float = 0.0


@dataclass
class MissionResult:
    commit: str
    model: str
    strategy: str
    success: bool
    tokens: int = 0
    usd: float = 0.0
    seconds: float = 0.0
    tests_failed: int = 0
    lint_delta: int = 0
    error: str = ""


@dataclass
class CellSummary:
    model: str
    strategy: str
    missions: int = 0
    successes: int = 0
    tokens: int = 0
    usd: float = 0.0
    seconds: float = 0.0
    tests_failed: int = 0
    lint_delta: int = 0
    usd_per_success: float | None = None
    tokens_per_success: float | None = None


Solver = Callable[[Mission, Path, str, str], SolverUsage]


def _git(repo: Path, *args: str, check: bool = True) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=False,
        **no_window_kwargs(),
    )
    if check and proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {proc.stderr.strip()}")
    return proc.stdout


def extract_missions(
    repo: Path | str, *, limit: int = 10, rev: str = "HEAD", scan: int = 400
) -> list[Mission]:
    """Mine commits that touch both tests and sources (single parent only)."""
    root = Path(repo)
    out = _git(
        root, "log", rev, f"-{scan}", "--no-merges", "--format=%H%x1f%P%x1f%B%x1e"
    )
    missions: list[Mission] = []
    for rec in out.split("\x1e"):
        rec = rec.strip()
        if not rec:
            continue
        sha, parents, body = (rec.split("\x1f", 2) + ["", ""])[:3]
        plist = parents.split()
        if len(plist) != 1 or not body.strip():
            continue
        names = _git(
            root, "diff-tree", "--no-commit-id", "--name-only", "-r", sha
        ).split()
        tests = tuple(n for n in names if _TEST_RE.search(n) and n.endswith(".py"))
        srcs = tuple(n for n in names if not _TEST_RE.search(n))
        if not tests or not srcs:
            continue
        missions.append(Mission(sha, plist[0], body.strip(), tests, srcs))
        if len(missions) >= limit:
            break
    return missions


def oracle_solver(repo: Path) -> Solver:
    """Dry-run solver: applies the real commit's source diff, no model call."""

    def solve(mission: Mission, workdir: Path, model: str, strategy: str):
        patch = _git(
            repo, "diff", mission.parent, mission.commit, "--", *mission.source_files
        )
        if patch.strip():
            proc = subprocess.run(
                ["git", "apply", "--whitespace=nowarn", "-"],
                cwd=workdir, input=patch, capture_output=True, text=True,
                check=False,
                **no_window_kwargs(),
            )
            if proc.returncode != 0:
                raise RuntimeError(f"oracle patch failed: {proc.stderr.strip()}")
        return SolverUsage()

    return solve


def _run_tests(workdir: Path, tests: Iterable[str]) -> tuple[bool, int]:
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-x", "--no-header", "-p",
         "no:cacheprovider", *tests],
        cwd=workdir, capture_output=True, text=True, check=False,
        **no_window_kwargs(),
    )
    failed = 0
    for n, kind in _PYTEST_SUMMARY.findall(proc.stdout):
        if kind != "passed":
            failed += int(n)
    return proc.returncode == 0, failed


def _lint_count(workdir: Path, files: Iterable[str]) -> int:
    existing = [f for f in files if (workdir / f).exists() and f.endswith(".py")]
    if not existing:
        return 0
    proc = subprocess.run(
        [sys.executable, "-m", "ruff", "check", "--output-format=concise",
         *existing],
        cwd=workdir, capture_output=True, text=True, check=False,
        **no_window_kwargs(),
    )
    return sum(1 for ln in proc.stdout.splitlines() if re.match(r".+:\d+:\d+:", ln))


def run_mission(
    repo: Path | str, mission: Mission, solver: Solver, model: str, strategy: str
) -> MissionResult:
    root = Path(repo)
    res = MissionResult(mission.commit, model, strategy, success=False)
    tmp = Path(tempfile.mkdtemp(prefix="navin-bench-"))
    work = tmp / "wt"
    t0 = time.monotonic()
    try:
        _git(root, "worktree", "add", "--detach", str(work), mission.parent)
        before = _lint_count(work, mission.source_files)
        for f in mission.test_files:
            data = subprocess.run(
                ["git", "show", f"{mission.commit}:{f}"], cwd=root,
                capture_output=True, check=True,
                **no_window_kwargs(),
            ).stdout
            (work / f).parent.mkdir(parents=True, exist_ok=True)
            (work / f).write_bytes(data)
        usage = solver(mission, work, model, strategy)
        res.tokens, res.usd = usage.tokens, usage.usd
        ok, failed = _run_tests(work, mission.test_files)
        res.success, res.tests_failed = ok, failed
        res.lint_delta = _lint_count(work, mission.source_files) - before
    except Exception as exc:  # one broken mission must not stop the matrix
        res.error = str(exc)[:300]
    finally:
        res.seconds = round(time.monotonic() - t0, 3)
        subprocess.run(
            ["git", "worktree", "remove", "--force", str(work)],
            cwd=root, capture_output=True, check=False,
            **no_window_kwargs(),
        )
        shutil.rmtree(tmp, ignore_errors=True)
    return res


def summarize(results: Iterable[MissionResult]) -> list[CellSummary]:
    cells: dict[tuple[str, str], CellSummary] = {}
    for r in results:
        c = cells.setdefault((r.model, r.strategy), CellSummary(r.model, r.strategy))
        c.missions += 1
        c.successes += int(r.success)
        c.tokens += r.tokens
        c.usd += r.usd
        c.seconds += r.seconds
        c.tests_failed += r.tests_failed
        c.lint_delta += r.lint_delta
    for c in cells.values():
        if c.successes:
            c.usd_per_success = round(c.usd / c.successes, 6)
            c.tokens_per_success = round(c.tokens / c.successes, 1)
        c.usd, c.seconds = round(c.usd, 6), round(c.seconds, 3)
    return list(cells.values())


@dataclass
class BenchReport:
    repo: str
    missions: list[Mission] = field(default_factory=list)
    results: list[MissionResult] = field(default_factory=list)
    dry_run: bool = False

    def to_json(self) -> dict:
        return {
            "repo": self.repo,
            "dry_run": self.dry_run,
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "missions": [asdict(m) for m in self.missions],
            "results": [asdict(r) for r in self.results],
            "matrix": [asdict(c) for c in summarize(self.results)],
        }

    def to_markdown(self) -> str:
        lines = [
            "# Navin Bench",
            "",
            f"Missions: {len(self.missions)}, dry-run: {self.dry_run}",
            "",
            "| model | strategy | success | tokens | USD | s | broken tests "
            "| lint delta | USD/success |",
            "|---|---|---|---|---|---|---|---|---|",
        ]
        for c in summarize(self.results):
            per = "n/a" if c.usd_per_success is None else f"{c.usd_per_success:.4f}"
            lines.append(
                f"| {c.model} | {c.strategy} | {c.successes}/{c.missions} "
                f"| {c.tokens} | {c.usd:.4f} | {c.seconds:.1f} "
                f"| {c.tests_failed} | {c.lint_delta:+d} | {per} |"
            )
        return "\n".join(lines) + "\n"


def run_bench(
    repo: Path | str,
    *,
    solver: Solver | None = None,
    models: Iterable[str] = ("dry",),
    strategies: Iterable[str] = ("oracle",),
    limit: int = 5,
    dry_run: bool = False,
) -> BenchReport:
    root = Path(repo)
    if dry_run:
        solver = oracle_solver(root)
    if solver is None:
        raise ValueError("a solver is required unless dry_run=True")
    report = BenchReport(repo=str(root), dry_run=dry_run)
    report.missions = extract_missions(root, limit=limit)
    for m in report.missions:
        for model in models:
            for strat in strategies:
                report.results.append(run_mission(root, m, solver, model, strat))
    return report


def _load_solver(spec: str) -> Solver:
    mod, _, attr = spec.partition(":")
    return getattr(importlib.import_module(mod), attr)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Replay real commits as missions")
    p.add_argument("--repo", default=".")
    p.add_argument("--limit", type=int, default=5)
    p.add_argument("--models", default="dry")
    p.add_argument("--strategies", default="oracle")
    p.add_argument("--solver", default="", help="module:function")
    p.add_argument("--dry-run", action="store_true", help="no model call")
    p.add_argument("--out", default="", help="JSON report path")
    p.add_argument("--md", default="", help="Markdown summary path")
    a = p.parse_args(argv)
    if not a.dry_run and not a.solver:
        p.error("--solver module:function is required without --dry-run")
    report = run_bench(
        a.repo,
        solver=_load_solver(a.solver) if a.solver else None,
        models=[s for s in a.models.split(",") if s],
        strategies=[s for s in a.strategies.split(",") if s],
        limit=a.limit,
        dry_run=a.dry_run,
    )
    md = report.to_markdown()
    print(md)
    payload = json.dumps(report.to_json(), indent=2) + "\n"
    if a.out:
        Path(a.out).write_text(payload)
    from navin.webui.bench_api import _root

    latest = _root() / "latest.json"
    latest.parent.mkdir(parents=True, exist_ok=True)
    latest.write_text(payload, encoding="utf-8")
    if a.md:
        Path(a.md).write_text(md, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
