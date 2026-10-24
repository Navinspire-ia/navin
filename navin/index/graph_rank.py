# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Structural ranking over the code graph: the baseline of Navin Graph Intelligence.

Files are the nodes. Edges are typed and directed, because "a calls b" and
"a is tested by b" answer different questions:

- ``imports``: a imports b (resolved import edge of the code index).
- ``calls``: a calls a definition that lives in b.
- ``inherits``: a subclasses, or decorates with, a definition from b.
- ``tests``: test file a exercises b (any of the above, seen from a test file).
- ``co_change``: a and b changed in the same commits (symmetric, from git).

A query starts from seed files (named paths, the files defining named symbols,
or the best full-text matches of a request), walks at most ``max_hops`` steps
and ranks what it reaches by hop-limited Personalized PageRank. A task profile
weights every relation in each direction, so one shared graph answers several
questions: ``code_relevance`` reads both ways, ``impact_analysis`` follows
what depends on the seeds, ``test_relevance`` does the same and keeps test
files only.

The profile is the hand-set counterpart of a GraphPrompt task vector: one
shared representation, a light per-task reading of it. A learned variant has
to beat this baseline on ``navin/evals/graph_rank_bench.py`` before replacing
it. Every result carries the path that links it to a seed, so a recommendation
can be checked, and a graph that cannot answer reports a fallback reason
instead of inventing a ranking. Results are advice for the planner, never a
decision to edit a file.
"""

from __future__ import annotations

import math
import re
import subprocess
import threading
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from itertools import combinations
from pathlib import Path
from typing import Any

from loguru import logger

from navin.index.service import _looks_like_test
from navin.index.symbols import REF_BASE, REF_CALL, REF_DECORATOR
from navin.utils.git_argv import git_argv
from navin.utils.path import normalize_relative_path
from navin.utils.proc import no_window_kwargs

MODEL_VERSION = "ppr-baseline-1"

REL_IMPORTS = "imports"
REL_CALLS = "calls"
REL_INHERITS = "inherits"
REL_TESTS = "tests"
REL_CO_CHANGE = "co_change"
RELATIONS = (REL_IMPORTS, REL_CALLS, REL_INHERITS, REL_TESTS, REL_CO_CHANGE)
# Stored once in each direction, so a walk follows them forward only.
_SYMMETRIC = frozenset({REL_CO_CHANGE})

_KIND_RELATION = {
    REF_CALL: REL_CALLS,
    REF_BASE: REL_INHERITS,
    REF_DECORATOR: REL_INHERITS,
}

# A commit touching more files than this is a sweep (rename, formatting,
# version bump): its pairs say nothing about how two files are coupled.
MAX_FILES_PER_COMMIT = 30
MAX_HOPS = 4
_GIT_TIMEOUT_S = 20
DEFAULT_HISTORY = 1500
_QUERY_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")


@dataclass(frozen=True, slots=True)
class TaskProfile:
    """How strongly a task follows each relation, per direction.

    ``forward`` applies to an edge walked from its source to its target (the
    seed uses something), ``backward`` to an edge walked from its target back
    to its source (something uses the seed).
    """

    forward: Mapping[str, float]
    backward: Mapping[str, float]
    tests_only: bool = False
    summary: str = ""


PROFILES: dict[str, TaskProfile] = {
    "code_relevance": TaskProfile(
        forward={
            REL_IMPORTS: 0.6, REL_CALLS: 1.0, REL_INHERITS: 1.0,
            REL_TESTS: 0.3, REL_CO_CHANGE: 0.8,
        },
        backward={REL_IMPORTS: 0.4, REL_CALLS: 0.7, REL_INHERITS: 0.7, REL_TESTS: 0.5},
        summary="files worth reading to work on the seeds",
    ),
    "impact_analysis": TaskProfile(
        forward={REL_IMPORTS: 0.1, REL_CALLS: 0.15, REL_INHERITS: 0.2, REL_CO_CHANGE: 1.0},
        backward={REL_IMPORTS: 0.8, REL_CALLS: 1.0, REL_INHERITS: 1.0, REL_TESTS: 1.0},
        summary="files a change to the seeds is likely to affect",
    ),
    "test_relevance": TaskProfile(
        forward={REL_IMPORTS: 0.1, REL_CALLS: 0.15, REL_INHERITS: 0.2, REL_CO_CHANGE: 0.8},
        backward={REL_IMPORTS: 0.7, REL_CALLS: 1.0, REL_INHERITS: 1.0, REL_TESTS: 1.5},
        tests_only=True,
        summary="tests to run after changing the seeds",
    ),
}
TASKS = tuple(PROFILES)


# ---------------------------------------------------------------------------
# Git history
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Commit:
    """One non-merge commit and the project files it touched."""

    sha: str
    timestamp: int
    subject: str
    files: tuple[str, ...]
    # A root commit (no parent) imports a whole tree at once: its pairs say
    # nothing about coupling, whatever the file cap.
    parents: int = 1


def _run_git(root: Path, args: Sequence[str]) -> str | None:
    git = git_argv(root)
    if git is None:
        return None
    try:
        out = subprocess.run(
            [*git, "-c", "core.quotePath=false", *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_GIT_TIMEOUT_S,
            **no_window_kwargs(),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout if out.returncode == 0 else None


def git_head(root: Path) -> str:
    """Current commit of ``root``, or "" outside a git repository."""
    out = _run_git(root, ["rev-parse", "HEAD"])
    return out.strip() if out else ""


def git_history(root: Path, *, max_commits: int = DEFAULT_HISTORY) -> list[Commit]:
    """Non-merge commits, newest first, with the files each one touched.

    Paths are relative to ``root`` (``--relative``), so a project opened in a
    sub-folder of its repository lines up with the index. Outside a git
    repository, or when git fails, the answer is empty and the graph is built
    from the code alone.
    """
    out = _run_git(
        root,
        [
            "log", "--no-merges", "--relative", "--name-only",
            f"--max-count={max(1, int(max_commits))}",
            "--format=%x1e%H%x1f%ct%x1f%P%x1f%s",
        ],
    )
    if not out:
        return []
    commits: list[Commit] = []
    for block in out.split("\x1e"):
        block = block.strip("\n")
        if not block:
            continue
        head, _, body = block.partition("\n")
        parts = head.split("\x1f", 3)
        if len(parts) < 4:
            continue
        try:
            stamp = int(parts[1])
        except ValueError:
            continue
        files = tuple(
            normalize_relative_path(line.strip()) for line in body.splitlines() if line.strip()
        )
        commits.append(
            Commit(
                sha=parts[0],
                timestamp=stamp,
                subject=parts[3],
                files=files,
                parents=len(parts[2].split()),
            )
        )
    return commits


# ---------------------------------------------------------------------------
# Graph
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class _Edge:
    weight: float = 0.0
    names: set[str] = field(default_factory=set)


@dataclass(frozen=True, slots=True)
class _Step:
    """One move of a walk under a task profile."""

    target: str
    probability: float
    relation: str
    direction: str  # "out": along the edge; "in": back from the used file to its user
    names: tuple[str, ...]


class CodeGraph:
    """Typed, directed, weighted file graph with per-task transition tables."""

    def __init__(self, nodes: Iterable[str], tests: Iterable[str] = ()) -> None:
        self.nodes: set[str] = set(nodes)
        self.tests: set[str] = set(tests) & self.nodes
        self.history_commits = 0
        self._out: dict[str, dict[tuple[str, str], _Edge]] = defaultdict(dict)
        self._in: dict[str, dict[tuple[str, str], _Edge]] = defaultdict(dict)
        self._transitions: dict[str, dict[str, list[_Step]]] = {}

    def add(
        self,
        source: str,
        target: str,
        relation: str,
        weight: float,
        names: Iterable[str] = (),
    ) -> None:
        """Add evidence for ``source -relation-> target``; repeats accumulate."""
        if source == target or weight <= 0:
            return
        if source not in self.nodes or target not in self.nodes:
            return
        edge = self._out[source].get((target, relation))
        if edge is None:
            edge = _Edge()
            self._out[source][(target, relation)] = edge
            self._in[target][(source, relation)] = edge
        edge.weight += weight
        for name in names:
            if len(edge.names) >= 3:
                break
            edge.names.add(name)
        self._transitions.clear()

    def edge(self, source: str, target: str, relation: str) -> tuple[float, tuple[str, ...]] | None:
        """Weight and evidence names of one edge, or None when absent."""
        found = self._out.get(source, {}).get((target, relation))
        if found is None:
            return None
        return found.weight, tuple(sorted(found.names))

    def relation_counts(self) -> dict[str, int]:
        """Number of distinct edges per relation (symmetric ones counted once)."""
        counts: dict[str, int] = defaultdict(int)
        for edges in self._out.values():
            for _, relation in edges:
                counts[relation] += 1
        for relation in _SYMMETRIC:
            counts[relation] //= 2
        return {relation: counts[relation] for relation in RELATIONS if counts.get(relation)}

    def transitions(self, task: str) -> dict[str, list[_Step]]:
        """Row-normalized moves out of every node under ``task``'s profile."""
        cached = self._transitions.get(task)
        if cached is not None:
            return cached
        profile = PROFILES[task]
        table: dict[str, list[_Step]] = {}
        for node in self.nodes:
            weights: dict[str, float] = defaultdict(float)
            strongest: dict[str, tuple[float, str, str, tuple[str, ...]]] = {}

            def _take(
                other: str, weight: float, relation: str, direction: str, edge: _Edge,
                weights: dict[str, float] = weights,
                strongest: dict[str, tuple[float, str, str, tuple[str, ...]]] = strongest,
            ) -> None:
                if weight <= 0:
                    return
                weights[other] += weight
                if weight > strongest.get(other, (0.0,))[0]:
                    strongest[other] = (weight, relation, direction, tuple(sorted(edge.names)))

            for (target, relation), edge in self._out.get(node, {}).items():
                _take(target, profile.forward.get(relation, 0.0) * edge.weight, relation, "out", edge)
            for (source, relation), edge in self._in.get(node, {}).items():
                if relation in _SYMMETRIC:
                    continue
                _take(source, profile.backward.get(relation, 0.0) * edge.weight, relation, "in", edge)
            total = sum(weights.values())
            if total <= 0:
                continue
            table[node] = [
                _Step(
                    target=other,
                    probability=weight / total,
                    relation=strongest[other][1],
                    direction=strongest[other][2],
                    names=strongest[other][3],
                )
                for other, weight in sorted(weights.items())
            ]
        self._transitions[task] = table
        return table


