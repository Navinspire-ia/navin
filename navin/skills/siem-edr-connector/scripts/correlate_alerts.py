#!/usr/bin/env python3
# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only
"""Correlate normalized (ECS-like) alerts into incidents by host or user in a sliding window.

Usage:
    correlate_alerts.py <alerts.jsonl> [--window-minutes 30] [--group-by host|user|both]
                        [--min-score 0] [--json] [--output incidents.md]

Input: the JSONL produced by normalize_events.py (fields @timestamp, host.name,
user.name, rule.name, event.severity_label, threat.tactic.name,
threat.technique.id). Alerts are deduplicated on (host, user, rule.name,
minute), sorted by time and chained while the gap to the previous alert of the
same entity is below the window. Each incident receives the distinct ATT&CK
tactics, a score (severity weights plus a bonus per distinct tactic and for
kill-chain progression), and a proposed triage decision.

Exit codes: 0 no incident at or above --min-score, 1 at least one incident
reported, 2 usage or input error.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SEVERITY_WEIGHT = {"informational": 1, "low": 2, "medium": 5, "high": 10, "critical": 20, "unknown": 2}

# ATT&CK Enterprise tactics in kill-chain order, with the names vendors emit.
TACTIC_ORDER = [
    "reconnaissance", "resource-development", "initial-access", "execution", "persistence",
    "privilege-escalation", "defense-evasion", "credential-access", "discovery",
    "lateral-movement", "collection", "command-and-control", "exfiltration", "impact",
]
TACTIC_ALIASES = {
    "initialaccess": "initial-access", "privilegeescalation": "privilege-escalation",
    "defenseevasion": "defense-evasion", "credentialaccess": "credential-access",
    "lateralmovement": "lateral-movement", "commandandcontrol": "command-and-control",
    "c2": "command-and-control", "resourcedevelopment": "resource-development",
    "malware": "execution", "ransomware": "impact", "suspiciousactivity": "execution",
    "exploit": "initial-access", "unwantedsoftware": "execution",
}


def canonical_tactic(raw: Any) -> list[str]:
    if raw is None:
        return []
    parts = [p for p in str(raw).replace(";", ",").split(",")]
    out: list[str] = []
    for part in parts:
        key = part.strip().lower().replace(" ", "-").replace("_", "-")
        compact = key.replace("-", "")
        if key in TACTIC_ORDER:
            name = key
        elif compact in TACTIC_ALIASES:
            name = TACTIC_ALIASES[compact]
        elif compact in {t.replace("-", "") for t in TACTIC_ORDER}:
            name = next(t for t in TACTIC_ORDER if t.replace("-", "") == compact)
        else:
            continue
        if name not in out:
            out.append(name)
    return out


def get_path(obj: Any, dotted: str) -> Any:
    cur: Any = obj
    for part in dotted.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


def parse_time(value: Any) -> datetime | None:
    if value is None:
        return None
    text = str(value).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


@dataclass
class Alert:
    when: datetime
    host: str
    user: str
    rule: str
    severity: str
    tactics: list[str]
    technique: str
    vendor: str


@dataclass
class Incident:
    key: str
    alerts: list[Alert] = field(default_factory=list)

    @property
    def start(self) -> datetime:
        return self.alerts[0].when

    @property
    def end(self) -> datetime:
        return self.alerts[-1].when

    def tactics(self) -> list[str]:
        seen: list[str] = []
        for a in self.alerts:
            for t in a.tactics:
                if t not in seen:
                    seen.append(t)
        return sorted(seen, key=TACTIC_ORDER.index)

    def techniques(self) -> list[str]:
        return sorted({a.technique for a in self.alerts if a.technique})

    def hosts(self) -> list[str]:
        return sorted({a.host for a in self.alerts if a.host})

    def users(self) -> list[str]:
        return sorted({a.user for a in self.alerts if a.user})

    def max_severity(self) -> str:
        return max((a.severity for a in self.alerts), key=lambda s: SEVERITY_WEIGHT.get(s, 0))

    def score(self) -> int:
        base = sum(SEVERITY_WEIGHT.get(a.severity, 2) for a in self.alerts)
        tactics = self.tactics()
        bonus = 5 * len(tactics)
        if len(tactics) >= 2:
            span = TACTIC_ORDER.index(tactics[-1]) - TACTIC_ORDER.index(tactics[0])
            bonus += 3 * span
        if len({a.vendor for a in self.alerts}) > 1:
            bonus += 10
        return base + bonus

    def decision(self) -> str:
        tactics = self.tactics()
        if self.max_severity() in ("high", "critical") and len(tactics) >= 2:
            return "likely true positive: escalate"
        if len(self.alerts) == 1 and self.max_severity() in ("informational", "low"):
            return "likely benign: close with note"
        if not tactics or len({a.rule for a in self.alerts}) == 1:
            return "needs data: pull process tree and network for the window"
        return "investigate: multiple rules on one entity"


def load_alerts(path: Path) -> tuple[list[Alert], int]:
    alerts: list[Alert] = []
    skipped = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        when = parse_time(record.get("@timestamp"))
        if when is None:
            skipped += 1
            continue
        alerts.append(Alert(
            when=when,
            host=short_host(get_path(record, "host.name")),
            user=str(get_path(record, "user.name") or "").lower(),
            rule=str(get_path(record, "rule.name") or get_path(record, "event.action") or "unnamed"),
            severity=str(get_path(record, "event.severity_label") or "unknown").lower(),
            tactics=canonical_tactic(get_path(record, "threat.tactic.name")),
            technique=str(get_path(record, "threat.technique.id") or ""),
            vendor=str(get_path(record, "observer.vendor") or "unknown"),
        ))
    return alerts, skipped


def short_host(value: Any) -> str:
    """Lower-case the host and drop the DNS suffix so WS-01 and ws-01.corp.local match."""
    if value is None:
        return ""
    text = str(value).strip().lower()
    if text.count(".") >= 1 and not text.replace(".", "").isdigit():
        text = text.split(".")[0]
    return text


def dedup(alerts: list[Alert]) -> tuple[list[Alert], int]:
    seen: set[tuple[str, str, str, str]] = set()
    out: list[Alert] = []
    for a in alerts:
        key = (a.host, a.user, a.rule, a.when.strftime("%Y-%m-%dT%H:%M"))
        if key in seen:
            continue
        seen.add(key)
        out.append(a)
    return out, len(alerts) - len(out)


def entity_keys(alert: Alert, mode: str) -> list[str]:
    keys: list[str] = []
    if mode in ("host", "both") and alert.host:
        keys.append(f"host:{alert.host}")
    if mode in ("user", "both") and alert.user:
        keys.append(f"user:{alert.user}")
    if not keys:
        keys.append(f"host:{alert.host or 'unknown'}")
    return keys


def correlate(alerts: list[Alert], window_minutes: int, mode: str) -> list[Incident]:
    alerts = sorted(alerts, key=lambda a: a.when)
    open_by_key: dict[str, Incident] = {}
    incidents: list[Incident] = []
    window = window_minutes * 60
    for alert in alerts:
        for key in entity_keys(alert, mode):
            current = open_by_key.get(key)
            if current is not None and (alert.when - current.end).total_seconds() <= window:
                current.alerts.append(alert)
            else:
                current = Incident(key=key, alerts=[alert])
                open_by_key[key] = current
                incidents.append(current)
    # In "both" mode an alert can sit in a host incident and a user incident; merge those
    # that share an alert so a single storyline is reported.
    merged: list[Incident] = []
    for inc in incidents:
        target = None
        for m in merged:
            if any(a is b for a in inc.alerts for b in m.alerts):
                target = m
                break
        if target is None:
            merged.append(inc)
        else:
            for a in inc.alerts:
                if not any(a is b for b in target.alerts):
                    target.alerts.append(a)
            target.alerts.sort(key=lambda a: a.when)
            target.key = f"{target.key}+{inc.key}"
    return sorted(merged, key=lambda i: i.score(), reverse=True)


def to_dict(inc: Incident) -> dict[str, Any]:
    return {
        "entity": inc.key,
        "start": inc.start.isoformat().replace("+00:00", "Z"),
        "end": inc.end.isoformat().replace("+00:00", "Z"),
        "alerts": len(inc.alerts),
        "hosts": inc.hosts(),
        "users": inc.users(),
        "rules": sorted({a.rule for a in inc.alerts}),
        "vendors": sorted({a.vendor for a in inc.alerts}),
        "max_severity": inc.max_severity(),
        "tactics": inc.tactics(),
        "techniques": inc.techniques(),
        "score": inc.score(),
        "decision": inc.decision(),
    }


def to_markdown(rows: list[dict[str, Any]], stats: dict[str, int]) -> str:
    lines = [
        "# Correlated incidents",
        "",
        f"Alerts read: {stats['alerts']}, deduplicated: {stats['deduplicated']}, skipped (no timestamp): {stats['skipped']}, incidents: {len(rows)}.",
        "",
        "| # | Entity | Window (UTC) | Alerts | Max severity | Tactics | Techniques | Score | Decision |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for i, r in enumerate(rows, 1):
        lines.append(
            f"| {i} | {r['entity']} | {r['start'][11:19]} to {r['end'][11:19]} {r['start'][:10]} | {r['alerts']} | {r['max_severity']} | "
            f"{', '.join(r['tactics']) or '-'} | {', '.join(r['techniques']) or '-'} | {r['score']} | {r['decision']} |"
        )
    lines.append("")
    for i, r in enumerate(rows, 1):
        lines.append(f"## Incident {i}: {r['entity']}")
        lines.append("")
        lines.append(f"- Hosts: {', '.join(r['hosts']) or '-'}; users: {', '.join(r['users']) or '-'}; vendors: {', '.join(r['vendors'])}")
        lines.append("- Rules: " + "; ".join(r["rules"]))
        lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Group normalized alerts into incidents per host/user within a time window.")
    parser.add_argument("input", help="normalized alerts JSONL")
    parser.add_argument("--window-minutes", type=int, default=30, help="max gap between chained alerts (default 30)")
    parser.add_argument("--group-by", choices=["host", "user", "both"], default="both")
    parser.add_argument("--min-score", type=int, default=0, help="only report incidents at or above this score")
    parser.add_argument("--json", action="store_true", help="JSON output instead of Markdown")
    parser.add_argument("--output", help="write the report to this file")
    args = parser.parse_args(argv)

    path = Path(args.input)
    if not path.is_file():
        print(f"error: {path} is not a file", file=sys.stderr)
        return 2
    if args.window_minutes <= 0:
        print("error: --window-minutes must be positive", file=sys.stderr)
        return 2
    try:
        alerts, skipped = load_alerts(path)
    except json.JSONDecodeError as exc:
        print(f"error: invalid JSONL: {exc}", file=sys.stderr)
        return 2
    total = len(alerts)
    alerts, removed = dedup(alerts)
    incidents = [to_dict(i) for i in correlate(alerts, args.window_minutes, args.group_by)]
    incidents = [i for i in incidents if i["score"] >= args.min_score]
    stats = {"alerts": total, "deduplicated": removed, "skipped": skipped, "incidents": len(incidents)}
    text = json.dumps({"stats": stats, "incidents": incidents}, indent=2) if args.json else to_markdown(incidents, stats)
    if args.output:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
        print(f"{len(incidents)} incidents -> {args.output}")
    else:
        print(text)
    return 1 if incidents else 0


if __name__ == "__main__":
    sys.exit(main())
