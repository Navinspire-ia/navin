# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Replay git history to measure structural ranking against simpler baselines.

The question is the one an agent faces before a change: given what it already
knows, which other files will the change really touch? Past commits answer it.
Each recent commit becomes a case, and every method ranks candidates without
seeing that commit. Three scenarios:

- ``co_edit``: one file of the commit is known (the seed); the other files it
  touched are the targets. This is Smart Code Retrieval and Impact Analysis.
- ``tests``: the code files of the commit are known; the test files it touched
  are the targets. This is Intelligent Test Selection.
- ``request``: only the commit subject is known, as a stand-in for the user's
  request; every touched file is a target. This is the end-to-end retrieval.

Methods: ``same_dir`` (sibling files), ``imports_1hop`` (direct import
neighbours), ``impact_bfs`` (reverse import walk, as the file graph impact
does today), ``co_change`` (past co-modifications only), ``ppr_*`` (graph
ranking, structure only or with history), ``text`` and ``text+ppr`` for the
request scenario. A learned GraphPrompt variant must beat ``ppr_history`` here
before it ships.

Leakage, stated honestly: co-change evidence comes only from commits older than
every evaluated commit, and files a commit added are left out of its seeds and
targets. The code structure (imports, calls) and the full-text index are read
at HEAD, which can only flatter the structure-based methods; the history-only
``co_change`` method is the one entirely free of it.

Usage::

    python -m navin.evals.graph_rank_bench [ROOT] --commits 80 --k 10 [--json out.json]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict, deque
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from navin.index import graph_rank
from navin.index.graph_rank import CodeGraph, Commit, build_graph, rank, resolve_seeds

MRR_DEPTH = 50
SCENARIOS = ("co_edit", "tests", "request")


@dataclass(frozen=True, slots=True)
class Case:
    scenario: str
    sha: str
    seeds: tuple[str, ...]
    targets: frozenset[str]
    query: str = ""


def added_files(root: Path, *, max_commits: int) -> dict[str, set[str]]:
    """Files each commit created (``--diff-filter=A``), by commit sha."""
    out = graph_rank._run_git(
        root,
        [
            "log", "--no-merges", "--relative", "--diff-filter=A", "--name-only",
            f"--max-count={max(1, int(max_commits))}", "--format=%x1e%H",
        ],
    )
    added: dict[str, set[str]] = {}
    for block in (out or "").split("\x1e"):
        block = block.strip("\n")
        if not block:
            continue
        sha, _, body = block.partition("\n")
        added[sha.strip()] = {line.strip() for line in body.splitlines() if line.strip()}
    return added


def build_cases(
    commits: Sequence[Commit],
    nodes: set[str],
    tests: set[str],
    added: dict[str, set[str]],
    *,
    eval_commits: int,
    max_files: int = graph_rank.MAX_FILES_PER_COMMIT,
    max_seeds: int = 3,
) -> tuple[list[Case], list[Commit]]:
    """Cases from the newest usable commits, and the strictly older history."""
    cases: list[Case] = []
    oldest = -1
    used = 0
    for position, commit in enumerate(commits):
        if used >= eval_commits:
            break
        if commit.parents == 0:
            continue
        fresh = added.get(commit.sha, set())
        files = sorted({path for path in commit.files if path in nodes and path not in fresh})
        if len(files) < 2 or len(files) > max_files:
            continue
        used += 1
        oldest = position
        code = [path for path in files if path not in tests]
        touched_tests = [path for path in files if path in tests]
        for seed in code[:max_seeds]:
            cases.append(Case("co_edit", commit.sha, (seed,), frozenset(set(files) - {seed})))
        if code and touched_tests:
            cases.append(Case("tests", commit.sha, tuple(code), frozenset(touched_tests)))
        if commit.subject.strip():
            cases.append(
                Case("request", commit.sha, (), frozenset(files), query=commit.subject.strip())
            )
    history = list(commits[oldest + 1:]) if oldest >= 0 else list(commits)
    return cases, history


# ---------------------------------------------------------------------------
# Methods: each returns a ranked list of candidate files for a case.
# ---------------------------------------------------------------------------


def _parent(path: str) -> str:
    return path.rsplit("/", 1)[0] if "/" in path else ""


