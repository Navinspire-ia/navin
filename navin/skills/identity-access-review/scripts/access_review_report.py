#!/usr/bin/env python3
# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only
"""Build an access review report from an account export (CSV or JSON).

Usage:
    access_review_report.py <accounts.csv|accounts.json> [--dormant-days 90]
        [--key-age-days 90] [--as-of YYYY-MM-DD] [--leavers leavers.txt] [--json]

Expected columns or keys per account: ``user``, ``type`` (human | service |
break-glass), ``privileged`` (true/false), ``mfa`` (true/false), ``last_login``
(ISO date or empty), ``manager`` (owner for service accounts), ``status``
(active | disabled), ``key_age_days`` (integer, service accounts), optional
``hr_status`` (active | leaver) and ``source`` (entra, okta, aws, ...).

Checks: dormant accounts (no login for more than --dormant-days), orphaned
accounts (no manager or owner), privileged accounts without MFA, service keys
older than --key-age-days, leavers still active (hr_status=leaver or user listed
in --leavers), break-glass accounts used (any login).

Exit codes: 0 no finding, 1 at least one finding, 2 usage or input error.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import date, datetime
from pathlib import Path

TRUE_VALUES = {"true", "yes", "y", "1", "enabled", "on"}
REQUIRED = ("user", "type", "privileged", "mfa", "last_login", "manager", "status")


class InputError(ValueError):
    """Raised when the export cannot be used."""


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


def load_accounts(path: Path) -> list[dict[str, str]]:
    if path.suffix.lower() == ".json":
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise InputError(f"{path}: invalid JSON ({exc})") from exc
        rows = data.get("accounts") if isinstance(data, dict) else data
        if not isinstance(rows, list):
            raise InputError(f"{path}: expected a list of accounts or {{'accounts': [...]}}")
        accounts = [{str(k).lower(): "" if v is None else str(v) for k, v in row.items()} for row in rows]
    else:
        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            accounts = []
            for index, row in enumerate(reader):
                if None in row:
                    raise InputError(f"{path}: row {index + 2} has more values than the header")
                accounts.append({(k or "").strip().lower(): (v or "").strip() for k, v in row.items()})
    if not accounts:
        raise InputError(f"{path}: no account rows")
    missing = [c for c in REQUIRED if c not in accounts[0]]
    if missing:
        raise InputError(f"{path}: missing columns {missing}; required: {list(REQUIRED)}")
    return accounts


def load_leavers(path: Path | None) -> set[str]:
    if path is None:
        return set()
    return {line.strip().lower() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()}


def analyse(
    accounts: list[dict[str, str]],
    as_of: date,
    dormant_days: int,
    key_age_days: int,
    leavers: set[str],
) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []

    def add(check: str, severity: str, account: dict[str, str], detail: str) -> None:
        findings.append(
            {
                "check": check,
                "severity": severity,
                "user": account["user"],
                "type": account["type"],
                "source": account.get("source", ""),
                "detail": detail,
            }
        )

    for account in accounts:
        user = account["user"]
        kind = account["type"].lower()
        active = account["status"].lower() == "active"
        privileged = as_bool(account["privileged"])
        last_login = parse_date(account.get("last_login"))
        is_leaver = account.get("hr_status", "").lower() in {"leaver", "terminated", "left"} or user.lower() in leavers

        if is_leaver and active:
            add("leaver_active", "high", account, "HR status is leaver but the account is still active")
        if not account.get("manager"):
            add("orphaned", "high" if privileged else "medium", account, "no manager or owner recorded")
        if active and privileged and not as_bool(account["mfa"]) and kind != "service":
            add("privileged_no_mfa", "high", account, "privileged account without MFA")
        if active and kind != "break-glass":
            if last_login is None:
                add("dormant", "medium", account, "never logged in")
            else:
                idle = (as_of - last_login).days
                if idle > dormant_days:
                    add(
                        "dormant",
                        "high" if privileged else "medium",
                        account,
                        f"last login {last_login.isoformat()} ({idle} days ago, threshold {dormant_days})",
                    )
        if kind == "service" and account.get("key_age_days"):
            try:
                age = int(float(account["key_age_days"]))
            except ValueError as exc:
                raise InputError(f"{user}: key_age_days is not a number") from exc
            if age > key_age_days:
                add("stale_key", "high" if privileged else "medium", account, f"key is {age} days old (threshold {key_age_days})")
        if kind == "break-glass" and last_login is not None:
            add("break_glass_used", "medium", account, f"break-glass account logged in on {last_login.isoformat()}; check the ticket")
    order = {"high": 0, "medium": 1, "low": 2}
    findings.sort(key=lambda f: (order[f["severity"]], f["check"], f["user"]))
    return findings


def summary(accounts: list[dict[str, str]], findings: list[dict[str, str]]) -> dict[str, int]:
    privileged = [a for a in accounts if as_bool(a["privileged"])]
    return {
        "accounts": len(accounts),
        "active": sum(1 for a in accounts if a["status"].lower() == "active"),
        "privileged": len(privileged),
        "privileged_with_mfa": sum(1 for a in privileged if as_bool(a["mfa"])),
        "service_accounts": sum(1 for a in accounts if a["type"].lower() == "service"),
        "findings": len(findings),
        "high": sum(1 for f in findings if f["severity"] == "high"),
        "medium": sum(1 for f in findings if f["severity"] == "medium"),
    }


def render_markdown(stats: dict[str, int], findings: list[dict[str, str]], as_of: date) -> str:
    mfa_pct = round(100 * stats["privileged_with_mfa"] / stats["privileged"]) if stats["privileged"] else 100
    lines = [
        f"# Access review report (as of {as_of.isoformat()})",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Accounts | {stats['accounts']} |",
        f"| Active | {stats['active']} |",
        f"| Privileged | {stats['privileged']} |",
        f"| Privileged MFA coverage | {mfa_pct}% |",
        f"| Service accounts | {stats['service_accounts']} |",
        f"| Findings | {stats['findings']} (high {stats['high']}, medium {stats['medium']}) |",
        "",
    ]
    if not findings:
        lines.append("No finding.")
        return "\n".join(lines)
    lines += ["| Check | Severity | User | Type | Source | Detail |", "|---|---|---|---|---|---|"]
    for f in findings:
        lines.append(f"| {f['check']} | {f['severity']} | {f['user']} | {f['type']} | {f['source']} | {f['detail']} |")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Access review report from an account export.")
    parser.add_argument("accounts", type=Path, help="CSV or JSON export")
    parser.add_argument("--dormant-days", type=int, default=90, help="inactivity threshold (default 90)")
    parser.add_argument("--key-age-days", type=int, default=90, help="service key age threshold (default 90)")
    parser.add_argument("--as-of", type=str, default=None, help="reference date YYYY-MM-DD (default today)")
    parser.add_argument("--leavers", type=Path, default=None, help="text file with one leaver user id per line")
    parser.add_argument("--json", action="store_true", help="print JSON instead of Markdown")
    args = parser.parse_args(argv)
    try:
        as_of = parse_date(args.as_of) or date.today()
        accounts = load_accounts(args.accounts)
        leavers = load_leavers(args.leavers)
        findings = analyse(accounts, as_of, args.dormant_days, args.key_age_days, leavers)
    except (InputError, OSError, UnicodeDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    stats = summary(accounts, findings)
    if args.json:
        print(json.dumps({"as_of": as_of.isoformat(), "summary": stats, "findings": findings}, indent=2))
    else:
        print(render_markdown(stats, findings, as_of))
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
