#!/usr/bin/env python3
# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only
"""Normalize vendor SIEM/EDR events (JSON or JSONL) into ECS-like JSONL.

Usage:
    normalize_events.py <events.json|events.jsonl> [--vendor splunk|elastic|sentinel|wazuh|crowdstrike|sentinelone|defender|auto]
                        [--mapping mapping.json] [--output out.jsonl] [--json]

The input is either a JSON array, a JSON object with a "results", "hits.hits",
"resources" or "data" list, or one JSON object per line (JSONL). Each event is
mapped with a built-in vendor mapping (CrowdStrike Falcon, Microsoft Defender
for Endpoint, Wazuh, Splunk Windows TA, Elastic, Microsoft Sentinel,
SentinelOne) or with a user mapping file:

    {"vendor": "custom", "fields": {"host.name": "src.hostname", "user.name": "actor"},
     "timestamp": "ts", "severity": {"field": "sev", "scale": {"1": "low", "2": "medium"}}}

Source paths are dotted ("device.hostname"). Output is one ECS-like JSON object
per line with @timestamp, event.*, host.name, user.name, process.*, source.ip,
destination.ip, rule.name, threat.tactic.name and observer.vendor; the original
event is kept under "raw" unless --drop-raw is given.

Exit codes: 0 all events mapped, 1 some events could not be mapped (no
timestamp or no host), 2 usage or input error.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SEVERITY_WORDS = {
    "informational": "informational",
    "info": "informational",
    "low": "low",
    "medium": "medium",
    "moderate": "medium",
    "high": "high",
    "critical": "critical",
}

# Built-in mappings: ECS field -> dotted source path in the vendor event.
BUILTIN_MAPPINGS: dict[str, dict[str, Any]] = {
    "crowdstrike": {
        "detector": ["device.hostname", "behaviors", "max_severity_displayname"],
        "timestamp": "created_timestamp",
        "fields": {
            "event.id": "detection_id",
            "host.name": "device.hostname",
            "host.id": "device.device_id",
            "host.os.full": "device.os_version",
            "host.ip": "device.local_ip",
            "user.name": "behaviors.0.user_name",
            "process.name": "behaviors.0.filename",
            "process.command_line": "behaviors.0.cmdline",
            "process.hash.sha256": "behaviors.0.sha256",
            "process.parent.command_line": "behaviors.0.parent_details.parent_cmdline",
            "rule.name": "behaviors.0.display_name",
            "rule.description": "behaviors.0.description",
            "threat.tactic.name": "behaviors.0.tactic",
            "threat.technique.id": "behaviors.0.technique_id",
            "threat.technique.name": "behaviors.0.technique",
            "event.outcome": "status",
        },
        "severity": {"field": "max_severity_displayname"},
    },
    "defender": {
        "detector": ["DeviceName", "ActionType", "ReportId"],
        "timestamp": "Timestamp",
        "fields": {
            "event.id": "ReportId",
            "event.action": "ActionType",
            "host.name": "DeviceName",
            "host.id": "DeviceId",
            "user.name": "AccountName",
            "user.domain": "AccountDomain",
            "process.name": "FileName",
            "process.command_line": "ProcessCommandLine",
            "process.hash.sha256": "SHA256",
            "process.parent.name": "InitiatingProcessFileName",
            "process.parent.command_line": "InitiatingProcessCommandLine",
            "source.ip": "LocalIP",
            "destination.ip": "RemoteIP",
            "destination.port": "RemotePort",
            "rule.name": "Title",
            "threat.tactic.name": "Category",
            "threat.technique.id": "AttackTechniques",
        },
        "severity": {"field": "Severity"},
    },
    "wazuh": {
        "detector": ["agent", "rule", "decoder"],
        "timestamp": "timestamp",
        "fields": {
            "event.id": "id",
            "host.name": "agent.name",
            "host.id": "agent.id",
            "host.ip": "agent.ip",
            "user.name": "data.win.eventdata.targetUserName",
            "process.name": "data.win.eventdata.image",
            "process.command_line": "data.win.eventdata.commandLine",
            "process.parent.name": "data.win.eventdata.parentImage",
            "source.ip": "data.srcip",
            "destination.ip": "data.dstip",
            "rule.id": "rule.id",
            "rule.name": "rule.description",
            "threat.tactic.name": "rule.mitre.tactic.0",
            "threat.technique.id": "rule.mitre.id.0",
            "threat.technique.name": "rule.mitre.technique.0",
            "event.code": "data.win.system.eventID",
        },
        "severity": {
            "field": "rule.level",
            "scale": {
                "0": "informational", "1": "informational", "2": "informational", "3": "low",
                "4": "low", "5": "low", "6": "low", "7": "medium", "8": "medium", "9": "medium",
                "10": "high", "11": "high", "12": "high", "13": "critical", "14": "critical",
                "15": "critical",
            },
        },
    },
    "splunk": {
        "detector": ["_time", "host", "sourcetype"],
        "timestamp": "_time",
        "fields": {
            "event.id": "RecordNumber",
            "event.code": "EventCode",
            "event.dataset": "sourcetype",
            "host.name": "host",
            "user.name": "user",
            "process.name": "process_name",
            "process.command_line": "process",
            "process.parent.name": "parent_process_name",
            "source.ip": "src_ip",
            "destination.ip": "dest_ip",
            "destination.port": "dest_port",
            "rule.name": "signature",
            "event.action": "action",
        },
        "severity": {"field": "severity"},
    },
    "elastic": {
        "detector": ["@timestamp", "kibana.alert.rule.name"],
        "timestamp": "@timestamp",
        "fields": {
            "event.id": "kibana.alert.uuid",
            "host.name": "host.name",
            "host.id": "host.id",
            "user.name": "user.name",
            "process.name": "process.name",
            "process.command_line": "process.command_line",
            "process.hash.sha256": "process.hash.sha256",
            "process.parent.name": "process.parent.name",
            "source.ip": "source.ip",
            "destination.ip": "destination.ip",
            "destination.port": "destination.port",
            "rule.name": "kibana.alert.rule.name",
            "rule.id": "kibana.alert.rule.uuid",
            "event.action": "event.action",
            "threat.tactic.name": "kibana.alert.rule.threat.0.tactic.name",
            "threat.technique.id": "kibana.alert.rule.threat.0.technique.0.id",
        },
        "severity": {"field": "kibana.alert.severity"},
    },
    "sentinel": {
        "detector": ["TimeGenerated", "AlertName"],
        "timestamp": "TimeGenerated",
        "fields": {
            "event.id": "SystemAlertId",
            "host.name": "CompromisedEntity",
            "user.name": "AccountName",
            "rule.name": "AlertName",
            "rule.description": "Description",
            "observer.product": "ProductName",
            "threat.tactic.name": "Tactics",
            "threat.technique.id": "Techniques",
            "event.outcome": "Status",
        },
        "severity": {"field": "AlertSeverity"},
    },
    "sentinelone": {
        "detector": ["agentRealtimeInfo", "threatInfo"],
        "timestamp": "threatInfo.createdAt",
        "fields": {
            "event.id": "id",
            "host.name": "agentRealtimeInfo.agentComputerName",
            "host.id": "agentRealtimeInfo.agentUuid",
            "host.os.full": "agentRealtimeInfo.agentOsName",
            "user.name": "threatInfo.processUser",
            "process.name": "threatInfo.originatorProcess",
            "process.hash.sha256": "threatInfo.sha256",
            "process.hash.sha1": "threatInfo.sha1",
            "file.path": "threatInfo.filePath",
            "rule.name": "threatInfo.threatName",
            "threat.tactic.name": "indicators.0.tactics.0.name",
            "threat.technique.id": "indicators.0.tactics.0.techniques.0.name",
            "event.outcome": "threatInfo.mitigationStatus",
        },
        "severity": {
            "field": "threatInfo.confidenceLevel",
            "scale": {"malicious": "high", "suspicious": "medium", "n/a": "low"},
        },
    },
}


def get_path(obj: Any, dotted: str) -> Any:
    """Resolve a dotted path; numeric segments index lists. Returns None when absent."""
    if isinstance(obj, dict) and dotted in obj:
        return obj[dotted]
    current: Any = obj
    for part in dotted.split("."):
        if isinstance(current, dict):
            if part not in current:
                return None
            current = current[part]
        elif isinstance(current, list):
            if not part.isdigit() or int(part) >= len(current):
                return None
            current = current[int(part)]
        else:
            return None
    return current


def set_path(obj: dict[str, Any], dotted: str, value: Any) -> None:
    parts = dotted.split(".")
    cur = obj
    for part in parts[:-1]:
        nxt = cur.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[part] = nxt
        cur = nxt
    cur[parts[-1]] = value


def parse_timestamp(value: Any) -> str | None:
    """Return an ISO 8601 UTC string from epoch seconds/milliseconds or an ISO string."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        seconds = float(value)
        if seconds > 1e12:
            seconds /= 1000.0
        return datetime.fromtimestamp(seconds, tz=timezone.utc).isoformat().replace("+00:00", "Z")
    text = str(value).strip()
    if not text:
        return None
    if text.replace(".", "", 1).isdigit():
        return parse_timestamp(float(text))
    candidate = text.replace("Z", "+00:00")
    if "." in candidate:
        head, _, tail = candidate.partition(".")
        frac = ""
        tz = ""
        for i, ch in enumerate(tail):
            if ch.isdigit():
                frac += ch
            else:
                tz = tail[i:]
                break
        candidate = f"{head}.{frac[:6].ljust(6, '0')}{tz}"
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def normalize_severity(value: Any, scale: dict[str, str] | None) -> str:
    if value is None:
        return "unknown"
    key = str(value).strip().lower()
    if scale and key in scale:
        return scale[key]
    if key in SEVERITY_WORDS:
        return SEVERITY_WORDS[key]
    if key.isdigit():
        number = int(key)
        if number >= 90 or (number >= 4 and number <= 5):
            return "critical"
        if number >= 70 or number == 3:
            return "high"
        if number >= 40 or number == 2:
            return "medium"
        return "low"
    return "unknown"


