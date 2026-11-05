#!/usr/bin/env python3
# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only
"""Check a third-party (ICT provider) risk register for gaps.

Usage:
    register_check.py <register.csv|register.json> [--as-of YYYY-MM-DD]
        [--expiry-days 90] [--assessment-max-age-days 365] [--concentration-pct 30] [--json]

One row per contractual arrangement. Columns or keys (DORA register of
information vocabulary): ``vendor``, ``lei``, ``country`` (ISO 3166-1 alpha-2 of
the provider), ``service``, ``function_supported``, ``criticality`` (critical |
important | standard; "critical or important" is treated as critical),
``tier`` (optional, defaults from criticality), ``contract_ref``,
``contract_end`` (ISO date or empty for open-ended), ``notice_period_days``,
``annual_cost_eur``, ``data_location`` (country codes separated by ``;``),
``substitutability`` (easy | difficult | not substitutable), ``exit_plan``
(true/false), ``exit_plan_tested`` (ISO date), ``last_assessment`` (ISO date),
``soc2_expiry``, ``iso27001_expiry`` (ISO dates), ``dpa_signed`` (true/false),
``subcontractors`` (names separated by ``;``), ``incident_notification_hours``.

Checks: required fields per tier, contracts and certificates expiring within
--expiry-days, critical or important vendors without exit plan, with an
untested exit plan or without an assessment in the last
--assessment-max-age-days, concentration of annual cost by provider and by
country above --concentration-pct, critical vendors with notification above
24 hours or without DPA.

Exit codes: 0 no blocking gap, 1 at least one blocking gap, 2 usage or input
error. Blocking gaps are the findings with severity "blocking".
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

TRUE_VALUES = {"true", "yes", "y", "1", "oui"}
BASE_REQUIRED = ("vendor", "country", "service", "function_supported", "criticality", "contract_ref")
REQUIRED_BY_TIER = {
    "critical": BASE_REQUIRED
    + (
        "lei",
        "contract_end",
        "notice_period_days",
        "annual_cost_eur",
        "data_location",
        "substitutability",
        "exit_plan",
        "last_assessment",
        "dpa_signed",
        "incident_notification_hours",
    ),
    "important": BASE_REQUIRED
    + ("lei", "notice_period_days", "annual_cost_eur", "data_location", "substitutability", "last_assessment", "dpa_signed"),
    "standard": BASE_REQUIRED + ("annual_cost_eur", "data_location"),
}


class InputError(ValueError):
    """Raised when the register cannot be used."""


def as_bool(value: object) -> bool:
    return str(value).strip().lower() in TRUE_VALUES


def parse_date(value: object) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError as exc:
        raise InputError(f"unparseable date {text!r}") from exc


def parse_number(value: object, field: str, vendor: str) -> float:
    text = str(value or "").strip().replace(" ", "").replace(",", ".")
    if not text:
        return 0.0
    try:
        return float(text)
    except ValueError as exc:
        raise InputError(f"{vendor}: {field} is not a number ({value!r})") from exc


def tier_of(row: dict[str, str]) -> str:
    explicit = row.get("tier", "").strip().lower()
    if explicit in REQUIRED_BY_TIER:
        return explicit
    crit = row.get("criticality", "").strip().lower()
    if "critical" in crit or crit in {"yes", "true", "cif"}:
        return "critical"
    if crit == "important":
        return "important"
    return "standard"


def load_register(path: Path) -> list[dict[str, str]]:
    if path.suffix.lower() == ".json":
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise InputError(f"{path}: invalid JSON ({exc})") from exc
        rows = data.get("arrangements") if isinstance(data, dict) else data
        if not isinstance(rows, list):
            raise InputError(f"{path}: expected a list or {{'arrangements': [...]}}")
        register = [{str(k).lower(): "" if v is None else str(v) for k, v in row.items()} for row in rows]
    else:
        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            register = []
            for index, row in enumerate(reader):
                if None in row:
                    raise InputError(f"{path}: row {index + 2} has more values than the header (quote fields containing commas)")
                register.append({(k or "").strip().lower(): (v or "").strip() for k, v in row.items()})
    if not register:
        raise InputError(f"{path}: no rows")
    for index, row in enumerate(register):
        missing = [c for c in BASE_REQUIRED if c not in row]
        if missing:
            raise InputError(f"{path}: row {index + 1} lacks columns {missing}")
    return register


def analyse(
    register: list[dict[str, str]],
    as_of: date,
    expiry_days: int,
    assessment_max_age: int,
    concentration_pct: float,
) -> tuple[list[dict[str, str]], dict[str, object]]:
    findings: list[dict[str, str]] = []

    def add(check: str, severity: str, row: dict[str, str], detail: str) -> None:
        findings.append(
            {
                "check": check,
                "severity": severity,
                "vendor": row.get("vendor", ""),
                "tier": tier_of(row),
                "contract_ref": row.get("contract_ref", ""),
                "detail": detail,
            }
        )

    cost_by_vendor: dict[str, float] = defaultdict(float)
    cost_by_country: dict[str, float] = defaultdict(float)
    critical_by_vendor: dict[str, int] = defaultdict(int)
    total_cost = 0.0

    for row in register:
        vendor = row.get("vendor", "?")
        tier = tier_of(row)
        high_tier = tier in {"critical", "important"}
        blocking = "blocking" if tier == "critical" else "major"

        empty = [f for f in REQUIRED_BY_TIER[tier] if not row.get(f, "").strip()]
        if empty:
            add("incomplete", blocking if high_tier else "minor", row, f"missing fields for tier {tier}: {', '.join(empty)}")

        lei = row.get("lei", "").strip()
        if lei and (len(lei) != 20 or not lei.isalnum()):
            add("invalid_lei", "major", row, f"LEI {lei!r} is not 20 alphanumeric characters")

        contract_end = parse_date(row.get("contract_end"))
        if contract_end is not None:
            days = (contract_end - as_of).days
            if days < 0:
                add("contract_expired", blocking if high_tier else "major", row, f"contract ended {contract_end.isoformat()}")
            elif days <= expiry_days:
                add("contract_expiring", "major" if high_tier else "minor", row, f"contract ends {contract_end.isoformat()} ({days} days)")

        for cert in ("soc2_expiry", "iso27001_expiry"):
            cert_date = parse_date(row.get(cert))
            if cert_date is None:
                continue
            days = (cert_date - as_of).days
            label = cert.replace("_expiry", "").upper()
            if days < 0:
                add("certificate_expired", "major" if high_tier else "minor", row, f"{label} report or certificate expired {cert_date.isoformat()}")
            elif days <= expiry_days:
                add("certificate_expiring", "minor", row, f"{label} expires {cert_date.isoformat()} ({days} days)")

        if high_tier:
            if not as_bool(row.get("exit_plan")):
                add("no_exit_plan", blocking, row, "no documented exit strategy (DORA art. 28(8))")
            else:
                tested = parse_date(row.get("exit_plan_tested"))
                if tier == "critical" and (tested is None or (as_of - tested).days > 365):
                    add("exit_plan_untested", "major", row, "exit plan not tested in the last 365 days")
            last = parse_date(row.get("last_assessment"))
            if last is None:
                add("no_assessment", blocking, row, "no due diligence assessment recorded")
            elif (as_of - last).days > assessment_max_age:
                add("assessment_stale", blocking if tier == "critical" else "major", row, f"last assessment {last.isoformat()} is older than {assessment_max_age} days")
            if row.get("substitutability", "").strip().lower() in {"not substitutable", "none", "no"}:
                add("not_substitutable", "major", row, "provider declared not substitutable; exit plan must cover in-house or dual sourcing")
            if row.get("dpa_signed", "").strip() and not as_bool(row.get("dpa_signed")):
                add("no_dpa", blocking, row, "personal data processed without a signed DPA (RGPD art. 28)")
            hours_text = row.get("incident_notification_hours", "").strip()
            if hours_text:
                hours = parse_number(hours_text, "incident_notification_hours", vendor)
                if hours > 24:
                    add("slow_incident_notification", "major", row, f"contractual incident notification is {int(hours)}h (expect 24h or less)")
            for loc in [c.strip().upper() for c in row.get("data_location", "").split(";") if c.strip()]:
                if loc not in EEA_AND_ADEQUATE:
                    add("data_outside_eea", "minor", row, f"data stored in {loc}: transfer mechanism (SCC, adequacy) must be on file")

        cost = parse_number(row.get("annual_cost_eur"), "annual_cost_eur", vendor)
        total_cost += cost
        cost_by_vendor[vendor] += cost
        cost_by_country[row.get("country", "").strip().upper() or "??"] += cost
        if tier == "critical":
            critical_by_vendor[vendor] += 1

    concentration: list[dict[str, object]] = []
    if total_cost > 0:
        for label, table in (("vendor", cost_by_vendor), ("country", cost_by_country)):
            for key, cost in sorted(table.items(), key=lambda kv: -kv[1]):
                share = round(100 * cost / total_cost, 1)
                concentration.append({"dimension": label, "key": key, "annual_cost_eur": round(cost, 2), "share_pct": share})
                if share > concentration_pct:
                    findings.append(
                        {
                            "check": f"concentration_{label}",
                            "severity": "major",
                            "vendor": key,
                            "tier": "-",
                            "contract_ref": "-",
                            "detail": f"{share}% of ICT third-party spend on one {label} (threshold {concentration_pct}%)",
                        }
                    )
    for vendor, count in critical_by_vendor.items():
        if count >= 2:
            findings.append(
                {
                    "check": "concentration_critical_functions",
                    "severity": "major",
                    "vendor": vendor,
                    "tier": "critical",
                    "contract_ref": "-",
                    "detail": f"{count} critical or important functions depend on the same provider",
                }
            )

    order = {"blocking": 0, "major": 1, "minor": 2}
    findings.sort(key=lambda f: (order[f["severity"]], f["vendor"], f["check"]))
    stats: dict[str, object] = {
        "arrangements": len(register),
        "vendors": len(cost_by_vendor),
        "critical": sum(1 for r in register if tier_of(r) == "critical"),
        "important": sum(1 for r in register if tier_of(r) == "important"),
        "standard": sum(1 for r in register if tier_of(r) == "standard"),
        "total_annual_cost_eur": round(total_cost, 2),
        "findings": len(findings),
        "blocking": sum(1 for f in findings if f["severity"] == "blocking"),
        "major": sum(1 for f in findings if f["severity"] == "major"),
        "minor": sum(1 for f in findings if f["severity"] == "minor"),
        "concentration": concentration,
    }
    return findings, stats


# EEA members plus countries with an EU adequacy decision (state of 2025).
EEA_AND_ADEQUATE = {
    "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR", "HU", "IE", "IT", "LV", "LT", "LU",
    "MT", "NL", "PL", "PT", "RO", "SK", "SI", "ES", "SE", "IS", "LI", "NO",
    "AD", "AR", "CA", "FO", "GG", "IL", "IM", "JP", "JE", "NZ", "KR", "CH", "GB", "UY",
}


def render_markdown(stats: dict[str, object], findings: list[dict[str, str]], as_of: date) -> str:
    lines = [
        f"# Third-party risk register check (as of {as_of.isoformat()})",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Arrangements | {stats['arrangements']} |",
        f"| Providers | {stats['vendors']} |",
        f"| Critical / important / standard | {stats['critical']} / {stats['important']} / {stats['standard']} |",
        f"| Total annual cost (EUR) | {stats['total_annual_cost_eur']} |",
        f"| Findings | {stats['findings']} (blocking {stats['blocking']}, major {stats['major']}, minor {stats['minor']}) |",
        "",
    ]
    if findings:
        lines += ["## Findings", "", "| Check | Severity | Vendor | Tier | Contract | Detail |", "|---|---|---|---|---|---|"]
        for f in findings:
            lines.append(f"| {f['check']} | {f['severity']} | {f['vendor']} | {f['tier']} | {f['contract_ref']} | {f['detail']} |")
    else:
        lines.append("No finding.")
    concentration = stats.get("concentration")
    if isinstance(concentration, list) and concentration:
        lines += ["", "## Concentration", "", "| Dimension | Key | Annual cost (EUR) | Share |", "|---|---|---|---|"]
        for c in concentration:
            lines.append(f"| {c['dimension']} | {c['key']} | {c['annual_cost_eur']} | {c['share_pct']}% |")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check a third-party risk register for completeness, expiries and concentration.")
    parser.add_argument("register", type=Path, help="CSV or JSON register")
    parser.add_argument("--as-of", default=None, help="reference date YYYY-MM-DD (default today)")
    parser.add_argument("--expiry-days", type=int, default=90, help="horizon for expiring contracts and certificates")
    parser.add_argument("--assessment-max-age-days", type=int, default=365, help="maximum age of the last assessment")
    parser.add_argument("--concentration-pct", type=float, default=30.0, help="spend share that flags concentration")
    parser.add_argument("--json", action="store_true", help="print JSON instead of Markdown")
    args = parser.parse_args(argv)
    try:
        as_of = parse_date(args.as_of) or date.today()
        register = load_register(args.register)
        findings, stats = analyse(register, as_of, args.expiry_days, args.assessment_max_age_days, args.concentration_pct)
    except (InputError, OSError, UnicodeDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps({"as_of": as_of.isoformat(), "summary": stats, "findings": findings}, indent=2))
    else:
        print(render_markdown(stats, findings, as_of))
    return 1 if stats["blocking"] else 0


if __name__ == "__main__":
    sys.exit(main())
