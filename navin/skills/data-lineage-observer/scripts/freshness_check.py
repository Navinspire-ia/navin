#!/usr/bin/env python3
# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only
"""Check dataset freshness from OpenLineage RunEvents against expected cadences.

Usage:
    freshness_check.py <events.jsonl> <expectations.json> [--now 2026-03-07T12:00:00Z] [--json]

``expectations.json`` maps a dataset id (``namespace/name``) to an object with
``cadence_minutes`` (how often a COMPLETE run must write it) and an optional
``grace_minutes`` (default 0) and ``owner``. A dataset is stale when
``now - last_complete > cadence_minutes + grace_minutes``, or when no COMPLETE
event wrote it at all.

For each expected dataset the script reports the last COMPLETE event time, the
age, the producing job, the last run state of that job (COMPLETE, FAIL, ABORT,
RUNNING, START) and the ``errorMessage`` facet of a failed run.

``--now`` defaults to the latest eventTime in the file so that archived
exports evaluate consistently.

Exit codes:
    0  every expected dataset is fresh
    1  at least one stale dataset or a failed last run
    2  usage or input error
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


def parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    text = value.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def load_events(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8")
    stripped = text.strip()
    if not stripped:
        return []
    if stripped.startswith("["):
        return [e for e in json.loads(stripped) if isinstance(e, dict)]
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


def index_events(events: list[dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], datetime | None]:
    """Return (last COMPLETE write per dataset, last event per job, latest event time)."""
    last_complete: dict[str, dict[str, Any]] = {}
    last_job_event: dict[str, dict[str, Any]] = {}
    latest: datetime | None = None
    for ev in events:
        when = parse_time(ev.get("eventTime"))
        if when is None:
            continue
        if latest is None or when > latest:
            latest = when
        job = ev.get("job") if isinstance(ev.get("job"), dict) else {}
        jid = job_id(job) if job else "?"
        etype = str(ev.get("eventType") or "OTHER")
        run = ev.get("run") if isinstance(ev.get("run"), dict) else {}
        run_facets = run.get("facets") if isinstance(run.get("facets"), dict) else {}
        error = run_facets.get("errorMessage") if isinstance(run_facets, dict) else None
        message = error.get("message") if isinstance(error, dict) else None
        prev = last_job_event.get(jid)
        if prev is None or when >= prev["time"]:
            last_job_event[jid] = {"time": when, "eventType": etype, "runId": run.get("runId"), "errorMessage": message}
        if etype != "COMPLETE":
            continue
        for ds in ev.get("outputs") or []:
            if not isinstance(ds, dict):
                continue
            did = dataset_id(ds)
            facets = ds.get("outputFacets") if isinstance(ds.get("outputFacets"), dict) else {}
            stats = facets.get("outputStatistics") if isinstance(facets, dict) else None
            row_count = stats.get("rowCount") if isinstance(stats, dict) else None
            prev_ds = last_complete.get(did)
            if prev_ds is None or when > prev_ds["time"]:
                last_complete[did] = {"time": when, "job": jid, "runId": run.get("runId"), "rowCount": row_count}
    return last_complete, last_job_event, latest


def fmt_age(delta: timedelta) -> str:
    minutes = int(delta.total_seconds() // 60)
    hours, mins = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    if days:
        return f"{days}d {hours}h {mins}m"
    if hours:
        return f"{hours}h {mins}m"
    return f"{mins}m"


def evaluate(expectations: dict[str, Any], last_complete: dict[str, dict[str, Any]], last_job: dict[str, dict[str, Any]], now: datetime) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for did in sorted(expectations):
        spec = expectations[did] if isinstance(expectations[did], dict) else {}
        cadence = float(spec.get("cadence_minutes") or 0)
        grace = float(spec.get("grace_minutes") or 0)
        allowed = timedelta(minutes=cadence + grace)
        last = last_complete.get(did)
        job_state = None
        error = None
        if last is not None:
            state = last_job.get(last["job"]) or {}
            job_state = state.get("eventType")
            error = state.get("errorMessage")
        if last is None:
            age = None
            stale = True
            status = "NO COMPLETE RUN"
        else:
            age = now - last["time"]
            stale = age > allowed
            status = "STALE" if stale else "fresh"
        failed = job_state in ("FAIL", "ABORT")
        rows.append(
            {
                "dataset": did,
                "owner": spec.get("owner"),
                "cadence_minutes": cadence,
                "grace_minutes": grace,
                "last_complete": last["time"].isoformat().replace("+00:00", "Z") if last else None,
                "age": fmt_age(age) if age is not None else None,
                "age_minutes": round(age.total_seconds() / 60, 1) if age is not None else None,
                "producing_job": last["job"] if last else None,
                "last_row_count": last["rowCount"] if last else None,
                "last_job_state": job_state,
                "last_error": error,
                "status": status,
                "stale": stale,
                "failed_last_run": failed,
            }
        )
    return rows


def render(rows: list[dict[str, Any]], now: datetime) -> str:
    lines = ["## Dataset freshness", "", f"Evaluated at {now.isoformat().replace('+00:00', 'Z')}", ""]
    lines += ["| Dataset | Cadence | Last COMPLETE | Age | Producing job | Last job state | Status |", "|---|---:|---|---:|---|---|---|"]
    for r in rows:
        cadence = f"{int(r['cadence_minutes'])}m" + (f" +{int(r['grace_minutes'])}m" if r["grace_minutes"] else "")
        state = r["last_job_state"] or "-"
        if r["failed_last_run"] and r["last_error"]:
            state += f" ({str(r['last_error'])[:60]})"
        lines.append(
            f"| {r['dataset']} | {cadence} | {r['last_complete'] or '-'} | {r['age'] or '-'} | {r['producing_job'] or '-'} | {state} | {r['status']} |"
        )
    stale = sum(1 for r in rows if r["stale"])
    failed = sum(1 for r in rows if r["failed_last_run"])
    lines += ["", f"Stale: {stale} of {len(rows)}; failed last run: {failed}."]
    return "\n".join(lines) + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Freshness of datasets from OpenLineage events.")
    parser.add_argument("events", help="OpenLineage RunEvents (.jsonl or JSON array)")
    parser.add_argument("expectations", help="JSON: {\"namespace/name\": {\"cadence_minutes\": 60, \"grace_minutes\": 15}}")
    parser.add_argument("--now", default=None, help="evaluation time, ISO 8601 (default: latest eventTime in the file)")
    parser.add_argument("--json", action="store_true", help="print JSON instead of Markdown")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    events_path = Path(args.events)
    exp_path = Path(args.expectations)
    for p in (events_path, exp_path):
        if not p.is_file():
            print(f"error: {p} is not a file", file=sys.stderr)
            return 2
    try:
        events = load_events(events_path)
        expectations = json.loads(exp_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        print(f"error: cannot read input: {exc}", file=sys.stderr)
        return 2
    if not isinstance(expectations, dict) or not expectations:
        print("error: expectations must be a non-empty JSON object", file=sys.stderr)
        return 2
    if not events:
        print(f"error: no events in {events_path}", file=sys.stderr)
        return 2
    last_complete, last_job, latest = index_events(events)
    now = parse_time(args.now) if args.now else latest
    if now is None:
        print("error: --now is invalid or no event carries an eventTime", file=sys.stderr)
        return 2
    rows = evaluate(expectations, last_complete, last_job, now)
    breached = any(r["stale"] or r["failed_last_run"] for r in rows)
    if args.json:
        print(json.dumps({"now": now.isoformat().replace("+00:00", "Z"), "datasets": rows, "breached": breached}, indent=2))
    else:
        print(render(rows, now), end="")
    return 1 if breached else 0


if __name__ == "__main__":
    sys.exit(main())