def detect_vendor(event: dict[str, Any]) -> str | None:
    best: tuple[int, str] | None = None
    for vendor, mapping in BUILTIN_MAPPINGS.items():
        hits = sum(1 for key in mapping["detector"] if get_path(event, key) is not None)
        if hits and (best is None or hits > best[0]):
            best = (hits, vendor)
    return best[1] if best else None


def normalize(event: dict[str, Any], mapping: dict[str, Any], vendor: str, keep_raw: bool) -> tuple[dict[str, Any], list[str]]:
    out: dict[str, Any] = {"observer": {"vendor": vendor}, "event": {"kind": "alert"}}
    problems: list[str] = []
    for ecs_field, src in mapping.get("fields", {}).items():
        value = get_path(event, src)
        if value is None or value == "":
            continue
        if isinstance(value, list) and ecs_field.startswith("threat."):
            value = ", ".join(str(v) for v in value)
        set_path(out, ecs_field, value)
    stamp = parse_timestamp(get_path(event, mapping.get("timestamp", "@timestamp")))
    if stamp is None:
        problems.append("no timestamp")
    else:
        out["@timestamp"] = stamp
    sev = mapping.get("severity", {})
    out["event"]["severity_label"] = normalize_severity(get_path(event, sev.get("field", "")), sev.get("scale"))
    if get_path(out, "host.name") is None:
        problems.append("no host.name")
    if keep_raw:
        out["raw"] = event
    return out, problems