def _relation_for(graph: CodeGraph, source: str, target: str, relation: str) -> str:
    """A use from a test file of a non-test file is a ``tests`` edge."""
    if source in graph.tests and target not in graph.tests:
        return REL_TESTS
    return relation


def add_co_changes(
    graph: CodeGraph,
    commits: Iterable[Commit],
    *,
    max_files: int = MAX_FILES_PER_COMMIT,
) -> int:
    """Add ``co_change`` edges from ``commits``; returns how many were used.

    Each commit spreads one unit of evidence over the partners of every file it
    touched (``1 / (n - 1)``), so a focused two-file commit says more about a
    pair than a twenty-file one. Root commits and commits over ``max_files``
    are skipped as bulk imports or sweeps. Files that are no longer indexed are
    ignored.
    """
    used = 0
    for commit in commits:
        if commit.parents == 0:
            continue
        files = sorted({path for path in commit.files if path in graph.nodes})
        if len(files) < 2 or len(files) > max_files:
            continue
        share = 1.0 / (len(files) - 1)
        for left, right in combinations(files, 2):
            graph.add(left, right, REL_CO_CHANGE, share)
            graph.add(right, left, REL_CO_CHANGE, share)
        used += 1
    graph.history_commits += used
    return used


def build_graph(
    index: Any,
    commits: Sequence[Commit] = (),
    *,
    max_files: int = MAX_FILES_PER_COMMIT,
) -> CodeGraph:
    """Typed file graph of ``index`` (a :class:`CodeIndex`), plus co-changes."""
    nodes = list(index.entries)
    graph = CodeGraph(nodes, tests=(path for path in nodes if _looks_like_test(path)))
    for source, target in index.edges():
        graph.add(source, target, _relation_for(graph, source, target, REL_IMPORTS), 1.0)
    for source, target, kind, count, names in index.file_call_edges():
        relation = _relation_for(graph, source, target, _KIND_RELATION.get(kind, REL_CALLS))
        graph.add(source, target, relation, 1.0 + math.log(max(count, 1)), names)
    add_co_changes(graph, commits, max_files=max_files)
    return graph


