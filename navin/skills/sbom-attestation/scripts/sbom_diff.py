#!/usr/bin/env python3
# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only
"""Diff two SBOMs (CycloneDX JSON or SPDX JSON, formats may differ).

Components are matched on the purl without its version (type/namespace/name),
falling back to the lowercase component name. The report lists added, removed
and version-changed components, and license changes on components present in
both documents.

Usage:
    sbom_diff.py OLD.json NEW.json [--json] [--fail-on-removed] [--fail-on-change]

Exit codes:
    0  no failure condition met
    1  --fail-on-removed and at least one component was removed, or
       --fail-on-change and any added, removed, version or license change exists
    2  usage error, unreadable file or unknown SBOM format
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from sbom_quality import Component, load_document


def match_key(component: Component) -> str:
    purl = component.purl
    if purl:
        base = purl.split("?", 1)[0].split("#", 1)[0]
        if "@" in base:
            base = base.rsplit("@", 1)[0]
        return base.lower()
    return "name:" + component.name.lower()


def index(components: list[Component]) -> dict[str, Component]:
    out: dict[str, Component] = {}
    for component in components:
        out.setdefault(match_key(component), component)
    return out


def diff(old: list[Component], new: list[Component]) -> dict[str, Any]:
    old_idx = index(old)
    new_idx = index(new)
    added = [new_idx[k] for k in sorted(set(new_idx) - set(old_idx))]
    removed = [old_idx[k] for k in sorted(set(old_idx) - set(new_idx))]
    version_changed: list[dict[str, str]] = []
    license_changed: list[dict[str, Any]] = []
    for key in sorted(set(old_idx) & set(new_idx)):
        before = old_idx[key]
        after = new_idx[key]
        if before.version != after.version:
            version_changed.append({"name": after.name or before.name, "old": before.version, "new": after.version, "purl": after.purl})
        if sorted(before.licenses) != sorted(after.licenses):
            license_changed.append({"name": after.name or before.name, "old": sorted(before.licenses), "new": sorted(after.licenses)})
    return {
        "added": [{"name": c.name, "version": c.version, "purl": c.purl} for c in added],
        "removed": [{"name": c.name, "version": c.version, "purl": c.purl} for c in removed],
        "version_changed": version_changed,
        "license_changed": license_changed,
        "unchanged": len(set(old_idx) & set(new_idx)) - len(version_changed),
    }


def label(entry: dict[str, str]) -> str:
    return f"{entry['name']}@{entry['version']}" if entry.get("version") else entry["name"]


def render_markdown(old: Path, new: Path, result: dict[str, Any]) -> str:
    lines = [
        f"# SBOM diff: {old.name} -> {new.name}",
        "",
        "| Change | Count |",
        "|---|---|",
        f"| added | {len(result['added'])} |",
        f"| removed | {len(result['removed'])} |",
        f"| version changed | {len(result['version_changed'])} |",
        f"| license changed | {len(result['license_changed'])} |",
        f"| unchanged | {result['unchanged']} |",
        "",
        "| Type | Component | Old | New | purl |",
        "|---|---|---|---|---|",
    ]
    for entry in result["added"]:
        lines.append(f"| added | {entry['name']} | - | {entry['version'] or '-'} | {entry['purl'] or '-'} |")
    for entry in result["removed"]:
        lines.append(f"| removed | {entry['name']} | {entry['version'] or '-'} | - | {entry['purl'] or '-'} |")
    for entry in result["version_changed"]:
        lines.append(f"| version | {entry['name']} | {entry['old'] or '-'} | {entry['new'] or '-'} | {entry['purl'] or '-'} |")
    for entry in result["license_changed"]:
        lines.append(f"| license | {entry['name']} | {', '.join(entry['old']) or '-'} | {', '.join(entry['new']) or '-'} | - |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Diff two SBOMs (CycloneDX or SPDX JSON).")
    parser.add_argument("old", help="baseline SBOM")
    parser.add_argument("new", help="candidate SBOM")
    parser.add_argument("--json", action="store_true", help="print JSON instead of Markdown")
    parser.add_argument("--fail-on-removed", action="store_true", help="exit 1 when a component disappeared")
    parser.add_argument("--fail-on-change", action="store_true", help="exit 1 on any added, removed, version or license change")
    args = parser.parse_args(argv)
    old_path = Path(args.old)
    new_path = Path(args.new)
    try:
        old_doc = load_document(old_path)
        new_doc = load_document(new_path)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    result = diff(old_doc.components, new_doc.components)
    result["old"] = {"file": str(old_path), "format": old_doc.fmt, "components": len(old_doc.components)}
    result["new"] = {"file": str(new_path), "format": new_doc.fmt, "components": len(new_doc.components)}
    any_change = any(result[key] for key in ("added", "removed", "version_changed", "license_changed"))
    failed = (args.fail_on_removed and bool(result["removed"])) or (args.fail_on_change and any_change)
    result["failed"] = failed
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(render_markdown(old_path, new_path, result), end="")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
