#!/usr/bin/env python3
# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only
"""Render a complete Markdown DR / BC exercise plan from a scenario JSON file.

Input shape:

    {
      "name": "Loss of eu-west-1 primary region",
      "type": "simulation",            # walkthrough | tabletop | simulation |
                                        # component_failover | full_failover |
                                        # backup_restore | chaos_game_day
      "date": "2026-04-18",
      "start_time": "09:00",
      "duration_minutes": 240,
      "environment": "production",
      "scope": "...",
      "objectives": ["..."],
      "scenario": "...",
      "systems": [{"name": "orders-db", "tier": "1", "target_rto_minutes": 60,
                   "target_rpo_minutes": 15, "strategy": "warm standby", "owner": "DBA on call"}],
      "injects": [{"offset_minutes": 0, "title": "...", "description": "...",
                   "expected_response": "...", "owner": "..."}],
      "roles": [{"role": "Exercise director", "name": "...", "contact": "..."}],
      "success_criteria": ["..."],
      "abort_criteria": ["..."],
      "communication": ["..."],
      "regulatory_drivers": ["DORA art. 25", "ISO 22301 8.5"]
    }

Only name, type, systems and injects are required; every other field gets a
sensible default. Output goes to stdout or to --out.

Usage:
    exercise_plan.py SCENARIO.json [--out PLAN.md] [--json]

Exit codes:
    0  plan rendered
    1  scenario incomplete (missing required field, unknown exercise type, inject without offset)
    2  usage error or unreadable input
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

EXERCISE_TYPES: dict[str, dict[str, str]] = {
    "walkthrough": {"label": "Plan walkthrough", "impact": "none", "cadence": "after every plan change, at least yearly"},
    "tabletop": {"label": "Tabletop", "impact": "none", "cadence": "twice a year, one per critical scenario"},
    "simulation": {"label": "Simulation (technical, non-production or isolated)", "impact": "none on production", "cadence": "yearly per critical system"},
    "component_failover": {"label": "Component failover", "impact": "limited, one component", "cadence": "quarterly for tier 1"},
    "full_failover": {"label": "Full site or region failover", "impact": "production traffic moves", "cadence": "yearly (DORA art. 25 critical functions)"},
    "backup_restore": {"label": "Backup restore test", "impact": "none (restore to isolated target)", "cadence": "quarterly per backup policy"},
    "chaos_game_day": {"label": "Chaos engineering game day", "impact": "controlled fault injection", "cadence": "quarterly"},
}

DEFAULT_ROLES = (
    ("Exercise director", "owns the scenario, releases injects, decides abort"),
    ("Incident commander", "runs the response as in a real incident"),
    ("Technical leads", "execute failover and restore steps per system"),
    ("Observer / scribe", "keeps the timestamped log and collects evidence; takes no action"),
    ("Communications lead", "status page, stakeholder and regulator notifications (exercise-tagged)"),
    ("Business validator", "confirms the service works from the user side"),
)

DEFAULT_ABORT = (
    "A real incident is declared anywhere in the organisation.",
    "Data loss beyond the agreed RPO is observed on a production system that was not in scope.",
    "A rollback step fails or the rollback path is no longer available.",
    "The exercise director or incident commander calls abort; the word is final and logged.",
)

EVIDENCE_ITEMS = (
    "Timestamped exercise log (observer notes) with UTC times for every inject and decision",
    "Incident ticket or channel export for the exercise, tagged EXERCISE",
    "Screenshots or CLI output of the failover command and of the health check that proved restoration",
    "Backup catalogue or replica status at T0 (last consistent recovery point) per system",
    "Monitoring dashboard export covering T0 to end of validation (error rate, latency, replication lag)",
    "Business validation checklist signed by the business validator",
    "Communication artefacts: status page updates, stakeholder emails, regulator notification drafts",
    "Rollback confirmation: primary site restored, replication re-established, data reconciled",
    "rpo_rto_check.py output (Markdown and JSON) computed from the log",
)


def fail(message: str) -> int:
    print(f"error: {message}", file=sys.stderr)
    return 1


def clock(start: str, offset: float) -> str:
    try:
        base = datetime.strptime(start, "%H:%M")
    except ValueError:
        return f"T+{offset:g} min"
    return (base + timedelta(minutes=offset)).strftime("%H:%M")


def build_plan(scenario: dict[str, Any]) -> tuple[dict[str, Any], str]:
    missing = [key for key in ("name", "type", "systems", "injects") if not scenario.get(key)]
    if missing:
        return {}, f"missing required field(s): {', '.join(missing)}"
    ex_type = str(scenario["type"]).strip().lower().replace("-", "_").replace(" ", "_")
    if ex_type not in EXERCISE_TYPES:
        return {}, f"unknown exercise type {scenario['type']!r}; expected one of {', '.join(EXERCISE_TYPES)}"
    injects = scenario["injects"]
    if not isinstance(injects, list) or any(not isinstance(i, dict) or not isinstance(i.get("offset_minutes"), (int, float)) for i in injects):
        return {}, "every inject needs a numeric offset_minutes"
    systems = scenario["systems"]
    if not isinstance(systems, list) or any(not isinstance(s, dict) or not s.get("name") for s in systems):
        return {}, "every system needs a name"

    start_time = str(scenario.get("start_time") or "09:00")
    duration = scenario.get("duration_minutes")
    if not isinstance(duration, (int, float)):
        duration = max(float(i["offset_minutes"]) for i in injects) + 60
    criteria: list[str] = []
    for s in systems:
        rto = s.get("target_rto_minutes")
        rpo = s.get("target_rpo_minutes")
        if isinstance(rto, (int, float)):
            criteria.append(f"{s['name']}: service restored and validated within {rto:g} min of incident declaration (RTO)")
        if isinstance(rpo, (int, float)):
            criteria.append(f"{s['name']}: data loss measured from the last consistent recovery point is at most {rpo:g} min (RPO)")
    criteria.extend(str(c) for c in scenario.get("success_criteria") or [])
    criteria.append("Every inject received the expected response without help from the exercise director")
    criteria.append("Rollback to the normal configuration completed and verified before the end of the window")

    plan = {
        "name": str(scenario["name"]),
        "type": ex_type,
        "type_info": EXERCISE_TYPES[ex_type],
        "date": str(scenario.get("date") or "<date>"),
        "start_time": start_time,
        "duration_minutes": float(duration),
        "environment": str(scenario.get("environment") or "<environment>"),
        "scope": str(scenario.get("scope") or "<scope: sites, systems, teams in and out of scope>"),
        "objectives": [str(o) for o in scenario.get("objectives") or []] or ["Validate that the recovery procedure restores the in-scope systems within their RTO and RPO"],
        "scenario": str(scenario.get("scenario") or "<narrative of the disruption>"),
        "systems": systems,
        "injects": sorted(injects, key=lambda i: float(i["offset_minutes"])),
        "roles": scenario.get("roles") or [],
        "success_criteria": criteria,
        "abort_criteria": [str(a) for a in scenario.get("abort_criteria") or []] or list(DEFAULT_ABORT),
        "communication": [str(c) for c in scenario.get("communication") or []],
        "regulatory_drivers": [str(r) for r in scenario.get("regulatory_drivers") or []],
        "evidence": list(EVIDENCE_ITEMS),
    }
    return plan, ""


def render(plan: dict[str, Any]) -> str:
    info = plan["type_info"]
    out = [
        f"# Exercise plan: {plan['name']}",
        "",
        "| Field | Value |",
        "|---|---|",
        f"| Type | {info['label']} (`{plan['type']}`) |",
        f"| Production impact | {info['impact']} |",
        f"| Recommended cadence | {info['cadence']} |",
        f"| Date and start | {plan['date']} {plan['start_time']} |",
        f"| Planned duration | {plan['duration_minutes']:g} min |",
        f"| Environment | {plan['environment']} |",
        f"| Regulatory drivers | {', '.join(plan['regulatory_drivers']) or '-'} |",
        "",
        "## Objectives",
        "",
        *[f"{n}. {o}" for n, o in enumerate(plan["objectives"], 1)],
        "",
        "## Scope",
        "",
        plan["scope"],
        "",
        "## Scenario",
        "",
        plan["scenario"],
        "",
        "## Systems in scope",
        "",
        "| System | Tier | Strategy | RTO target (min) | RPO target (min) | Owner |",
        "|---|---|---|---|---|---|",
    ]
    for s in plan["systems"]:
        out.append(
            f"| {s['name']} | {s.get('tier') or '-'} | {s.get('strategy') or '-'} | {s.get('target_rto_minutes', '-')} | {s.get('target_rpo_minutes', '-')} | {s.get('owner') or '-'} |"
        )
    out.extend(["", "## Roles", "", "| Role | Name | Contact | Responsibility |", "|---|---|---|---|"])
    named = {str(r.get("role", "")).lower(): r for r in plan["roles"] if isinstance(r, dict)}
    for role, duty in DEFAULT_ROLES:
        r = named.pop(role.lower(), {})
        out.append(f"| {role} | {r.get('name') or '<to assign>'} | {r.get('contact') or '-'} | {r.get('responsibility') or duty} |")
    for r in named.values():
        out.append(f"| {r.get('role')} | {r.get('name') or '<to assign>'} | {r.get('contact') or '-'} | {r.get('responsibility') or '-'} |")
    out.extend(["", "## Inject timeline", "", "| Clock | T+ (min) | Inject | Description | Expected response | Owner | Observed (fill during exercise) |", "|---|---|---|---|---|---|---|"])
    for i in plan["injects"]:
        off = float(i["offset_minutes"])
        out.append(
            f"| {clock(plan['start_time'], off)} | {off:g} | {i.get('title') or '-'} | {i.get('description') or '-'} | {i.get('expected_response') or '-'} | {i.get('owner') or '-'} | |"
        )
    out.append(f"| {clock(plan['start_time'], plan['duration_minutes'])} | {plan['duration_minutes']:g} | End of exercise | Rollback verified, hot debrief | All systems back to normal configuration | Exercise director | |")
    out.extend(["", "## Timestamps to record per system (UTC)", "", "| System | incident_declared | failover_decided | restore_started | service_restored | service_validated | last_consistent_backup / replica lag |", "|---|---|---|---|---|---|---|"])
    for s in plan["systems"]:
        out.append(f"| {s['name']} | | | | | | |")
    out.extend(["", "## Success criteria", "", *[f"- [ ] {c}" for c in plan["success_criteria"]]])
    out.extend(["", "## Abort and rollback criteria", "", *[f"- {a}" for a in plan["abort_criteria"]]])
    out.extend(["", "## Communication plan", ""])
    if plan["communication"]:
        out.extend(f"- {c}" for c in plan["communication"])
    else:
        out.extend(
            [
                "- Announce the exercise window to on-call teams and the service desk 48 h before; every message carries the prefix EXERCISE.",
                "- Status page and customer communications are drafted but not published unless the exercise type is full_failover and the business approved it.",
                "- Regulator notification templates are completed as a drill and kept as evidence, never sent.",
            ]
        )
    out.extend(["", "## Evidence checklist", "", *[f"- [ ] {e}" for e in plan["evidence"]]])
    out.extend(
        [
            "",
            "## After the exercise",
            "",
            "1. Hot debrief within 30 min: what worked, what did not, immediate risks.",
            "2. Run `rpo_rto_check.py` on the recorded timestamps; attach the output.",
            "3. After-action report within 10 working days with findings, owners and due dates.",
            "4. Update the DR plan, runbooks and BIA where the exercise proved them wrong.",
        ]
    )
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render a Markdown DR exercise plan from a scenario JSON.")
    parser.add_argument("scenario", help="scenario JSON file")
    parser.add_argument("--out", help="write the plan to this file instead of stdout")
    parser.add_argument("--json", action="store_true", help="print the normalised plan as JSON instead of Markdown")
    args = parser.parse_args(argv)
    path = Path(args.scenario)
    try:
        scenario = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as exc:
        print(f"error: cannot read {path}: {exc}", file=sys.stderr)
        return 2
    except json.JSONDecodeError as exc:
        print(f"error: {path} is not valid JSON: {exc}", file=sys.stderr)
        return 2
    if not isinstance(scenario, dict):
        print("error: scenario must be a JSON object", file=sys.stderr)
        return 2
    plan, problem = build_plan(scenario)
    if problem:
        return fail(problem)
    text = json.dumps(plan, indent=2) + "\n" if args.json else render(plan)
    if args.out:
        try:
            Path(args.out).write_text(text, encoding="utf-8")
        except OSError as exc:
            print(f"error: cannot write {args.out}: {exc}", file=sys.stderr)
            return 2
        print(f"plan written to {args.out}")
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
