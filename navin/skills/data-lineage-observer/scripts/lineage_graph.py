#!/usr/bin/env python3
# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only
"""Build a lineage graph from OpenLineage RunEvents and query it.

Usage:
    lineage_graph.py <events.jsonl> stats [--format markdown|json|dot]
    lineage_graph.py <events.jsonl> impact <dataset> [--depth N] [--format ...]
    lineage_graph.py <events.jsonl> upstream <dataset> [--depth N] [--format ...]
    lineage_graph.py <events.jsonl> orphans [--format ...]

Output options (``--format`` or ``--json``) come after the subcommand.

The input holds one OpenLineage RunEvent per line (or a JSON array). Each
event carries ``job.namespace``, ``job.name``, ``inputs[]`` and ``outputs[]``
with ``namespace`` and ``name``. Edges are input dataset -> job -> output
dataset. A dataset is addressed as ``namespace/name`` or by its bare ``name``
when that is unambiguous.

``impact`` lists everything downstream of a dataset (jobs that read it and the
datasets they write, recursively), ``upstream`` walks the other way, ``orphans``
lists datasets that are written but never read and datasets read but never
written by any job in the file.

Exit codes:
    0  success (orphans: none found)
    1  orphans found, or impact/upstream found at least one dependent node
    2  usage or input error (unreadable file, unknown dataset, ambiguous name)
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

DATASET = "dataset"
JOB = "job"


class Graph:
    """Bipartite dataset/job graph with directed edges."""

    def __init__(self) -> None:
        self.kind: dict[str, str] = {}
        self.succ: dict[str, set[str]] = defaultdict(set)
        self.pred: dict[str, set[str]] = defaultdict(set)
        self.events = 0
        self.runs: set[str] = set()
        self.event_types: dict[str, int] = defaultdict(int)
        self.producers: set[str] = set()
        self.column_lineage_edges = 0

    def add(self, kind: str, node: str) -> None:
        self.kind.setdefault(node, kind)

    def edge(self, src: str, dst: str) -> None:
        self.succ[src].add(dst)
        self.pred[dst].add(src)

    def datasets(self) -> list[str]:
        return sorted(n for n, k in self.kind.items() if k == DATASET)

    def jobs(self) -> list[str]:
        return sorted(n for n, k in self.kind.items() if k == JOB)


def load_events(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8")
    stripped = text.strip()
    if not stripped:
        return []
    if stripped.startswith("["):
        payload = json.loads(stripped)
        return [e for e in payload if isinstance(e, dict)]
    events: list[dict[str, Any]] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"line {line_no}: invalid JSON ({exc.msg})") from exc
        if isinstance(obj, dict):
            events.append(obj)
    return events


def dataset_id(ds: dict[str, Any]) -> str:
    return f"{ds.get('namespace', '?')}/{ds.get('name', '?')}"


def job_id(job: dict[str, Any]) -> str:
    return f"{job.get('namespace', '?')}/{job.get('name', '?')}"


def build_graph(events: list[dict[str, Any]]) -> Graph:
    graph = Graph()
    for ev in events:
        graph.events += 1
        graph.event_types[str(ev.get("eventType") or "UNKNOWN")] += 1
        if ev.get("producer"):
            graph.producers.add(str(ev["producer"]))
        run = ev.get("run") or {}
        if isinstance(run, dict) and run.get("runId"):
            graph.runs.add(str(run["runId"]))
        job = ev.get("job")
        if not isinstance(job, dict):
            continue
        jid = job_id(job)
        graph.add(JOB, jid)
        for ds in ev.get("inputs") or []:
            if isinstance(ds, dict):
                did = dataset_id(ds)
                graph.add(DATASET, did)
                graph.edge(did, jid)
        for ds in ev.get("outputs") or []:
            if isinstance(ds, dict):
                did = dataset_id(ds)
                graph.add(DATASET, did)
                graph.edge(jid, did)
                facets = ds.get("facets") or {}
                col = facets.get("columnLineage") if isinstance(facets, dict) else None
                if isinstance(col, dict):
                    fields = col.get("fields") or {}
                    for spec in fields.values():
                        if isinstance(spec, dict):
                            graph.column_lineage_edges += len(spec.get("inputFields") or [])
    return graph


def resolve(graph: Graph, ref: str) -> str:
    if ref in graph.kind and graph.kind[ref] == DATASET:
        return ref
    matches = [d for d in graph.datasets() if d.split("/", 1)[1] == ref or d.endswith("/" + ref)]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise LookupError(f"unknown dataset {ref!r}; use namespace/name (known: {len(graph.datasets())} datasets)")
    raise LookupError(f"ambiguous dataset {ref!r}: " + ", ".join(matches))


def walk(graph: Graph, start: str, forward: bool, depth: int) -> list[dict[str, Any]]:
    """Breadth-first walk; depth counts dataset hops (a job plus its dataset is one hop)."""
    nxt = graph.succ if forward else graph.pred
    seen = {start}
    out: list[dict[str, Any]] = []
    queue: deque[tuple[str, int, str]] = deque([(start, 0, start)])
    while queue:
        node, hops, via = queue.popleft()
        if hops >= depth:
            continue
        for job in sorted(nxt.get(node, ())):
            for ds in sorted(nxt.get(job, ())):
                if ds in seen:
                    continue
                seen.add(ds)
                out.append({"dataset": ds, "hops": hops + 1, "via_job": job, "from": node})
                queue.append((ds, hops + 1, job))
            if hops + 1 <= depth and graph.kind.get(job) == JOB and not nxt.get(job):
                out.append({"dataset": None, "hops": hops + 1, "via_job": job, "from": node})
    return out


def orphans(graph: Graph) -> dict[str, list[str]]:
    never_read = [d for d in graph.datasets() if not graph.succ.get(d)]
    never_written = [d for d in graph.datasets() if not graph.pred.get(d)]
    return {"written_never_read": never_read, "read_never_written": never_written}


def to_dot(graph: Graph, highlight: set[str] | None = None) -> str:
    hl = highlight or set()
    lines = ["digraph lineage {", "  rankdir=LR;", "  node [fontname=Helvetica];"]
    for d in graph.datasets():
        color = ', style=filled, fillcolor="#ffe08a"' if d in hl else ""
        lines.append(f'  "{d}" [shape=box{color}];')
    for j in graph.jobs():
        lines.append(f'  "{j}" [shape=ellipse, style=rounded];')
    for src, dsts in sorted(graph.succ.items()):
        for dst in sorted(dsts):
            lines.append(f'  "{src}" -> "{dst}";')
    lines.append("}")
    return "\n".join(lines) + "\n"


def stats_payload(graph: Graph) -> dict[str, Any]:
    by_ns: dict[str, int] = defaultdict(int)
    for d in graph.datasets():
        by_ns[d.split("/", 1)[0]] += 1
    return {
        "events": graph.events,
        "runs": len(graph.runs),
        "event_types": dict(sorted(graph.event_types.items())),
        "jobs": len(graph.jobs()),
        "datasets": len(graph.datasets()),
        "edges": sum(len(v) for v in graph.succ.values()),
        "column_lineage_edges": graph.column_lineage_edges,
        "datasets_by_namespace": dict(sorted(by_ns.items())),
        "producers": sorted(graph.producers),
    }


def render_stats(payload: dict[str, Any]) -> str:
    lines = ["## Lineage graph stats", "", "| Metric | Value |", "|---|---:|"]
    for key in ("events", "runs", "jobs", "datasets", "edges", "column_lineage_edges"):
        lines.append(f"| {key} | {payload[key]} |")
    lines += ["", "| Event type | Count |", "|---|---:|"]
    for k, v in payload["event_types"].items():
        lines.append(f"| {k} | {v} |")
    lines += ["", "| Namespace | Datasets |", "|---|---:|"]
    for k, v in payload["datasets_by_namespace"].items():
        lines.append(f"| {k} | {v} |")
    lines += ["", "Producers: " + (", ".join(payload["producers"]) or "none")]
    return "\n".join(lines) + "\n"


def render_walk(title: str, start: str, rows: list[dict[str, Any]]) -> str:
    lines = [f"## {title} of {start}", "", "| Hops | Dataset | Via job | From |", "|---:|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['hops']} | {r['dataset'] or '(job without output)'} | {r['via_job']} | {r['from']} |")
    if not rows:
        lines.append("| - | none | - | - |")
    return "\n".join(lines) + "\n"


def render_orphans(payload: dict[str, list[str]]) -> str:
    lines = ["## Orphan datasets", "", "| Kind | Dataset |", "|---|---|"]
    for d in payload["written_never_read"]:
        lines.append(f"| written, never read | {d} |")
    for d in payload["read_never_written"]:
        lines.append(f"| read, never written (external source or missing producer) | {d} |")
    if not payload["written_never_read"] and not payload["read_never_written"]:
        lines.append("| - | none |")
    return "\n".join(lines) + "\n"


def build_parser() -> argparse.ArgumentParser:
    output = argparse.ArgumentParser(add_help=False)
    output.add_argument("--format", choices=("markdown", "json", "dot"), default=None, help="output format (default markdown)")
    output.add_argument("--json", action="store_const", const=True, default=None, help="same as --format json")
    parser = argparse.ArgumentParser(description="Lineage graph queries over OpenLineage RunEvents (output options go after the subcommand).")
    parser.add_argument("path", help="OpenLineage events (.jsonl, or a JSON array)")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("stats", help="counts of events, runs, jobs, datasets, edges", parents=[output])
    for name in ("impact", "upstream"):
        p = sub.add_parser(name, help=f"{name} walk from a dataset", parents=[output])
        p.add_argument("dataset", help="namespace/name or unambiguous name")
        p.add_argument("--depth", type=int, default=10, help="maximum dataset hops (default 10)")
    sub.add_parser("orphans", help="datasets never read, datasets never written", parents=[output])
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    fmt = "json" if args.json else (args.format or "markdown")
    path = Path(args.path)
    if not path.is_file():
        print(f"error: {path} is not a file", file=sys.stderr)
        return 2
    try:
        events = load_events(path)
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        print(f"error: cannot read {path}: {exc}", file=sys.stderr)
        return 2
    if not events:
        print(f"error: no events in {path}", file=sys.stderr)
        return 2
    graph = build_graph(events)

    if args.command == "stats":
        payload = stats_payload(graph)
        if fmt == "json":
            print(json.dumps(payload, indent=2))
        elif fmt == "dot":
            print(to_dot(graph), end="")
        else:
            print(render_stats(payload), end="")
        return 0

    if args.command == "orphans":
        payload = orphans(graph)
        if fmt == "json":
            print(json.dumps(payload, indent=2))
        elif fmt == "dot":
            print(to_dot(graph, set(payload["written_never_read"]) | set(payload["read_never_written"])), end="")
        else:
            print(render_orphans(payload), end="")
        return 1 if (payload["written_never_read"] or payload["read_never_written"]) else 0

    try:
        start = resolve(graph, args.dataset)
    except LookupError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    forward = args.command == "impact"
    rows = walk(graph, start, forward, max(1, args.depth))
    if fmt == "json":
        print(json.dumps({"start": start, "direction": "downstream" if forward else "upstream", "nodes": rows}, indent=2))
    elif fmt == "dot":
        print(to_dot(graph, {start} | {r["dataset"] for r in rows if r["dataset"]}), end="")
    else:
        print(render_walk("Downstream impact" if forward else "Upstream sources", start, rows), end="")
    return 1 if rows else 0


if __name__ == "__main__":
    sys.exit(main())