def same_dir(index: Any, case: Case, pool: Iterable[str]) -> list[str]:
    folders = {_parent(seed) for seed in case.seeds}
    near = sorted(path for path in pool if _parent(path) in folders)
    uppers = {_parent(folder) for folder in folders}
    wider = sorted(
        path for path in pool if _parent(path) not in folders and _parent(_parent(path)) in uppers
    )
    return near + wider


def imports_1hop(index: Any, case: Case, pool: Iterable[str]) -> list[str]:
    allowed = set(pool)
    links: dict[str, int] = defaultdict(int)
    for seed in case.seeds:
        for other in set(index.dependencies(seed)) | set(index.dependents(seed)):
            if other in allowed:
                links[other] += 1

    def degree(path: str) -> int:
        return len(index.dependencies(path)) + len(index.dependents(path))

    return sorted(links, key=lambda path: (-links[path], -degree(path), path))


def impact_bfs(index: Any, case: Case, pool: Iterable[str], *, hops: int = 3) -> list[str]:
    allowed = set(pool)
    seen = set(case.seeds)
    order: list[str] = []
    frontier = deque(case.seeds)
    for _ in range(hops):
        layer: set[str] = set()
        while frontier:
            for other in index.dependents(frontier.popleft()):
                if other not in seen:
                    seen.add(other)
                    layer.add(other)
        order.extend(sorted(path for path in layer if path in allowed))
        frontier = deque(sorted(layer))
        if not frontier:
            break
    return order


def co_change_only(graph: CodeGraph, case: Case, pool: Iterable[str]) -> list[str]:
    allowed = set(pool)
    scores: dict[str, float] = defaultdict(float)
    for seed in case.seeds:
        for other in allowed:
            found = graph.edge(seed, other, graph_rank.REL_CO_CHANGE)
            if found is not None:
                scores[other] += found[0]
    return sorted(scores, key=lambda path: (-scores[path], path))


def ppr(
    graph: CodeGraph, case: Case, pool: Iterable[str], *, task: str,
    seeds: dict[str, float] | None = None, keep_seeds: bool = False,
) -> list[str]:
    allowed = set(pool)
    weights = seeds if seeds is not None else {seed: 1.0 for seed in case.seeds}
    result = rank(
        graph, weights, task=task, limit=MRR_DEPTH * 4,
        exclude=() if keep_seeds else case.seeds, with_evidence=False,
    )
    return [item.path for item in result.files if item.path in allowed]


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class _Tally:
    cases: int = 0
    recall: float = 0.0
    hits: int = 0
    reciprocal: float = 0.0
    answered: int = 0

    def add(self, ranked: Sequence[str], targets: frozenset[str], k: int) -> None:
        self.cases += 1
        top = ranked[:k]
        found = len(targets.intersection(top))
        self.recall += found / len(targets) if targets else 0.0
        self.hits += 1 if found else 0
        self.answered += 1 if ranked else 0
        for position, path in enumerate(ranked[:MRR_DEPTH], start=1):
            if path in targets:
                self.reciprocal += 1.0 / position
                break

    def summary(self) -> dict[str, float | int]:
        count = max(self.cases, 1)
        return {
            "cases": self.cases,
            "recall_at_k": round(self.recall / count, 4),
            "hit_at_k": round(self.hits / count, 4),
            "mrr": round(self.reciprocal / count, 4),
            "coverage": round(self.answered / count, 4),
        }