_GRAPHS: dict[str, tuple[tuple[Any, ...], CodeGraph]] = {}
_GRAPHS_LOCK = threading.Lock()


def graph_for(index: Any, *, history: bool = True, max_commits: int = DEFAULT_HISTORY) -> CodeGraph:
    """The graph of ``index``, rebuilt only when the tree or HEAD moved."""
    root = Path(index.root)
    head = git_head(root) if history else ""
    signature = (
        len(index.entries),
        sum(entry.mtime_ns for entry in index.entries.values()),
        head,
        max_commits if history else 0,
    )
    key = str(root)
    with _GRAPHS_LOCK:
        cached = _GRAPHS.get(key)
        if cached is not None and cached[0] == signature:
            return cached[1]
    commits = git_history(root, max_commits=max_commits) if history and head else []
    graph = build_graph(index, commits)
    with _GRAPHS_LOCK:
        _GRAPHS[key] = (signature, graph)
    return graph


# ---------------------------------------------------------------------------
# Seeds
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Seeds:
    """Where a ranking starts.

    ``explicit`` seeds were named by the caller and are left out of the
    results; seeds found from a text query stay in, since they are answers.
    """

    weights: dict[str, float]
    explicit: frozenset[str]
    notes: tuple[str, ...] = ()


def query_files(index: Any, query: str, *, limit: int = 8) -> list[str]:
    """Indexed code files matching ``query``: full-text first, symbol names as fallback."""
    text = query.strip()
    if not text:
        return []
    found: list[str] = []
    try:
        from navin.index.fulltext import FulltextIndex, fulltext_file_set

        fulltext = FulltextIndex(Path(index.root))
        if fulltext.available():
            fulltext.refresh(fulltext_file_set(index))
            for hit in fulltext.search(text, limit=limit * 3) or []:
                path = str(hit.get("path", ""))
                if path in index.entries and path not in found:
                    found.append(path)
    except Exception as exc:  # full-text is optional; symbol search still answers
        logger.debug("graph rank: full-text seed search failed: {}", exc)
    if not found:
        for token in _QUERY_TOKEN.findall(text):
            for loc in index.search(token, limit=3):
                if loc.path not in found:
                    found.append(loc.path)
    return found[:limit]


