#!/usr/bin/env python3
# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only
"""Compute SLI, error budget and multi-window burn rates from good/total buckets.

Usage:
    error_budget.py <buckets.csv|.json> --target 99.9 [--window-days 30]
                    [--now 2026-03-07T12:00:00Z] [--json]

Input: time buckets with columns ``timestamp`` (ISO 8601), ``good`` and
``total`` (event counts in that bucket). CSV with a header row, or a JSON
array of objects with the same keys (or ``{"buckets": [...]}``). Buckets of
any regular size work (1m, 5m, 1h); the long windows need enough history.

Computed over the rolling window ending at ``--now`` (default: last bucket):
  SLI = good / total, error budget = 1 - target, consumed = (1 - SLI) / (1 - target),
  burn rate over 1h, 6h, 1d, 3d = error ratio in that span / (1 - target),
  and the Google SRE workbook multi-window alerts:
    page   : burn >= 14.4 over 1h  and >= 14.4 over 5m
    page   : burn >= 6    over 6h  and >= 6    over 30m
    ticket : burn >= 3    over 1d  and >= 3    over 2h
    ticket : burn >= 1    over 3d  and >= 1    over 6h

Exit codes:
    0  budget remaining > 0 and no page-level alert active
    1  budget exhausted (consumed >= 100%) or a page alert is active
    2  usage or input error
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ALERTS: tuple[tuple[str, float, str, str], ...] = (
    ("page", 14.4, "1h", "5m"),
    ("page", 6.0, "6h", "30m"),
    ("ticket", 3.0, "1d", "2h"),
    ("ticket", 1.0, "3d", "6h"),
)

SPANS: dict[str, timedelta] = {
    "5m": timedelta(minutes=5),
    "30m": timedelta(minutes=30),
    "1h": timedelta(hours=1),
    "2h": timedelta(hours=2),
    "6h": timedelta(hours=6),
    "1d": timedelta(days=1),
    "3d": timedelta(days=3),
}


class Bucket:
    def __init__(self, when: datetime, good: float, total: float) -> None:
        self.when = when
        self.good = good
        self.total = total


def parse_time(value: str) -> datetime:
    text = value.strip().replace("Z", "+00:00")
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def load_buckets(path: Path) -> list[Bucket]:
    text = path.read_text(encoding="utf-8")
    rows: list[dict[str, Any]]
    if path.suffix.lower() == ".json" or text.lstrip().startswith(("[", "{")):
        payload = json.loads(text)
        if isinstance(payload, dict):
            payload = payload.get("buckets", [])
        if not isinstance(payload, list):
            raise ValueError("JSON must be an array of buckets or {\"buckets\": [...]}")
        rows = [r for r in payload if isinstance(r, dict)]
    else:
        rows = list(csv.DictReader(text.splitlines()))
    buckets: list[Bucket] = []
    for idx, row in enumerate(rows, start=1):
        try:
            when = parse_time(str(row["timestamp"]))
            good = float(row["good"])
            total = float(row["total"])
        except (KeyError, ValueError, TypeError) as exc:
            raise ValueError(f"row {idx}: need timestamp, good, total ({exc})") from exc
        if total < 0 or good < 0 or good > total:
            raise ValueError(f"row {idx}: need 0 <= good <= total")
        buckets.append(Bucket(when, good, total))
    buckets.sort(key=lambda b: b.when)
    return buckets


def bucket_size(buckets: list[Bucket]) -> timedelta:
    if len(buckets) < 2:
        return timedelta(minutes=1)
    deltas = sorted((b.when - a.when) for a, b in zip(buckets, buckets[1:]) if b.when > a.when)
    return deltas[len(deltas) // 2] if deltas else timedelta(minutes=1)


def ratio_in(buckets: list[Bucket], end: datetime, span: timedelta, size: timedelta) -> tuple[float | None, float, float]:
    """Error ratio over (end - span, end]; a bucket stamped t covers [t, t + size)."""
    start = end - span
    good = 0.0
    total = 0.0
    for b in buckets:
        if b.when + size > start and b.when < end:
            good += b.good
            total += b.total
    if total <= 0:
        return None, good, total
    return (total - good) / total, good, total


def evaluate(buckets: list[Bucket], target_pct: float, window_days: int, now: datetime) -> dict[str, Any]:
    target = target_pct / 100.0
    budget = 1.0 - target
    size = bucket_size(buckets)
    window = timedelta(days=window_days)

    err_ratio, good, total = ratio_in(buckets, now, window, size)
    sli = (good / total) if total > 0 else None
    consumed = (err_ratio / budget) if (err_ratio is not None and budget > 0) else None
    remaining = (1.0 - consumed) if consumed is not None else None
    bad_events = total - good
    allowed_bad = total * budget

    burn: dict[str, float | None] = {}
    coverage: dict[str, float] = {}
    for name, span in SPANS.items():
        r, _g, t = ratio_in(buckets, now, span, size)
        burn[name] = round(r / budget, 3) if (r is not None and budget > 0) else None
        coverage[name] = t

    alerts = []
    for severity, threshold, long_w, short_w in ALERTS:
        long_b = burn.get(long_w)
        short_b = burn.get(short_w)
        active = long_b is not None and short_b is not None and long_b >= threshold and short_b >= threshold
        hours_to_exhaust = (window_days * 24 / threshold) if threshold > 0 else None
        alerts.append(
            {
                "severity": severity,
                "threshold": threshold,
                "long_window": long_w,
                "short_window": short_w,
                "long_burn": long_b,
                "short_burn": short_b,
                "active": active,
                "budget_consumed_by_long_window_pct": round(threshold * SPANS[long_w].total_seconds() / window.total_seconds() * 100, 2),
                "time_to_exhaustion_hours_at_threshold": round(hours_to_exhaust, 1) if hours_to_exhaust else None,
            }
        )

    first = buckets[0].when
    history = now - first
    return {
        "target_pct": target_pct,
        "window_days": window_days,
        "now": now.isoformat().replace("+00:00", "Z"),
        "bucket_size_minutes": round(size.total_seconds() / 60, 2),
        "history_covers_window": history >= window,
        "history_days": round(history.total_seconds() / 86400, 2),
        "total_events": total,
        "good_events": good,
        "bad_events": bad_events,
        "allowed_bad_events": round(allowed_bad, 2),
        "sli_pct": round(sli * 100, 4) if sli is not None else None,
        "error_budget_pct": round(budget * 100, 4),
        "budget_consumed_pct": round(consumed * 100, 2) if consumed is not None else None,
        "budget_remaining_pct": round(remaining * 100, 2) if remaining is not None else None,
        "burn_rate": {k: burn[k] for k in ("1h", "6h", "1d", "3d")},
        "burn_rate_short": {k: burn[k] for k in ("5m", "30m", "2h", "6h")},
        "events_in_span": coverage,
        "alerts": alerts,
        "exhausted": consumed is not None and consumed >= 1.0,
        "page_active": any(a["active"] and a["severity"] == "page" for a in alerts),
    }


def fmt(value: float | None, suffix: str = "") -> str:
    return "n/a" if value is None else f"{value}{suffix}"


def render(report: dict[str, Any]) -> str:
    lines = [f"## SLO {report['target_pct']}% over {report['window_days']} days (rolling)", ""]
    lines.append(f"Evaluated at {report['now']}; bucket size {report['bucket_size_minutes']} min; history {report['history_days']} days"
                 + ("" if report["history_covers_window"] else " (shorter than the window: budget figures are partial)"))
    lines += ["", "| Metric | Value |", "|---|---:|"]
    lines.append(f"| SLI | {fmt(report['sli_pct'], '%')} |")
    lines.append(f"| Error budget | {report['error_budget_pct']}% |")
    lines.append(f"| Events good / total | {int(report['good_events'])} / {int(report['total_events'])} |")
    lines.append(f"| Bad events / allowed | {int(report['bad_events'])} / {report['allowed_bad_events']} |")
    lines.append(f"| Budget consumed | {fmt(report['budget_consumed_pct'], '%')} |")
    lines.append(f"| Budget remaining | {fmt(report['budget_remaining_pct'], '%')} |")
    lines += ["", "| Window | Burn rate |", "|---|---:|"]
    for k, v in report["burn_rate"].items():
        lines.append(f"| {k} | {fmt(v)} |")
    lines += ["", "| Severity | Threshold | Long window burn | Short window burn | Active | Budget used by long window | Time to exhaustion at threshold |", "|---|---:|---|---|---|---:|---:|"]
    for a in report["alerts"]:
        lines.append(
            f"| {a['severity']} | {a['threshold']}x | {a['long_window']}: {fmt(a['long_burn'])} | {a['short_window']}: {fmt(a['short_burn'])} | "
            f"{'YES' if a['active'] else 'no'} | {a['budget_consumed_by_long_window_pct']}% | {a['time_to_exhaustion_hours_at_threshold']} h |"
        )
    status = []
    if report["exhausted"]:
        status.append("error budget exhausted")
    if report["page_active"]:
        status.append("page-level burn active")
    lines += ["", "Status: " + ("; ".join(status) if status else "within budget, no page alert")]
    return "\n".join(lines) + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="SLI, error budget and burn rates from good/total time buckets.")
    parser.add_argument("path", help="CSV (timestamp,good,total) or JSON array of buckets")
    parser.add_argument("--target", type=float, required=True, help="SLO target in percent, e.g. 99.9")
    parser.add_argument("--window-days", type=int, default=30, help="rolling window length in days (default 30)")
    parser.add_argument("--now", default=None, help="evaluation time ISO 8601 (default: end of the last bucket)")
    parser.add_argument("--json", action="store_true", help="print JSON instead of Markdown")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not 0 < args.target < 100:
        print("error: --target must be between 0 and 100 (exclusive)", file=sys.stderr)
        return 2
    if args.window_days < 1:
        print("error: --window-days must be >= 1", file=sys.stderr)
        return 2
    path = Path(args.path)
    if not path.is_file():
        print(f"error: {path} is not a file", file=sys.stderr)
        return 2
    try:
        buckets = load_buckets(path)
    except (OSError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        print(f"error: cannot read {path}: {exc}", file=sys.stderr)
        return 2
    if not buckets:
        print(f"error: no buckets in {path}", file=sys.stderr)
        return 2
    try:
        now = parse_time(args.now) if args.now else buckets[-1].when + bucket_size(buckets)
    except ValueError:
        print("error: --now must be ISO 8601", file=sys.stderr)
        return 2
    report = evaluate(buckets, args.target, args.window_days, now)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(render(report), end="")
    return 1 if (report["exhausted"] or report["page_active"]) else 0


if __name__ == "__main__":
    sys.exit(main())
