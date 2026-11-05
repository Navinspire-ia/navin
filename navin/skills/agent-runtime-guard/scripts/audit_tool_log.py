#!/usr/bin/env python3
# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only
"""Review the tool-call log written by guard_pretooluse.py (or any JSONL of tool events).

Usage:
    audit_tool_log.py <events.jsonl> [--json] [--top 10] [--loop-threshold 3]

Each line is one tool call: {"ts", "session_id", "tool_name", "tool_input",
"action" (allow|warn|deny), "rule", "reason"}. Lines from other sources are
accepted when they carry at least tool_name; missing fields count as allow.

The report gives: calls per tool and per decision, denied calls with their
rules, MCP servers used, repeated identical calls (loops), bursts (many calls
in one minute), hosts contacted and paths written. Exit 1 when a deny, a loop
or a burst is found so a CI step or a Stop hook can fail on it; exit 2 on an
unreadable file.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

URL_RE = re.compile(r"https?://([^/\s'\"<>:]+)")
WRITE_TOOLS = {"write_file", "edit_file", "apply_patch", "manage_files"}


def read_events(path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for number, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{number}: invalid JSON: {exc.msg}") from exc
            if isinstance(record, dict) and record.get("tool_name"):
                events.append(record)
    return events


def minute_of(ts: str) -> str:
    return ts[:16] if len(ts) >= 16 else ts


def signature(event: dict[str, Any]) -> str:
    return json.dumps({"t": event.get("tool_name"), "a": event.get("tool_input", {})}, sort_keys=True, ensure_ascii=False)


def analyse(events: list[dict[str, Any]], top: int, loop_threshold: int, burst_threshold: int) -> dict[str, Any]:
    per_tool: Counter[str] = Counter()
    per_action: Counter[str] = Counter()
    denied: list[dict[str, Any]] = []
    warned: list[dict[str, Any]] = []
    mcp_servers: Counter[str] = Counter()
    signatures: Counter[str] = Counter()
    per_minute: Counter[str] = Counter()
    hosts: Counter[str] = Counter()
    written: Counter[str] = Counter()
    sessions: set[str] = set()

    for event in events:
        tool = str(event["tool_name"])
        action = str(event.get("action") or "allow")
        per_tool[tool] += 1
        per_action[action] += 1
        if event.get("session_id"):
            sessions.add(str(event["session_id"]))
        if action == "deny":
            denied.append(event)
        elif action == "warn":
            warned.append(event)
        if tool.startswith("mcp_"):
            mcp_servers[tool[4:].split("_", 1)[0]] += 1
        signatures[signature(event)] += 1
        if event.get("ts"):
            per_minute[minute_of(str(event["ts"]))] += 1
        text = json.dumps(event.get("tool_input", {}), ensure_ascii=False)
        for host in URL_RE.findall(text):
            hosts[host.lower()] += 1
        if tool in WRITE_TOOLS:
            args = event.get("tool_input") if isinstance(event.get("tool_input"), dict) else {}
            for key in ("path", "destination"):
                if isinstance(args.get(key), str):
                    written[args[key]] += 1
            for edit in args.get("edits", []) if isinstance(args.get("edits"), list) else []:
                if isinstance(edit, dict) and isinstance(edit.get("path"), str):
                    written[edit["path"]] += 1
            for item in args.get("paths", []) if isinstance(args.get("paths"), list) else []:
                if isinstance(item, str):
                    written[item] += 1

    loops = [
        {"count": count, "call": json.loads(sig)}
        for sig, count in signatures.most_common()
        if count >= loop_threshold
    ]
    bursts = [{"minute": minute, "calls": count} for minute, count in sorted(per_minute.items()) if count >= burst_threshold]
    return {
        "calls": len(events),
        "sessions": len(sessions),
        "per_action": dict(per_action),
        "per_tool": per_tool.most_common(top),
        "denied": [{"ts": e.get("ts"), "tool": e["tool_name"], "rule": e.get("rule"), "reason": e.get("reason")} for e in denied],
        "warned": [{"ts": e.get("ts"), "tool": e["tool_name"], "rule": e.get("rule"), "reason": e.get("reason")} for e in warned],
        "mcp_servers": mcp_servers.most_common(),
        "loops": loops,
        "bursts": bursts,
        "hosts": hosts.most_common(top),
        "paths_written": written.most_common(top),
        "findings": len(denied) + len(loops) + len(bursts),
    }


def markdown(report: dict[str, Any]) -> str:
    lines = [
        "## Agent runtime audit",
        "",
        f"- calls: {report['calls']} in {report['sessions']} session(s)",
        "- decisions: " + ", ".join(f"{k} {v}" for k, v in sorted(report["per_action"].items())),
        f"- findings: {report['findings']} (denied {len(report['denied'])}, loops {len(report['loops'])}, bursts {len(report['bursts'])})",
        "",
        "| Tool | Calls |",
        "|---|---|",
    ]
    lines += [f"| {tool} | {count} |" for tool, count in report["per_tool"]]
    if report["denied"]:
        lines += ["", "| When | Tool | Rule | Reason |", "|---|---|---|---|"]
        lines += [f"| {d['ts'] or '-'} | {d['tool']} | {d['rule'] or '-'} | {d['reason'] or '-'} |" for d in report["denied"]]
    if report["warned"]:
        lines += ["", "Warnings:", ""]
        lines += [f"- {w['ts'] or '-'} {w['tool']}: {w['reason']} ({w['rule']})" for w in report["warned"]]
    if report["mcp_servers"]:
        lines += ["", "| MCP server | Calls |", "|---|---|"]
        lines += [f"| {server} | {count} |" for server, count in report["mcp_servers"]]
    if report["loops"]:
        lines += ["", "Repeated identical calls (possible loops):", ""]
        for loop in report["loops"]:
            call = loop["call"]
            lines.append(f"- {loop['count']}x {call['t']} {json.dumps(call['a'], ensure_ascii=False)[:160]}")
    if report["bursts"]:
        lines += ["", "Bursts:", ""]
        lines += [f"- {b['minute']}: {b['calls']} calls" for b in report["bursts"]]
    if report["hosts"]:
        lines += ["", "| Host | Mentions |", "|---|---|"]
        lines += [f"| {host} | {count} |" for host, count in report["hosts"]]
    if report["paths_written"]:
        lines += ["", "| Path written | Times |", "|---|---|"]
        lines += [f"| {path} | {count} |" for path, count in report["paths_written"]]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit a JSONL log of agent tool calls.")
    parser.add_argument("log", type=Path, help="JSONL written by guard_pretooluse.py --log")
    parser.add_argument("--json", action="store_true", help="JSON report instead of Markdown")
    parser.add_argument("--top", type=int, default=10, help="rows per table (default 10)")
    parser.add_argument("--loop-threshold", type=int, default=3, help="identical calls that count as a loop (default 3)")
    parser.add_argument("--burst-threshold", type=int, default=40, help="calls in one minute that count as a burst (default 40)")
    args = parser.parse_args(argv)
    try:
        events = read_events(args.log)
    except (OSError, ValueError) as exc:
        print(f"audit_tool_log: {exc}", file=sys.stderr)
        return 2
    report = analyse(events, args.top, args.loop_threshold, args.burst_threshold)
    print(json.dumps(report, ensure_ascii=False, indent=2) if args.json else markdown(report))
    return 1 if report["findings"] else 0


if __name__ == "__main__":
    sys.exit(main())
