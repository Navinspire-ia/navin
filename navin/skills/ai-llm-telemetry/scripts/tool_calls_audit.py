#!/usr/bin/env python3
# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only
"""Audit tool calls from an Opik span export or OpenTelemetry GenAI spans.

Usage:
    tool_calls_audit.py <spans.json|.jsonl> [--loop-threshold 3] [--top 10]
                        [--fail-on-loops] [--json]

Accepted inputs (auto-detected per record):
  * Opik spans (``GET /api/v1/private/spans`` response ``content`` list or a
    JSON array): fields id, trace_id, parent_span_id, name, type (llm, tool,
    general), input, output, start_time, end_time, error_info, usage.
  * OTel GenAI spans (OTLP JSON ``resourceSpans[].scopeSpans[].spans[]`` or a
    flat array): name ``execute_tool <name>``, traceId, spanId, parentSpanId,
    startTimeUnixNano, endTimeUnixNano, status.code, attributes list with
    gen_ai.operation.name, gen_ai.tool.name, gen_ai.tool.call.id,
    gen_ai.tool.call.arguments.

Reports per-tool frequency, failure rate and latency p50/p95, repeated
identical calls (same trace, tool and arguments: a loop signature) and the
longest tool chains per trace.

Exit codes:
    0  audit produced, no loop found (or loops found without --fail-on-loops)
    1  at least one loop found and --fail-on-loops is set
    2  usage or input error
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class ToolCall:
    """One normalised tool call."""

    def __init__(self, trace_id: str, tool: str, args_key: str, start_ms: float, duration_ms: float, failed: bool) -> None:
        self.trace_id = trace_id
        self.tool = tool
        self.args_key = args_key
        self.start_ms = start_ms
        self.duration_ms = duration_ms
        self.failed = failed


def load_json(path: Path) -> Any:
    text = path.read_text(encoding="utf-8")
    stripped = text.strip()
    if not stripped:
        return []
    whole_document_error: json.JSONDecodeError | None = None
    if stripped.startswith("[") or stripped.startswith("{"):
        try:
            return json.loads(stripped)
        except json.JSONDecodeError as exc:
            # Not one JSON document (an Opik or OTLP export): read it as JSON Lines
            # below, and keep this error for the message if that fails too.
            whole_document_error = exc
    records: list[Any] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError as exc:
            detail = f"line {line_no}: invalid JSON ({exc.msg})"
            if whole_document_error is not None:
                detail += (
                    f"; as one JSON document: {whole_document_error.msg}"
                    f" at line {whole_document_error.lineno} column {whole_document_error.colno}"
                )
            raise ValueError(detail) from exc
    return records


def flatten_spans(payload: Any) -> list[dict[str, Any]]:
    """Return a flat list of span dicts from Opik or OTLP shaped payloads."""
    if isinstance(payload, list):
        out: list[dict[str, Any]] = []
        for item in payload:
            out.extend(flatten_spans(item))
        return out
    if not isinstance(payload, dict):
        return []
    if "resourceSpans" in payload:
        spans: list[dict[str, Any]] = []
        for rs in payload.get("resourceSpans") or []:
            for ss in rs.get("scopeSpans") or []:
                spans.extend(s for s in ss.get("spans") or [] if isinstance(s, dict))
        return spans
    if "content" in payload and isinstance(payload["content"], list):
        return [s for s in payload["content"] if isinstance(s, dict)]
    if "spans" in payload and isinstance(payload["spans"], list):
        return [s for s in payload["spans"] if isinstance(s, dict)]
    return [payload]


def parse_iso_ms(value: Any) -> float | None:
    if not isinstance(value, str) or not value:
        return None
    text = value.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp() * 1000.0


def otel_attributes(span: dict[str, Any]) -> dict[str, Any]:
    attrs: dict[str, Any] = {}
    raw = span.get("attributes")
    if isinstance(raw, dict):
        return raw
    for item in raw or []:
        if not isinstance(item, dict) or "key" not in item:
            continue
        value = item.get("value")
        if isinstance(value, dict):
            value = next(iter(value.values()), None)
        attrs[str(item["key"])] = value
    return attrs


def canonical(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    except (TypeError, ValueError):
        return str(value)


def normalise(span: dict[str, Any]) -> ToolCall | None:
    """Turn one span into a ToolCall, or None when it is not a tool call."""
    if "startTimeUnixNano" in span or "traceId" in span:
        attrs = otel_attributes(span)
        name = str(span.get("name") or "")
        op = str(attrs.get("gen_ai.operation.name") or "")
        if op != "execute_tool" and not name.startswith("execute_tool"):
            return None
        tool = str(attrs.get("gen_ai.tool.name") or name.replace("execute_tool", "").strip() or "(unknown tool)")
        start_ns = float(span.get("startTimeUnixNano") or 0)
        end_ns = float(span.get("endTimeUnixNano") or start_ns)
        status = span.get("status") or {}
        code = status.get("code") if isinstance(status, dict) else None
        failed = code in (2, "2", "STATUS_CODE_ERROR", "ERROR")
        args_key = canonical(attrs.get("gen_ai.tool.call.arguments", attrs.get("gen_ai.tool.call.id")))
        return ToolCall(str(span.get("traceId") or "?"), tool, args_key, start_ns / 1e6, (end_ns - start_ns) / 1e6, failed)
    if str(span.get("type") or "").lower() != "tool":
        return None
    tool = str(span.get("name") or "(unknown tool)")
    start = parse_iso_ms(span.get("start_time"))
    end = parse_iso_ms(span.get("end_time"))
    if start is None:
        start = 0.0
    duration = (end - start) if end is not None else 0.0
    error_info = span.get("error_info")
    failed = bool(error_info)
    return ToolCall(str(span.get("trace_id") or "?"), tool, canonical(span.get("input")), start, duration, failed)


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(0, min(len(ordered) - 1, round((pct / 100.0) * (len(ordered) - 1))))
    return ordered[rank]


def audit(calls: list[ToolCall], loop_threshold: int, top: int) -> dict[str, Any]:
    per_tool: dict[str, list[ToolCall]] = defaultdict(list)
    per_trace: dict[str, list[ToolCall]] = defaultdict(list)
    for c in calls:
        per_tool[c.tool].append(c)
        per_trace[c.trace_id].append(c)

    tools = []
    for name, items in per_tool.items():
        durations = [c.duration_ms for c in items]
        failures = sum(1 for c in items if c.failed)
        tools.append(
            {
                "tool": name,
                "calls": len(items),
                "failures": failures,
                "failure_rate": round(failures / len(items), 4),
                "p50_ms": round(percentile(durations, 50), 1),
                "p95_ms": round(percentile(durations, 95), 1),
            }
        )
    tools.sort(key=lambda r: int(r["calls"]), reverse=True)

    loops = []
    for trace_id, items in per_trace.items():
        signatures = Counter((c.tool, c.args_key) for c in items)
        for (tool, args_key), count in signatures.items():
            if count >= loop_threshold:
                loops.append({"trace_id": trace_id, "tool": tool, "repeats": count, "arguments": args_key[:120]})
    loops.sort(key=lambda r: int(r["repeats"]), reverse=True)

    chains = []
    for trace_id, items in per_trace.items():
        ordered = sorted(items, key=lambda c: c.start_ms)
        chains.append(
            {
                "trace_id": trace_id,
                "tool_calls": len(ordered),
                "failures": sum(1 for c in ordered if c.failed),
                "sequence": " > ".join(c.tool for c in ordered[:12]) + (" > ..." if len(ordered) > 12 else ""),
            }
        )
    chains.sort(key=lambda r: int(r["tool_calls"]), reverse=True)

    return {
        "tool_calls": len(calls),
        "traces": len(per_trace),
        "failures": sum(1 for c in calls if c.failed),
        "tools": tools,
        "loops": loops,
        "longest_chains": chains[:top],
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = ["## Tool calls audit", ""]
    lines.append(f"- Tool calls: {report['tool_calls']} across {report['traces']} traces, {report['failures']} failed")
    lines.append(f"- Loops detected: {len(report['loops'])}")
    lines += ["", "### Per tool", "", "| Tool | Calls | Failures | Failure rate | p50 ms | p95 ms |", "|---|---:|---:|---:|---:|---:|"]
    for t in report["tools"]:
        lines.append(f"| {t['tool']} | {t['calls']} | {t['failures']} | {t['failure_rate'] * 100:.1f}% | {t['p50_ms']} | {t['p95_ms']} |")
    lines += ["", "### Repeated identical calls (loops)", ""]
    if report["loops"]:
        lines += ["| Trace | Tool | Repeats | Arguments |", "|---|---|---:|---|"]
        for lp in report["loops"]:
            args = str(lp["arguments"]).replace("|", "\\|")
            lines.append(f"| {lp['trace_id']} | {lp['tool']} | {lp['repeats']} | `{args}` |")
    else:
        lines.append("No repeated identical call above the threshold.")
    lines += ["", "### Longest chains", "", "| Trace | Tool calls | Failures | Sequence |", "|---|---:|---:|---|"]
    for ch in report["longest_chains"]:
        lines.append(f"| {ch['trace_id']} | {ch['tool_calls']} | {ch['failures']} | {ch['sequence']} |")
    return "\n".join(lines) + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Tool call frequency, failures, latency and loop detection from Opik or OTel GenAI spans.")
    parser.add_argument("path", help="span export (.json or .jsonl)")
    parser.add_argument("--loop-threshold", type=int, default=3, help="identical calls in one trace that count as a loop (default 3)")
    parser.add_argument("--top", type=int, default=10, help="number of longest chains to list (default 10)")
    parser.add_argument("--fail-on-loops", action="store_true", help="exit 1 when at least one loop is found")
    parser.add_argument("--json", action="store_true", help="print the report as JSON instead of Markdown")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    path = Path(args.path)
    if not path.is_file():
        print(f"error: {path} is not a file", file=sys.stderr)
        return 2
    try:
        spans = flatten_spans(load_json(path))
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        print(f"error: cannot read {path}: {exc}", file=sys.stderr)
        return 2
    calls = [c for c in (normalise(s) for s in spans) if c is not None]
    if not calls:
        print(f"error: no tool spans found in {path} (need type=tool or gen_ai.operation.name=execute_tool)", file=sys.stderr)
        return 2
    report = audit(calls, max(2, args.loop_threshold), max(1, args.top))
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(render_markdown(report), end="")
    return 1 if (args.fail_on_loops and report["loops"]) else 0


if __name__ == "__main__":
    sys.exit(main())