def resolve_seeds(
    index: Any,
    *,
    paths: Iterable[str] = (),
    symbols: Iterable[str] = (),
    query: str = "",
    query_limit: int = 8,
) -> Seeds:
    """Seed files with weights, from paths, symbol names and a text query."""
    weights: dict[str, float] = {}
    explicit: set[str] = set()
    notes: list[str] = []

    def _seed(path: str, weight: float, *, named: bool) -> None:
        weights[path] = max(weights.get(path, 0.0), weight)
        if named:
            explicit.add(path)

    for raw in paths:
        cleaned = normalize_relative_path(raw.strip())
        if not cleaned:
            continue
        if cleaned in index.entries:
            _seed(cleaned, 1.0, named=True)
            continue
        candidates = index.candidates_for(cleaned)
        if len(candidates) == 1:
            _seed(candidates[0], 1.0, named=True)
        elif candidates:
            notes.append(f"path {raw!r} is ambiguous: {', '.join(candidates[:5])}")
        else:
            notes.append(f"path {raw!r} is not an indexed code file")
    for name in symbols:
        needle = name.strip()
        if not needle:
            continue
        files = sorted({loc.path for loc in index.definition(needle)})
        if not files:
            notes.append(f"symbol {needle!r} has no definition in the index")
            continue
        for path in files:
            _seed(path, 1.0 / len(files), named=True)
    if query.strip():
        matches = query_files(index, query, limit=query_limit)
        if not matches:
            notes.append("the text query matched no indexed code file")
        for position, path in enumerate(matches):
            if path not in explicit:
                _seed(path, 0.5 / (position + 1), named=False)
    return Seeds(weights=weights, explicit=frozenset(explicit), notes=tuple(notes))


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EvidenceStep:
    """One edge on the path linking a seed to a ranked file."""

    source: str
    target: str
    relation: str
    direction: str
    names: tuple[str, ...] = ()

    def render(self) -> str:
        arrow = f"-{self.relation}->" if self.direction == "out" else f"<-{self.relation}-"
        tail = f" ({', '.join(self.names)})" if self.names else ""
        return f"{self.source} {arrow} {self.target}{tail}"


@dataclass(frozen=True, slots=True)
class RankedFile:
    path: str
    score: float
    is_test: bool
    is_seed: bool = False
    evidence: tuple[EvidenceStep, ...] = ()

    @property
    def symbols(self) -> tuple[str, ...]:
        """Symbols on the last edge into this file, the most specific evidence."""
        return self.evidence[-1].names if self.evidence else ()


@dataclass(frozen=True, slots=True)
class RankResult:
    task: str
    seeds: tuple[str, ...]
    files: tuple[RankedFile, ...]
    reached: int
    fallback: str = ""
    model_version: str = MODEL_VERSION

    def to_dict(self) -> dict[str, Any]:
        """The GraphIntelligenceResult wire shape (camelCase keys)."""
        return {
            "task": self.task,
            "seedNodes": list(self.seeds),
            "nodes": [
                {
                    "nodeId": item.path,
                    "file": item.path,
                    "symbol": item.symbols[0] if item.symbols else "",
                    "score": item.score,
                    "isTest": item.is_test,
                    "isSeed": item.is_seed,
                    "evidencePaths": [[step.render() for step in item.evidence]]
                    if item.evidence
                    else [],
                }
                for item in self.files
            ],
            "reached": self.reached,
            "fallback": self.fallback,
            "modelVersion": self.model_version,
        }


