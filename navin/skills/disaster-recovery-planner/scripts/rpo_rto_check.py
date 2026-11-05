#!/usr/bin/env python3
# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only
"""Compute achieved RTO and RPO per system from a disaster-recovery drill log.

Input: a JSON file with the shape

    {
      "exercise": "Q1 regional failover",
      "date": "2026-03-14",
      "systems": [
        {
          "name": "orders-db",
          "tier": "1",
          "target_rto_minutes": 60,
          "target_rpo_minutes": 15,
          "timestamps": {
            "incident_declared": "2026-03-14T09:00:00Z",
            "failover_decided": "2026-03-14T09:12:00Z",
            "restore_started": "2026-03-14T09:15:00Z",
            "service_restored": "2026-03-14T09:41:00Z",
            "service_validated": "2026-03-14T09:52:00Z"
          },
          "last_consistent_backup": "2026-03-14T08:50:00Z",
          "replica_lag_seconds": 42
        }
      ]
    }

Achieved RTO = service_validated (fallback service_restored) - incident_declared.
Achieved RPO = incident_declared - last_consistent_backup, or replica_lag_seconds
when no backup timestamp is given; when both are present the larger value is kept.
Timestamps are ISO 8601; a trailing Z is accepted. Naive timestamps are treated
as UTC.

Usage:
    rpo_rto_check.py DRILL.json [--json] [--warn-margin-pct N]

Exit codes:
    0  every system met its RTO and RPO targets
    1  at least one breach (or a system that could not be measured)
    2  usage error or unreadable input
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

PHASES = (
    ("decision", "incident_declared", "failover_decided"),
    ("preparation", "failover_decided", "restore_started"),
    ("restore", "restore_started", "service_restored"),
    ("validation", "service_restored", "service_validated"),
)


def parse_ts(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def minutes(delta: timedelta) -> float:
    return round(delta.total_seconds() / 60.0, 1)


def evaluate_system(system: dict[str, Any]) -> dict[str, Any]:
    name = str(system.get("name") or "<unnamed>")
    stamps_raw = system.get("timestamps") or {}
    stamps = {key: parse_ts(value) for key, value in stamps_raw.items()} if isinstance(stamps_raw, dict) else {}
    errors: list[str] = []
    for key, value in (stamps_raw.items() if isinstance(stamps_raw, dict) else []):
        if stamps.get(key) is None:
            errors.append(f"timestamp {key}={value!r} is not ISO 8601")

    target_rto = system.get("target_rto_minutes")
    target_rpo = system.get("target_rpo_minutes")
    if not isinstance(target_rto, (int, float)):
        errors.append("target_rto_minutes missing or not a number")
    if not isinstance(target_rpo, (int, float)):
        errors.append("target_rpo_minutes missing or not a number")

    declared = stamps.get("incident_declared")
    restored_end = stamps.get("service_validated") or stamps.get("service_restored")
    achieved_rto: float | None = None
    if declared and restored_end:
        achieved_rto = minutes(restored_end - declared)
        if achieved_rto < 0:
            errors.append("service restored before the incident was declared")
    else:
        errors.append("incident_declared and service_restored (or service_validated) are required for RTO")

    achieved_rpo: float | None = None
    rpo_source = ""
    backup = parse_ts(system.get("last_consistent_backup"))
    if backup and declared:
        achieved_rpo = minutes(declared - backup)
        rpo_source = "last_consistent_backup"
        if achieved_rpo < 0:
            errors.append("last_consistent_backup is after incident_declared")
    lag = system.get("replica_lag_seconds")
    if isinstance(lag, (int, float)):
        lag_minutes = round(lag / 60.0, 1)
        if achieved_rpo is None or lag_minutes > achieved_rpo:
            achieved_rpo = lag_minutes
            rpo_source = "replica_lag_seconds"
    if achieved_rpo is None:
        errors.append("last_consistent_backup or replica_lag_seconds is required for RPO")

    phases: dict[str, float | None] = {}
    for label, start, end in PHASES:
        a = stamps.get(start)
        b = stamps.get(end)
        phases[label] = minutes(b - a) if a and b else None

    rto_ok = achieved_rto is not None and isinstance(target_rto, (int, float)) and achieved_rto <= target_rto
    rpo_ok = achieved_rpo is not None and isinstance(target_rpo, (int, float)) and achieved_rpo <= target_rpo
    rto_margin = round(float(target_rto) - achieved_rto, 1) if achieved_rto is not None and isinstance(target_rto, (int, float)) else None
    rpo_margin = round(float(target_rpo) - achieved_rpo, 1) if achieved_rpo is not None and isinstance(target_rpo, (int, float)) else None
    measurable = not errors
    return {
        "name": name,
        "tier": str(system.get("tier") or ""),
        "target_rto_minutes": target_rto,
        "achieved_rto_minutes": achieved_rto,
        "rto_margin_minutes": rto_margin,
        "rto_status": "PASS" if rto_ok else "FAIL",
        "target_rpo_minutes": target_rpo,
        "achieved_rpo_minutes": achieved_rpo,
        "rpo_margin_minutes": rpo_margin,
        "rpo_source": rpo_source,
        "rpo_status": "PASS" if rpo_ok else "FAIL",
        "phases_minutes": phases,
        "passed": measurable and rto_ok and rpo_ok,
        "errors": errors,
    }


def fmt(value: float | int | None) -> str:
    return "-" if value is None else f"{value:g}"


def render_markdown(report: dict[str, Any], warn_pct: float) -> str:
    lines = [
        f"# RTO / RPO check: {report['exercise']}",
        "",
        f"- Date: {report['date'] or '-'}",
        f"- Systems: {report['system_count']}, breaches: {report['breach_count']}",
        f"- Overall: {'PASS' if report['passed'] else 'FAIL'}",
        "",
        "| System | Tier | RTO target | RTO achieved | RTO margin | RTO | RPO target | RPO achieved | RPO margin | RPO source | RPO | Result |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for s in report["systems"]:
        lines.append(
            f"| {s['name']} | {s['tier'] or '-'} | {fmt(s['target_rto_minutes'])} | {fmt(s['achieved_rto_minutes'])} | {fmt(s['rto_margin_minutes'])} | {s['rto_status']} "
            f"| {fmt(s['target_rpo_minutes'])} | {fmt(s['achieved_rpo_minutes'])} | {fmt(s['rpo_margin_minutes'])} | {s['rpo_source'] or '-'} | {s['rpo_status']} "
            f"| {'PASS' if s['passed'] else 'FAIL'} |"
        )
    lines.extend(["", "| System | decision | preparation | restore | validation |", "|---|---|---|---|---|"])
    for s in report["systems"]:
        p = s["phases_minutes"]
        lines.append(f"| {s['name']} | {fmt(p['decision'])} | {fmt(p['preparation'])} | {fmt(p['restore'])} | {fmt(p['validation'])} |")
    notes: list[str] = []
    for s in report["systems"]:
        for err in s["errors"]:
            notes.append(f"- {s['name']}: {err}")
        for kind in ("rto", "rpo"):
            margin = s[f"{kind}_margin_minutes"]
            target = s[f"target_{kind}_minutes"]
            if margin is not None and isinstance(target, (int, float)) and target > 0 and 0 <= margin < target * warn_pct / 100.0:
                notes.append(f"- {s['name']}: {kind.upper()} margin {margin:g} min is under {warn_pct:g} % of the target")
    if notes:
        lines.extend(["", "## Notes", "", *notes])
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Achieved RTO / RPO per system from a drill log.")
    parser.add_argument("drill", help="drill log JSON")
    parser.add_argument("--json", action="store_true", help="print JSON instead of Markdown")
    parser.add_argument("--warn-margin-pct", type=float, default=20.0, help="flag margins under this percentage of the target (default 20)")
    args = parser.parse_args(argv)
    path = Path(args.drill)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as exc:
        print(f"error: cannot read {path}: {exc}", file=sys.stderr)
        return 2
    except json.JSONDecodeError as exc:
        print(f"error: {path} is not valid JSON: {exc}", file=sys.stderr)
        return 2
    systems = data.get("systems") if isinstance(data, dict) else None
    if not isinstance(systems, list) or not systems:
        print("error: expected an object with a non-empty 'systems' list", file=sys.stderr)
        return 2
    results = [evaluate_system(s) for s in systems if isinstance(s, dict)]
    breaches = [r for r in results if not r["passed"]]
    report = {
        "exercise": str(data.get("exercise") or path.stem),
        "date": str(data.get("date") or ""),
        "system_count": len(results),
        "breach_count": len(breaches),
        "passed": not breaches,
        "systems": results,
    }
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(render_markdown(report, args.warn_margin_pct), end="")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
