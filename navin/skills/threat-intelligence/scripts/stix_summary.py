#!/usr/bin/env python3
# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only
"""Summarize a STIX 2.1 bundle: object counts, indicators, ATT&CK techniques, relationships, TLP.

Usage:
    stix_summary.py <bundle.json> [--json] [--as-of 2026-03-12T00:00:00Z] [--output summary.md]

Reads a STIX 2.1 bundle (type "bundle", "objects" list) such as a MISP
/events/{id}.json?returnFormat=stix2 export, an OpenCTI bundle or a TAXII
collection page. Produces per-type counts, the indicators with pattern type,
pattern, validity window and status (valid, expired, future, revoked),
attack-patterns resolved to ATT&CK ids through external_references
(source_name mitre-attack), the relationship graph as readable triples, the
TLP marking per object (marking-definition ids and extension TLP 2.0 names),
and consistency problems (dangling references, indicators without pattern,
missing valid_from).

Exit codes: 0 bundle consistent, 1 expired or revoked indicators, dangling
references or objects without marking were found, 2 usage or input error.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# STIX 2.1 standard TLP 1.0 marking-definition ids (section 7.2.1.4) and TLP 2.0 extension names.
TLP_IDS = {
    "marking-definition--613f2e26-407d-48c7-9eca-b8e91df99dc9": "TLP:WHITE",
    "marking-definition--34098fce-860f-48ae-8e50-ebd3cc5e41da": "TLP:GREEN",
    "marking-definition--f88d31f6-486f-44da-b317-01333bde0b82": "TLP:AMBER",
    "marking-definition--5e57c739-391a-4eb3-b6be-7d15ca92d5ed": "TLP:RED",
    "marking-definition--94868c89-83c2-464b-929b-a1a8aa3c8487": "TLP:CLEAR",
    "marking-definition--bab4a63c-aed9-4cf5-a766-dfca5abac2bb": "TLP:GREEN",
    "marking-definition--55d920b0-5e8b-4f79-9ee9-91f868d9b421": "TLP:AMBER",
    "marking-definition--939a9414-2ddd-4d32-a0cd-375ea402b003": "TLP:AMBER+STRICT",
    "marking-definition--e828b379-4e03-4974-9ac4-e53a884c97c1": "TLP:RED",
}


def parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def marking_name(obj: dict[str, Any]) -> str | None:
    if obj.get("type") != "marking-definition":
        return None
    if obj.get("definition_type") == "tlp":
        return "TLP:" + str(obj.get("definition", {}).get("tlp", "?")).upper()
    ext = obj.get("extensions", {})
    for ext_id, body in ext.items():
        if "marking-definition" in ext_id and isinstance(body, dict) and body.get("tlp_2_0"):
            return "TLP:" + str(body["tlp_2_0"]).upper().replace("_", "+")
    if obj.get("name", "").upper().startswith("TLP:"):
        return str(obj["name"]).upper()
    if obj.get("definition_type") == "statement":
        return "statement: " + str(obj.get("definition", {}).get("statement", ""))[:60]
    return TLP_IDS.get(str(obj.get("id")))


def attack_ids(obj: dict[str, Any]) -> list[str]:
    ids: list[str] = []
    for ref in obj.get("external_references", []) or []:
        if ref.get("source_name") in ("mitre-attack", "mitre-mobile-attack", "mitre-ics-attack") and ref.get("external_id"):
            ids.append(str(ref["external_id"]))
    return ids


def label(obj: dict[str, Any]) -> str:
    for key in ("name", "value", "pattern"):
        if obj.get(key):
            text = str(obj[key])
            return text if len(text) <= 60 else text[:57] + "..."
    return str(obj.get("id", "?"))


def summarize(bundle: dict[str, Any], as_of: datetime) -> dict[str, Any]:
    objects: list[dict[str, Any]] = [o for o in bundle.get("objects", []) if isinstance(o, dict) and o.get("id")]
    by_id = {o["id"]: o for o in objects}
    counts: dict[str, int] = {}
    for o in objects:
        counts[o.get("type", "?")] = counts.get(o.get("type", "?"), 0) + 1

    markings = {o["id"]: (marking_name(o) or o["id"]) for o in objects if o.get("type") == "marking-definition"}
    markings.update({k: v for k, v in TLP_IDS.items() if k not in markings})

    def tlp_of(o: dict[str, Any]) -> str:
        names = [markings.get(m, m) for m in o.get("object_marking_refs", []) or []]
        return ", ".join(names) if names else "none"

    problems: list[str] = []
    indicators: list[dict[str, Any]] = []
    for o in objects:
        if o.get("type") != "indicator":
            continue
        valid_from = parse_time(o.get("valid_from"))
        valid_until = parse_time(o.get("valid_until"))
        if o.get("revoked"):
            status = "revoked"
        elif valid_from is None:
            status = "invalid: missing valid_from"
            problems.append(f"{o['id']} has no valid_from")
        elif valid_until is not None and valid_until <= as_of:
            status = "expired"
        elif valid_from > as_of:
            status = "future"
        else:
            status = "valid"
        if not o.get("pattern"):
            problems.append(f"{o['id']} has no pattern")
        indicators.append({
            "id": o["id"],
            "name": o.get("name", ""),
            "pattern_type": o.get("pattern_type", "stix"),
            "pattern": o.get("pattern", ""),
            "valid_from": o.get("valid_from"),
            "valid_until": o.get("valid_until"),
            "status": status,
            "confidence": o.get("confidence"),
            "labels": o.get("labels", []),
            "indicator_types": o.get("indicator_types", []),
            "kill_chain_phases": [f"{p.get('kill_chain_name')}:{p.get('phase_name')}" for p in o.get("kill_chain_phases", []) or []],
            "tlp": tlp_of(o),
        })

    attack_patterns = [
        {"id": o["id"], "name": o.get("name", ""), "attack_ids": attack_ids(o), "tactics": [p.get("phase_name") for p in o.get("kill_chain_phases", []) or []], "tlp": tlp_of(o)}
        for o in objects if o.get("type") == "attack-pattern"
    ]
    for ap in attack_patterns:
        if not ap["attack_ids"]:
            problems.append(f"{ap['id']} ({ap['name']}) has no mitre-attack external reference")

    relationships: list[dict[str, Any]] = []
    for o in objects:
        if o.get("type") == "relationship":
            src, tgt = o.get("source_ref", ""), o.get("target_ref", "")
            for ref in (src, tgt):
                if ref not in by_id:
                    problems.append(f"{o['id']} references {ref} which is not in the bundle")
            relationships.append({
                "id": o["id"],
                "source": label(by_id.get(src, {"id": src})),
                "source_type": src.split("--")[0],
                "relationship_type": o.get("relationship_type", ""),
                "target": label(by_id.get(tgt, {"id": tgt})),
                "target_type": tgt.split("--")[0],
                "tlp": tlp_of(o),
            })
        elif o.get("type") == "sighting":
            ref = o.get("sighting_of_ref", "")
            if ref not in by_id:
                problems.append(f"{o['id']} sights {ref} which is not in the bundle")
            relationships.append({
                "id": o["id"],
                "source": ", ".join(label(by_id.get(w, {"id": w})) for w in o.get("where_sighted_refs", []) or []) or "(unspecified)",
                "source_type": "identity",
                "relationship_type": f"sighted x{o.get('count', 1)}",
                "target": label(by_id.get(ref, {"id": ref})),
                "target_type": ref.split("--")[0],
                "tlp": tlp_of(o),
            })

    tlp_counts: dict[str, int] = {}
    unmarked = 0
    for o in objects:
        if o.get("type") in ("marking-definition",):
            continue
        name = tlp_of(o)
        if name == "none":
            unmarked += 1
        tlp_counts[name] = tlp_counts.get(name, 0) + 1
    if unmarked:
        problems.append(f"{unmarked} objects carry no object_marking_refs")

    actors = [{"id": o["id"], "name": o.get("name"), "aliases": o.get("aliases", []), "type": o["type"]} for o in objects if o.get("type") in ("threat-actor", "intrusion-set", "campaign", "malware", "tool")]

    return {
        "bundle_id": bundle.get("id"),
        "as_of": as_of.isoformat().replace("+00:00", "Z"),
        "objects": len(objects),
        "counts": dict(sorted(counts.items())),
        "tlp": tlp_counts,
        "indicators": indicators,
        "attack_patterns": attack_patterns,
        "actors_and_malware": actors,
        "relationships": relationships,
        "problems": problems,
    }


def to_markdown(s: dict[str, Any]) -> str:
    lines = [f"# STIX 2.1 bundle summary: {s['bundle_id'] or 'no id'}", "", f"{s['objects']} objects, evaluated as of {s['as_of']}.", "", "## Object counts", "", "| Type | Count |", "|---|---|"]
    lines += [f"| {t} | {c} |" for t, c in s["counts"].items()]
    lines += ["", "## TLP markings", "", "| Marking | Objects |", "|---|---|"]
    lines += [f"| {m} | {c} |" for m, c in sorted(s["tlp"].items())]
    lines += ["", "## Indicators", "", "| Name | Pattern type | Pattern | Valid from | Valid until | Status | Confidence | TLP |", "|---|---|---|---|---|---|---|---|"]
    for i in s["indicators"]:
        pattern = " ".join(i["pattern"].split()).replace("|", "\\|")
        if len(pattern) > 120:
            pattern = pattern[:117] + "..."
        lines.append(f"| {i['name'] or '-'} | {i['pattern_type']} | `{pattern}` | {i['valid_from'] or '-'} | {i['valid_until'] or '-'} | {i['status']} | {i['confidence'] if i['confidence'] is not None else '-'} | {i['tlp']} |")
    lines += ["", "## Attack patterns (MITRE ATT&CK)", "", "| Name | ATT&CK id | Tactics | TLP |", "|---|---|---|---|"]
    for a in s["attack_patterns"]:
        lines.append(f"| {a['name']} | {', '.join(a['attack_ids']) or 'missing'} | {', '.join(t for t in a['tactics'] if t) or '-'} | {a['tlp']} |")
    lines += ["", "## Actors, malware, tools", "", "| Type | Name | Aliases |", "|---|---|---|"]
    for a in s["actors_and_malware"]:
        lines.append(f"| {a['type']} | {a['name']} | {', '.join(a['aliases']) or '-'} |")
    lines += ["", "## Relationships and sightings", "", "| Source | Relationship | Target | TLP |", "|---|---|---|---|"]
    for r in s["relationships"]:
        lines.append(f"| {r['source']} ({r['source_type']}) | {r['relationship_type']} | {r['target']} ({r['target_type']}) | {r['tlp']} |")
    lines += ["", "## Problems", ""]
    lines += [f"- {p}" for p in s["problems"]] or ["- none"]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Summarize a STIX 2.1 bundle.")
    parser.add_argument("bundle", help="STIX 2.1 bundle JSON file")
    parser.add_argument("--json", action="store_true", help="JSON output instead of Markdown")
    parser.add_argument("--as-of", help="evaluate indicator validity at this ISO time (default now)")
    parser.add_argument("--output", help="write the summary to this file")
    args = parser.parse_args(argv)

    path = Path(args.bundle)
    if not path.is_file():
        print(f"error: {path} is not a file", file=sys.stderr)
        return 2
    try:
        bundle = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"error: invalid JSON: {exc}", file=sys.stderr)
        return 2
    if not isinstance(bundle, dict) or bundle.get("type") != "bundle" or not isinstance(bundle.get("objects"), list):
        print("error: not a STIX bundle (expected type 'bundle' with an 'objects' list)", file=sys.stderr)
        return 2
    as_of = parse_time(args.as_of) if args.as_of else datetime.now(timezone.utc)
    if as_of is None:
        print("error: --as-of must be an ISO 8601 timestamp", file=sys.stderr)
        return 2

    summary = summarize(bundle, as_of)
    text = json.dumps(summary, indent=2) if args.json else to_markdown(summary)
    if args.output:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
        print(f"summary -> {args.output}")
    else:
        print(text)
    bad_indicators = any(i["status"] in ("expired", "revoked") or i["status"].startswith("invalid") for i in summary["indicators"])
    return 1 if (summary["problems"] or bad_indicators) else 0


if __name__ == "__main__":
    sys.exit(main())