def _trace(
    table: Mapping[str, list[_Step]],
    seeds: Iterable[str],
    hops: int,
    targets: Iterable[str],
) -> dict[str, tuple[EvidenceStep, ...]]:
    """Most probable path of at most ``hops`` moves from any seed to each target.

    Layered max-product search: layer ``t`` holds the best path of exactly
    ``t`` moves. The cheapest layer of a target never repeats a node, since
    cutting the loop would give a path at least as probable in fewer moves.
    """
    layers: list[dict[str, float]] = [{seed: 0.0 for seed in seeds}]
    parents: list[dict[str, tuple[str, _Step]]] = [{}]
    for _ in range(hops):
        current: dict[str, float] = {}
        parent: dict[str, tuple[str, _Step]] = {}
        for node, cost in layers[-1].items():
            for step in table.get(node, ()):
                candidate = cost - math.log(step.probability)
                if candidate < current.get(step.target, math.inf):
                    current[step.target] = candidate
                    parent[step.target] = (node, step)
        if not current:
            break
        layers.append(current)
        parents.append(parent)
    traced: dict[str, tuple[EvidenceStep, ...]] = {}
    for target in targets:
        best_layer, best_cost = 0, math.inf
        for depth in range(1, len(layers)):
            cost = layers[depth].get(target)
            if cost is not None and cost < best_cost - 1e-12:
                best_layer, best_cost = depth, cost
        if not best_layer:
            continue
        steps: list[EvidenceStep] = []
        node = target
        for depth in range(best_layer, 0, -1):
            previous, step = parents[depth][node]
            steps.append(EvidenceStep(previous, node, step.relation, step.direction, step.names))
            node = previous
        traced[target] = tuple(reversed(steps))
    return traced


def rank(
    graph: CodeGraph,
    seeds: Mapping[str, float],
    *,
    task: str = "code_relevance",
    max_hops: int = 3,
    limit: int = 15,
    restart: float = 0.25,
    exclude: Iterable[str] = (),
    with_evidence: bool = True,
) -> RankResult:
    """Rank the files reachable from ``seeds`` for ``task``.

    Hop-limited Personalized PageRank: ``score = sum_t a (1 - a)^t x_t`` where
    ``x_0`` is the normalized seed distribution, ``x_t = x_(t-1) P`` under the
    task's transition table and ``t`` stops at ``max_hops``. Mass that reaches
    a file with no outgoing move simply stops, which only lowers its partners.
    Scores are scaled so the best result is 1.0.
    """
    if task not in PROFILES:
        raise ValueError(f"unknown task {task!r}; expected one of {', '.join(TASKS)}")
    profile = PROFILES[task]
    personal = {path: float(weight) for path, weight in seeds.items() if path in graph.nodes and weight > 0}
    if not personal:
        return RankResult(task, (), (), 0, fallback="no seed resolves to an indexed code file")
    total = sum(personal.values())
    personal = {path: weight / total for path, weight in personal.items()}
    seed_list = tuple(sorted(personal))
    hops = max(1, min(int(max_hops), MAX_HOPS))
    table = graph.transitions(task)

    scores: dict[str, float] = defaultdict(float)
    for path, mass in personal.items():
        scores[path] += restart * mass
    frontier: dict[str, float] = dict(personal)
    damping = restart
    for _ in range(hops):
        moved: dict[str, float] = defaultdict(float)
        for node, mass in frontier.items():
            for step in table.get(node, ()):
                moved[step.target] += mass * step.probability
        if not moved:
            break
        damping *= 1.0 - restart
        for node, mass in moved.items():
            scores[node] += damping * mass
        frontier = moved

    excluded = set(exclude)
    candidates = [
        (path, score)
        for path, score in scores.items()
        if path not in excluded and (not profile.tests_only or path in graph.tests)
    ]
    reached = len(scores)
    if not candidates:
        reason = (
            "no test file is linked to the seeds"
            if profile.tests_only
            else "the seeds have no structural neighbour for this task"
        )
        return RankResult(task, seed_list, (), reached, fallback=reason)
    candidates.sort(key=lambda item: (-item[1], item[0]))
    top = candidates[: max(1, int(limit))]
    best = top[0][1] or 1.0
    traced = (
        _trace(table, personal, hops, [path for path, _ in top if path not in personal])
        if with_evidence
        else {}
    )
    files = tuple(
        RankedFile(
            path=path,
            score=round(score / best, 4),
            is_test=path in graph.tests,
            is_seed=path in personal,
            evidence=traced.get(path, ()),
        )
        for path, score in top
    )
    return RankResult(task, seed_list, files, reached)
