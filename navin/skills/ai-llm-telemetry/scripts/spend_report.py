#!/usr/bin/env python3
# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only
"""Aggregate a LiteLLM ``GET /spend/logs`` export into cost and token reports.

Usage:
    spend_report.py <spend_logs.json|.jsonl> [--top 10] [--budget 25.0]
                    [--mad-threshold 3.5] [--json]

The input is either a JSON array, a JSON object with a ``data`` list (the raw
proxy response), or one JSON object per line (JSONL). Each record is a spend
log row: request_id, call_type, api_key, spend, total_tokens, prompt_tokens,
completion_tokens, startTime, endTime, model, user, team_id, end_user,
metadata, cache_hit.

Outputs cost and tokens per model, team, user and day, the top N most
expensive requests, the cache hit ratio, token percentiles and daily cost
anomalies flagged with the median absolute deviation (MAD).

Exit codes:
    0  report produced, no budget breach
    1  total spend above --budget, or at least one daily anomaly flagged
    2  usage or input error (unreadable file, no records)
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

MAD_SCALE = 1.4826  # makes the MAD comparable to a standard deviation


def load_records(path: Path) -> list[dict[str, Any]]:
    """Read a JSON array, a ``{"data": [...]}`` object or a JSONL file."""
    text = path.read_text(encoding="utf-8")
    stripped = text.strip()
    if not stripped:
        return []
    if stripped.startswith("[") or stripped.startswith("{"):
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, list):
            return [r for r in payload if isinstance(r, dict)]
        if isinstance(payload, dict):
            data = payload.get("data")
            if isinstance(data, list):
                return [r for r in data if isinstance(r, dict)]
            return [payload]
    records: list[dict[str, Any]] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"line {line_no}: invalid JSON ({exc.msg})") from exc
        if isinstance(obj, dict):
            records.append(obj)
    return records


def as_float(value: Any) -> float:
    if isinstance(value, bool) or value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value))
    except ValueError:
        return 0.0


def as_int(value: Any) -> int:
    return int(as_float(value))


def day_of(record: dict[str, Any]) -> str:
    start = str(record.get("startTime") or record.get("start_time") or "")
    return start[:10] if len(start) >= 10 else "unknown"


def label(value: Any, fallback: str) -> str:
    text = str(value).strip() if value is not None else ""
    return text or fallback


def percentile(values: list[int], pct: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    rank = max(0, min(len(ordered) - 1, round((pct / 100.0) * (len(ordered) - 1))))
    return ordered[rank]


def is_cache_hit(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes"}


def aggregate(records: list[dict[str, Any]], top: int, mad_threshold: float) -> dict[str, Any]:
    by_model: dict[str, dict[str, float]] = defaultdict(lambda: {"spend": 0.0, "requests": 0, "tokens": 0})
    by_team: dict[str, dict[str, float]] = defaultdict(lambda: {"spend": 0.0, "requests": 0, "tokens": 0})
    by_user: dict[str, dict[str, float]] = defaultdict(lambda: {"spend": 0.0, "requests": 0, "tokens": 0})
    by_day: dict[str, dict[str, float]] = defaultdict(lambda: {"spend": 0.0, "requests": 0, "tokens": 0})
    total_tokens: list[int] = []
    prompt_tokens: list[int] = []
    completion_tokens: list[int] = []
    cache_hits = 0
    total_spend = 0.0

    for rec in records:
        spend = as_float(rec.get("spend"))
        tokens = as_int(rec.get("total_tokens"))
        total_spend += spend
        total_tokens.append(tokens)
        prompt_tokens.append(as_int(rec.get("prompt_tokens")))
        completion_tokens.append(as_int(rec.get("completion_tokens")))
        if is_cache_hit(rec.get("cache_hit")):
            cache_hits += 1
        for bucket, key in (
            (by_model, label(rec.get("model"), "(no model)")),
            (by_team, label(rec.get("team_id"), "(no team)")),
            (by_user, label(rec.get("user") or rec.get("end_user"), "(no user)")),
            (by_day, day_of(rec)),
        ):
            bucket[key]["spend"] += spend
            bucket[key]["requests"] += 1
            bucket[key]["tokens"] += tokens

    top_requests = sorted(records, key=lambda r: as_float(r.get("spend")), reverse=True)[:top]
    top_rows = [
        {
            "request_id": label(r.get("request_id"), "?"),
            "model": label(r.get("model"), "?"),
            "team_id": label(r.get("team_id"), "-"),
            "user": label(r.get("user") or r.get("end_user"), "-"),
            "total_tokens": as_int(r.get("total_tokens")),
            "spend": round(as_float(r.get("spend")), 6),
            "startTime": label(r.get("startTime"), "-"),
        }
        for r in top_requests
    ]

    anomalies = daily_anomalies(by_day, mad_threshold)

    return {
        "records": len(records),
        "total_spend": round(total_spend, 6),
        "cache_hit_ratio": round(cache_hits / len(records), 4) if records else 0.0,
        "tokens": {
            "total_p50": percentile(total_tokens, 50),
            "total_p95": percentile(total_tokens, 95),
            "prompt_p50": percentile(prompt_tokens, 50),
            "prompt_p95": percentile(prompt_tokens, 95),
            "completion_p50": percentile(completion_tokens, 50),
            "completion_p95": percentile(completion_tokens, 95),
            "sum": sum(total_tokens),
        },
        "by_model": rows(by_model),
        "by_team": rows(by_team),
        "by_user": rows(by_user),
        "by_day": rows(by_day, sort_key="key"),
        "top_requests": top_rows,
        "daily_anomalies": anomalies,
    }


def rows(bucket: dict[str, dict[str, float]], sort_key: str = "spend") -> list[dict[str, Any]]:
    items = [
        {"key": k, "spend": round(v["spend"], 6), "requests": int(v["requests"]), "tokens": int(v["tokens"])}
        for k, v in bucket.items()
    ]
    if sort_key == "key":
        return sorted(items, key=lambda r: str(r["key"]))
    return sorted(items, key=lambda r: float(r["spend"]), reverse=True)


def daily_anomalies(by_day: dict[str, dict[str, float]], threshold: float) -> list[dict[str, Any]]:
    """Flag days whose spend deviates from the median by more than ``threshold`` scaled MADs."""
    days = sorted((d, v["spend"]) for d, v in by_day.items() if d != "unknown")
    if len(days) < 3:
        return []
    spends = [s for _, s in days]
    median = statistics.median(spends)
    mad = statistics.median(abs(s - median) for s in spends) * MAD_SCALE
    flagged: list[dict[str, Any]] = []
    for day, spend in days:
        if mad == 0:
            score = 0.0 if spend == median else float("inf")
        else:
            score = (spend - median) / mad
        if score > threshold:
            flagged.append({"day": day, "spend": round(spend, 6), "median": round(median, 6), "score": round(score, 2) if score != float("inf") else "inf"})
    return flagged


def table(title: str, items: list[dict[str, Any]], first_col: str) -> list[str]:
    out = [f"### {title}", "", f"| {first_col} | Requests | Tokens | Spend (USD) |", "|---|---:|---:|---:|"]
    for r in items:
        out.append(f"| {r['key']} | {r['requests']} | {r['tokens']} | {r['spend']:.4f} |")
    out.append("")
    return out


def render_markdown(report: dict[str, Any], budget: float | None) -> str:
    lines = ["## LiteLLM spend report", ""]
    lines.append(f"- Records: {report['records']}")
    lines.append(f"- Total spend: {report['total_spend']:.4f} USD")
    if budget is not None:
        state = "EXCEEDED" if report["total_spend"] > budget else "within budget"
        lines.append(f"- Budget: {budget:.4f} USD ({state})")
    lines.append(f"- Cache hit ratio: {report['cache_hit_ratio'] * 100:.1f}%")
    tok = report["tokens"]
    lines.append(
        f"- Tokens per request p50/p95: total {tok['total_p50']}/{tok['total_p95']}, "
        f"prompt {tok['prompt_p50']}/{tok['prompt_p95']}, completion {tok['completion_p50']}/{tok['completion_p95']}"
    )
    lines.append("")
    lines += table("Cost per model", report["by_model"], "Model")
    lines += table("Cost per team", report["by_team"], "Team")
    lines += table("Cost per user", report["by_user"], "User")
    lines += table("Cost per day", report["by_day"], "Day")
    lines += ["### Top requests", "", "| Request id | Model | Team | User | Tokens | Spend (USD) | Start |", "|---|---|---|---|---:|---:|---|"]
    for r in report["top_requests"]:
        lines.append(
            f"| {r['request_id']} | {r['model']} | {r['team_id']} | {r['user']} | {r['total_tokens']} | {r['spend']:.4f} | {r['startTime']} |"
        )
    lines += ["", "### Daily anomalies (MAD)", ""]
    if report["daily_anomalies"]:
        lines += ["| Day | Spend (USD) | Median (USD) | Score |", "|---|---:|---:|---:|"]
        for a in report["daily_anomalies"]:
            lines.append(f"| {a['day']} | {a['spend']:.4f} | {a['median']:.4f} | {a['score']} |")
    else:
        lines.append("No daily anomaly above the threshold.")
    return "\n".join(lines) + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Cost and token report from a LiteLLM /spend/logs export.")
    parser.add_argument("path", help="spend logs export (.json array, {data:[...]} object, or .jsonl)")
    parser.add_argument("--top", type=int, default=10, help="number of most expensive requests to list (default 10)")
    parser.add_argument("--budget", type=float, default=None, help="USD budget; exit 1 when total spend exceeds it")
    parser.add_argument("--mad-threshold", type=float, default=3.5, help="scaled MAD score above which a day is an anomaly (default 3.5)")
    parser.add_argument("--json", action="store_true", help="print the report as JSON instead of Markdown")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    path = Path(args.path)
    if not path.is_file():
        print(f"error: {path} is not a file", file=sys.stderr)
        return 2
    try:
        records = load_records(path)
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        print(f"error: cannot read {path}: {exc}", file=sys.stderr)
        return 2
    if not records:
        print(f"error: no spend records found in {path}", file=sys.stderr)
        return 2
    report = aggregate(records, max(1, args.top), args.mad_threshold)
    breached = args.budget is not None and report["total_spend"] > args.budget
    report["budget"] = args.budget
    report["budget_exceeded"] = breached
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(render_markdown(report, args.budget), end="")
    return 1 if (breached or report["daily_anomalies"]) else 0


if __name__ == "__main__":
    sys.exit(main())