def evaluate(
    index: Any,
    commits: Sequence[Commit],
    added: dict[str, set[str]] | None = None,
    *,
    eval_commits: int = 80,
    k: int = 10,
    max_seeds: int = 3,
) -> dict[str, Any]:
    """Run every scenario and method; returns the scores and the split used."""
    nodes = set(index.entries)
    structure = build_graph(index, [])
    tests = set(structure.tests)
    cases, history = build_cases(
        commits, nodes, tests, added or {}, eval_commits=eval_commits, max_seeds=max_seeds,
    )
    with_history = build_graph(index, history)
    code_pool = sorted(nodes)
    test_pool = sorted(tests)

    co_edit: dict[str, Callable[[Case], list[str]]] = {
        "same_dir": lambda case: same_dir(index, case, code_pool),
        "imports_1hop": lambda case: imports_1hop(index, case, code_pool),
        "impact_bfs": lambda case: impact_bfs(index, case, code_pool),
        "co_change": lambda case: co_change_only(with_history, case, code_pool),
        "ppr_structure": lambda case: ppr(structure, case, code_pool, task="code_relevance"),
        "ppr_history": lambda case: ppr(with_history, case, code_pool, task="code_relevance"),
        "ppr_impact_history": lambda case: ppr(
            with_history, case, code_pool, task="impact_analysis"
        ),
    }
    tests_methods: dict[str, Callable[[Case], list[str]]] = {
        "same_dir": lambda case: same_dir(index, case, test_pool),
        "imports_1hop": lambda case: imports_1hop(index, case, test_pool),
        "impact_bfs": lambda case: impact_bfs(index, case, test_pool),
        "co_change": lambda case: co_change_only(with_history, case, test_pool),
        "ppr_structure": lambda case: ppr(structure, case, test_pool, task="test_relevance"),
        "ppr_history": lambda case: ppr(with_history, case, test_pool, task="test_relevance"),
    }
    seed_cache: dict[str, dict[str, float]] = {}

    def _text_seeds(case: Case) -> dict[str, float]:
        if case.query not in seed_cache:
            seed_cache[case.query] = resolve_seeds(index, query=case.query).weights
        return seed_cache[case.query]

    request: dict[str, Callable[[Case], list[str]]] = {
        "text": lambda case: sorted(
            _text_seeds(case), key=lambda path: (-_text_seeds(case)[path], path)
        ),
        "text+ppr_structure": lambda case: ppr(
            structure, case, code_pool, task="code_relevance",
            seeds=_text_seeds(case), keep_seeds=True,
        ),
        "text+ppr_history": lambda case: ppr(
            with_history, case, code_pool, task="code_relevance",
            seeds=_text_seeds(case), keep_seeds=True,
        ),
    }
    methods = {"co_edit": co_edit, "tests": tests_methods, "request": request}

    tallies: dict[str, dict[str, _Tally]] = {
        scenario: {name: _Tally() for name in methods[scenario]} for scenario in SCENARIOS
    }
    for case in cases:
        for name, method in methods[case.scenario].items():
            tallies[case.scenario][name].add(method(case), case.targets, k)

    evaluated = {case.sha for case in cases}
    return {
        "k": k,
        "eval_commits": len(evaluated),
        "history_commits": with_history.history_commits,
        "oldest_evaluated": next(
            (commit.sha for commit in reversed(commits) if commit.sha in evaluated), ""
        ),
        "model_version": graph_rank.MODEL_VERSION,
        "scenarios": {
            scenario: {name: tally.summary() for name, tally in tallies[scenario].items()}
            for scenario in SCENARIOS
        },
    }


def render(report: dict[str, Any]) -> str:
    """Markdown tables, one per scenario, best Recall@k first."""
    k = report["k"]
    lines = [
        f"Graph ranking bench ({report['model_version']}): {report['eval_commits']} "
        f"evaluated commit(s), co-change history from {report['history_commits']} older "
        "commit(s).",
    ]
    for scenario, rows in report["scenarios"].items():
        cases = next(iter(rows.values()), {}).get("cases", 0)
        lines += [
            "",
            f"{scenario} ({cases} case(s))",
            "",
            f"| method | Recall@{k} | Hit@{k} | MRR | coverage |",
            "|---|---|---|---|---|",
        ]
        ordered = sorted(rows.items(), key=lambda item: (-item[1]["recall_at_k"], item[0]))
        for name, row in ordered:
            lines.append(
                f"| {name} | {row['recall_at_k']:.3f} | {row['hit_at_k']:.3f} | "
                f"{row['mrr']:.3f} | {row['coverage']:.3f} |"
            )
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("root", nargs="?", default=".", help="project root (a git repository)")
    parser.add_argument("--commits", type=int, default=80, help="recent commits to evaluate")
    parser.add_argument("--history", type=int, default=graph_rank.DEFAULT_HISTORY)
    parser.add_argument("--k", type=int, default=10)
    parser.add_argument("--json", type=Path, default=None, help="also write the report here")
    args = parser.parse_args(argv)

    from navin.index import get_index

    root = Path(args.root).resolve()
    started = time.monotonic()
    index = get_index(root)
    index.ensure()
    commits = graph_rank.git_history(root, max_commits=args.history)
    if not commits:
        print(f"No git history under {root}.", file=sys.stderr)
        return 1
    report = evaluate(
        index, commits, added_files(root, max_commits=args.history),
        eval_commits=args.commits, k=args.k,
    )
    report["seconds"] = round(time.monotonic() - started, 1)
    print(render(report))
    print(f"\n({report['seconds']} s)")
    if args.json is not None:
        args.json.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
