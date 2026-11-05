#!/usr/bin/env python3
# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only
"""Detect separation-of-duties (SoD) conflicts from an entitlement export.

Usage:
    sod_conflicts.py <entitlements.csv> <sod-matrix.json> [--json] [--fail-on high|medium|low]

The entitlements CSV needs the columns ``user`` and ``entitlement`` (one row per
grant; extra columns such as ``source`` or ``granted_at`` are kept as context).
The SoD matrix is a JSON file shaped like
``{"pairs": [{"a": "AP_CREATE_VENDOR", "b": "AP_APPROVE_PAYMENT",
"severity": "high", "domain": "finance", "rationale": "..."}]}``. Entitlement
names are matched case-insensitively; a pair value may also be a glob such as
``aws:AdministratorAccess*`` (fnmatch syntax).

Exit codes: 0 no conflict at or above the threshold, 1 conflicts at or above
the threshold (default: high), 2 usage or input error.
"""

from __future__ import annotations

import argparse
import csv
import fnmatch
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Iterable

SEVERITY_RANK = {"low": 1, "medium": 2, "high": 3}


class InputError(ValueError):
    """Raised when an input file cannot be used."""


def load_entitlements(path: Path) -> dict[str, set[str]]:
    """Return a mapping user -> set of entitlement names."""
    grants: dict[str, set[str]] = defaultdict(set)
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        fields = [f.strip().lower() for f in (reader.fieldnames or [])]
        if "user" not in fields or "entitlement" not in fields:
            raise InputError(f"{path}: columns 'user' and 'entitlement' are required, got {fields}")
        for row in reader:
            normalized = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
            user = normalized.get("user", "")
            entitlement = normalized.get("entitlement", "")
            if user and entitlement:
                grants[user].add(entitlement)
    if not grants:
        raise InputError(f"{path}: no entitlement rows found")
    return grants


def load_matrix(path: Path) -> list[dict[str, str]]:
    """Return the list of conflicting pairs, validated."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise InputError(f"{path}: invalid JSON ({exc})") from exc
    pairs = data.get("pairs") if isinstance(data, dict) else data
    if not isinstance(pairs, list) or not pairs:
        raise InputError(f"{path}: expected a non-empty 'pairs' list")
    result: list[dict[str, str]] = []
    for index, pair in enumerate(pairs):
        if not isinstance(pair, dict) or "a" not in pair or "b" not in pair:
            raise InputError(f"{path}: pair #{index} needs keys 'a' and 'b'")
        severity = str(pair.get("severity", "medium")).lower()
        if severity not in SEVERITY_RANK:
            raise InputError(f"{path}: pair #{index} has unknown severity {severity!r}")
        result.append(
            {
                "a": str(pair["a"]),
                "b": str(pair["b"]),
                "severity": severity,
                "domain": str(pair.get("domain", "general")),
                "rationale": str(pair.get("rationale", "")),
            }
        )
    return result


def matching(pattern: str, entitlements: Iterable[str]) -> list[str]:
    """Entitlements matching a literal name or an fnmatch pattern, case-insensitive."""
    needle = pattern.lower()
    return sorted(e for e in entitlements if fnmatch.fnmatchcase(e.lower(), needle))


def find_conflicts(grants: dict[str, set[str]], pairs: list[dict[str, str]]) -> list[dict[str, object]]:
    """Return one record per (user, pair) conflict."""
    conflicts: list[dict[str, object]] = []
    for user in sorted(grants):
        held = grants[user]
        for pair in pairs:
            left = matching(pair["a"], held)
            right = matching(pair["b"], held)
            # One entitlement matching both patterns is not a conflict with itself.
            right = [r for r in right if r not in left]
            if left and right:
                conflicts.append(
                    {
                        "user": user,
                        "severity": pair["severity"],
                        "domain": pair["domain"],
                        "entitlement_a": ", ".join(left),
                        "entitlement_b": ", ".join(right),
                        "rationale": pair["rationale"],
                    }
                )
    conflicts.sort(key=lambda c: (-SEVERITY_RANK[str(c["severity"])], str(c["user"])))
    return conflicts


def render_markdown(conflicts: list[dict[str, object]], users: int, pairs: int) -> str:
    counts = {sev: sum(1 for c in conflicts if c["severity"] == sev) for sev in ("high", "medium", "low")}
    lines = [
        "# SoD conflict report",
        "",
        f"Users analysed: {users} | Matrix pairs: {pairs} | Conflicts: {len(conflicts)} "
        f"(high {counts['high']}, medium {counts['medium']}, low {counts['low']})",
        "",
    ]
    if not conflicts:
        lines.append("No SoD conflict found.")
        return "\n".join(lines)
    lines += [
        "| User | Severity | Domain | Entitlement A | Entitlement B | Rationale |",
        "|---|---|---|---|---|---|",
    ]
    for c in conflicts:
        lines.append(
            f"| {c['user']} | {c['severity']} | {c['domain']} | {c['entitlement_a']} | "
            f"{c['entitlement_b']} | {c['rationale']} |"
        )
    by_user: dict[str, int] = defaultdict(int)
    for c in conflicts:
        by_user[str(c["user"])] += 1
    lines += ["", "## Conflicts per user", "", "| User | Conflicts |", "|---|---|"]
    for user, count in sorted(by_user.items(), key=lambda kv: (-kv[1], kv[0])):
        lines.append(f"| {user} | {count} |")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Detect SoD conflicts from an entitlement CSV and a SoD matrix.")
    parser.add_argument("entitlements", type=Path, help="CSV with columns user,entitlement")
    parser.add_argument("matrix", type=Path, help="JSON file with the conflicting pairs")
    parser.add_argument("--json", action="store_true", help="print JSON instead of Markdown")
    parser.add_argument(
        "--fail-on",
        choices=sorted(SEVERITY_RANK, key=SEVERITY_RANK.get),
        default="high",
        help="lowest severity that makes the script exit 1 (default: high)",
    )
    args = parser.parse_args(argv)
    try:
        grants = load_entitlements(args.entitlements)
        pairs = load_matrix(args.matrix)
    except (InputError, OSError, UnicodeDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    conflicts = find_conflicts(grants, pairs)
    threshold = SEVERITY_RANK[args.fail_on]
    breached = [c for c in conflicts if SEVERITY_RANK[str(c["severity"])] >= threshold]
    if args.json:
        print(
            json.dumps(
                {
                    "users": len(grants),
                    "pairs": len(pairs),
                    "conflicts": conflicts,
                    "fail_on": args.fail_on,
                    "breached": len(breached),
                },
                indent=2,
            )
        )
    else:
        print(render_markdown(conflicts, len(grants), len(pairs)))
    return 1 if breached else 0


if __name__ == "__main__":
    sys.exit(main())