def load_events(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8")
    stripped = text.strip()
    if not stripped:
        raise ValueError("input file is empty")
    if stripped.startswith("[") or stripped.startswith("{") and stripped.count("\n") == 0:
        data = json.loads(stripped)
    else:
        try:
            data = json.loads(stripped)
        except json.JSONDecodeError:
            data = [json.loads(line) for line in stripped.splitlines() if line.strip()]
    if isinstance(data, dict):
        for key in ("results", "resources", "data", "value", "Results"):
            if isinstance(data.get(key), list):
                return [e for e in data[key] if isinstance(e, dict)]
        hits = get_path(data, "hits.hits")
        if isinstance(hits, list):
            return [h.get("_source", h) for h in hits if isinstance(h, dict)]
        return [data]
    if isinstance(data, list):
        return [e for e in data if isinstance(e, dict)]
    raise ValueError("input is neither a JSON object, array nor JSONL")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Normalize vendor SIEM/EDR events to ECS-like JSONL.")
    parser.add_argument("input", help="JSON or JSONL file of vendor events")
    parser.add_argument("--vendor", default="auto", help="built-in mapping to use, or auto (default)")
    parser.add_argument("--mapping", help="custom mapping JSON file (overrides --vendor)")
    parser.add_argument("--output", help="write JSONL here instead of stdout")
    parser.add_argument("--drop-raw", action="store_true", help="do not keep the original event under raw")
    parser.add_argument("--json", action="store_true", help="print a JSON summary instead of the JSONL stream")
    args = parser.parse_args(argv)

    path = Path(args.input)
    if not path.is_file():
        print(f"error: {path} is not a file", file=sys.stderr)
        return 2
    try:
        events = load_events(path)
    except (ValueError, json.JSONDecodeError) as exc:
        print(f"error: cannot parse {path}: {exc}", file=sys.stderr)
        return 2

    custom: dict[str, Any] | None = None
    if args.mapping:
        try:
            custom = json.loads(Path(args.mapping).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"error: cannot read mapping: {exc}", file=sys.stderr)
            return 2
        if not isinstance(custom, dict) or "fields" not in custom:
            print("error: mapping must be an object with a 'fields' map", file=sys.stderr)
            return 2
    elif args.vendor != "auto" and args.vendor not in BUILTIN_MAPPINGS:
        print(f"error: unknown vendor {args.vendor}; choose from {', '.join(BUILTIN_MAPPINGS)} or auto", file=sys.stderr)
        return 2

    normalized: list[dict[str, Any]] = []
    unmapped = 0
    per_vendor: dict[str, int] = {}
    for event in events:
        if custom is not None:
            vendor = str(custom.get("vendor", "custom"))
            mapping = custom
        else:
            vendor = args.vendor if args.vendor != "auto" else (detect_vendor(event) or "unknown")
            mapping = BUILTIN_MAPPINGS.get(vendor, {"fields": {}, "timestamp": "@timestamp"})
        record, problems = normalize(event, mapping, vendor, not args.drop_raw)
        if problems:
            unmapped += 1
            record["event"]["normalization_problems"] = problems
        per_vendor[vendor] = per_vendor.get(vendor, 0) + 1
        normalized.append(record)

    lines = [json.dumps(r, ensure_ascii=False, sort_keys=True) for r in normalized]
    if args.output:
        Path(args.output).write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    summary = {"input": str(path), "events": len(events), "unmapped": unmapped, "per_vendor": per_vendor, "output": args.output}
    if args.json:
        print(json.dumps(summary, indent=2))
    elif not args.output:
        print("\n".join(lines))
    else:
        print(f"{len(events)} events -> {args.output} ({unmapped} with problems), vendors: {per_vendor}")
    return 1 if unmapped else 0


if __name__ == "__main__":
    sys.exit(main())
