#!/usr/bin/env python3
# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only
"""Score vendors: inherent risk x control effectiveness, tier and next review date.

Usage:
    vendor_risk_score.py <vendors.json> [--as-of YYYY-MM-DD] [--json] [--fail-tier critical]

Input: a JSON list (or ``{"vendors": [...]}``) of vendor attribute objects.

Inherent risk inputs (each scored 1 to 4):
    data_classification   public | internal | confidential | personal_sensitive
    function_criticality  support | business | important | critical
    access_level          none | read | write | privileged
    substitutability      easy | moderate | difficult | not_substitutable
    data_volume           low | medium | high | massive (records: <10k, <1M, <10M, more)
    subcontracting_depth  integer, number of sub-outsourcing levels (0 to 3+)

Control effectiveness inputs (true/false or date):
    soc2_type2 (bool), iso27001 (bool), pentest_last (ISO date), dpa_signed (bool),
    bcp_tested_last (ISO date), cyber_insurance (bool), incident_notification_hours (int),
    security_rating (0 to 100, e.g. SecurityScorecard or BitSight normalised),
    open_critical_findings (int)

Output per vendor: inherent score (1.0 to 4.0), control score (0 to 100),
residual score, tier (critical | important | standard), review cadence in
months and next review date computed from ``last_assessment``.

Exit codes: 0 ok, 1 when at least one vendor reaches --fail-tier (default:
critical) with a residual score of 3.0 or more, 2 usage or input error.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

SCALES: dict[str, dict[str, int]] = {
    "data_classification": {"public": 1, "internal": 2, "confidential": 3, "personal_sensitive": 4},
    "function_criticality": {"support": 1, "business": 2, "important": 3, "critical": 4},
    "access_level": {"none": 1, "read": 2, "write": 3, "privileged": 4},
    "substitutability": {"easy": 1, "moderate": 2, "difficult": 3, "not_substitutable": 4},
    "data_volume": {"low": 1, "medium": 2, "high": 3, "massive": 4},
}
WEIGHTS = {
    "data_classification": 0.25,
    "function_criticality": 0.30,
    "access_level": 0.15,
    "substitutability": 0.15,
    "data_volume": 0.05,
    "subcontracting_depth": 0.10,
}
CADENCE_MONTHS = {"critical": 12, "important": 24, "standard": 36}
TIER_RANK = {"standard": 0, "important": 1, "critical": 2}


class InputError(ValueError):
    """Raised when a vendor record is unusable."""


def parse_date(value: object) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError as exc:
        raise InputError(f"unparseable date {text!r}") from exc


def scale(vendor: dict[str, object], field: str) -> int:
    value = str(vendor.get(field, "")).strip().lower()
    table = SCALES[field]
    if value not in table:
        raise InputError(f"{vendor.get('name', '?')}: {field} must be one of {sorted(table)}, got {value!r}")
    return table[value]


def inherent_score(vendor: dict[str, object]) -> float:
    depth_raw = vendor.get("subcontracting_depth", 0)
    if not isinstance(depth_raw, int):
        raise InputError(f"{vendor.get('name', '?')}: subcontracting_depth must be an integer")
    depth_score = min(4, depth_raw + 1)
    total = sum(scale(vendor, f) * WEIGHTS[f] for f in SCALES) + depth_score * WEIGHTS["subcontracting_depth"]
    return round(total, 2)


def control_score(vendor: dict[str, object], as_of: date) -> tuple[int, list[str]]:
    """Return 0..100 and the list of control gaps."""
    score = 0
    gaps: list[str] = []

    def check(flag: bool, points: int, gap: str) -> None:
        nonlocal score
        if flag:
            score += points
        else:
            gaps.append(gap)

    check(bool(vendor.get("soc2_type2")), 20, "no SOC 2 Type II report")
    check(bool(vendor.get("iso27001")), 15, "no ISO 27001 certificate")
    pentest = parse_date(vendor.get("pentest_last"))
    check(pentest is not None and (as_of - pentest).days <= 365, 15, "no penetration test in the last 12 months")
    check(bool(vendor.get("dpa_signed")), 10, "no signed DPA (RGPD art. 28)")
    bcp = parse_date(vendor.get("bcp_tested_last"))
    check(bcp is not None and (as_of - bcp).days <= 365, 10, "BCP/DR not tested in the last 12 months")
    check(bool(vendor.get("cyber_insurance")), 5, "no cyber insurance evidence")
    hours = vendor.get("incident_notification_hours")
    check(isinstance(hours, (int, float)) and hours <= 24, 10, "incident notification clause above 24h or absent")
    rating = vendor.get("security_rating")
    if isinstance(rating, (int, float)):
        if rating >= 80:
            score += 10
        elif rating >= 60:
            score += 5
            gaps.append(f"external security rating {rating} below 80")
        else:
            gaps.append(f"external security rating {rating} below 60")
    else:
        gaps.append("no external security rating")
    open_findings = vendor.get("open_critical_findings", 0)
    if isinstance(open_findings, int) and open_findings > 0:
        score = max(0, score - 10 * open_findings)
        gaps.append(f"{open_findings} open critical finding(s)")
    check(bool(vendor.get("exit_plan")), 5, "no exit plan")
    return min(100, score), gaps


def residual(inherent: float, control: int) -> float:
    # Controls at 100% halve the inherent risk; controls at 0% leave it intact.
    return round(inherent * (1 - 0.5 * control / 100), 2)


def tier_for(vendor: dict[str, object], inherent: float) -> str:
    crit = str(vendor.get("function_criticality", "")).lower()
    if crit == "critical" or inherent >= 3.2:
        return "critical"
    if crit == "important" or inherent >= 2.4:
        return "important"
    return "standard"


def add_months(day: date, months: int) -> date:
    month = day.month - 1 + months
    year = day.year + month // 12
    month = month % 12 + 1
    last_day = (date(year + (month == 12), month % 12 + 1, 1) - timedelta(days=1)).day
    return date(year, month, min(day.day, last_day))


def score_vendor(vendor: dict[str, object], as_of: date) -> dict[str, object]:
    name = str(vendor.get("name", "")).strip()
    if not name:
        raise InputError("every vendor needs a 'name'")
    inherent = inherent_score(vendor)
    control, gaps = control_score(vendor, as_of)
    tier = tier_for(vendor, inherent)
    cadence = CADENCE_MONTHS[tier]
    last = parse_date(vendor.get("last_assessment"))
    next_review = add_months(last, cadence) if last else as_of
    return {
        "name": name,
        "inherent_score": inherent,
        "control_score": control,
        "residual_score": residual(inherent, control),
        "tier": tier,
        "review_cadence_months": cadence,
        "last_assessment": last.isoformat() if last else "",
        "next_review": next_review.isoformat(),
        "overdue": next_review < as_of,
        "control_gaps": gaps,
    }


def load_vendors(path: Path) -> list[dict[str, object]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise InputError(f"{path}: invalid JSON ({exc})") from exc
    vendors = data.get("vendors") if isinstance(data, dict) else data
    if not isinstance(vendors, list) or not vendors:
        raise InputError(f"{path}: expected a non-empty list of vendors")
    if not all(isinstance(v, dict) for v in vendors):
        raise InputError(f"{path}: every vendor must be an object")
    return vendors


def render_markdown(results: list[dict[str, object]], as_of: date) -> str:
    lines = [
        f"# Vendor risk scores (as of {as_of.isoformat()})",
        "",
        "| Vendor | Tier | Inherent | Controls | Residual | Last assessment | Next review | Overdue | Control gaps |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        gaps = "; ".join(str(g) for g in r["control_gaps"]) or "none"
        lines.append(
            f"| {r['name']} | {r['tier']} | {r['inherent_score']} | {r['control_score']}/100 | {r['residual_score']} | "
            f"{r['last_assessment'] or '-'} | {r['next_review']} | {'yes' if r['overdue'] else 'no'} | {gaps} |"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Score vendors and assign tiers and review dates.")
    parser.add_argument("vendors", type=Path, help="JSON file with vendor attributes")
    parser.add_argument("--as-of", default=None, help="reference date YYYY-MM-DD (default today)")
    parser.add_argument("--fail-tier", choices=sorted(TIER_RANK, key=TIER_RANK.get), default="critical", help="tier that triggers exit 1 when residual >= 3.0")
    parser.add_argument("--json", action="store_true", help="print JSON instead of Markdown")
    args = parser.parse_args(argv)
    try:
        as_of = parse_date(args.as_of) or date.today()
        vendors = load_vendors(args.vendors)
        results = [score_vendor(v, as_of) for v in vendors]
    except (InputError, OSError, UnicodeDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    results.sort(key=lambda r: (-TIER_RANK[str(r["tier"])], -float(str(r["residual_score"]))))
    threshold = TIER_RANK[args.fail_tier]
    breached = [
        r for r in results if TIER_RANK[str(r["tier"])] >= threshold and float(str(r["residual_score"])) >= 3.0
    ]
    if args.json:
        print(json.dumps({"as_of": as_of.isoformat(), "vendors": results, "breached": [r["name"] for r in breached]}, indent=2))
    else:
        print(render_markdown(results, as_of))
    return 1 if breached else 0


if __name__ == "__main__":
    sys.exit(main())
