#!/usr/bin/env python3
# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only
"""Score an SBOM against the NTIA minimum elements.

Reads one CycloneDX (1.4 to 1.6) JSON or SPDX (2.2 / 2.3) JSON document and
reports: component count, coverage percentage of each NTIA minimum element
(supplier, component name, version, unique identifiers purl or cpe, dependency
relationships, author of the SBOM data, timestamp), license coverage, the
components missing version, purl, license or supplier, duplicate components
and whether a dependency graph is present.

Usage:
    sbom_quality.py SBOM.json [--min-score N] [--json] [--max-list N]

Exit codes:
    0  score at or above --min-score (default 0, so always 0 without threshold)
    1  score below --min-score
    2  usage error, unreadable file or unknown SBOM format
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

NTIA_ELEMENTS = (
    "supplier",
    "component_name",
    "version",
    "unique_identifiers",
    "dependency_relationship",
    "sbom_author",
    "timestamp",
)


@dataclass
class Component:
    name: str
    version: str = ""
    purl: str = ""
    cpe: str = ""
    supplier: str = ""
    licenses: list[str] = field(default_factory=list)
    ref: str = ""

    @property
    def label(self) -> str:
        return f"{self.name}@{self.version}" if self.version else self.name


@dataclass
class Document:
    fmt: str
    spec_version: str
    components: list[Component]
    has_dependency_graph: bool
    dependency_edges: int
    author: str
    timestamp: str


def _first_license_cdx(entry: dict[str, Any]) -> str:
    lic = entry.get("license")
    if isinstance(lic, dict):
        return str(lic.get("id") or lic.get("name") or "")
    return str(entry.get("expression") or "")


def parse_cyclonedx(doc: dict[str, Any]) -> Document:
    components: list[Component] = []
    for raw in doc.get("components") or []:
        if not isinstance(raw, dict):
            continue
        supplier = raw.get("supplier") or {}
        supplier_name = str(supplier.get("name") or "") if isinstance(supplier, dict) else ""
        if not supplier_name:
            supplier_name = str(raw.get("publisher") or "")
        licenses = sorted(
            {
                value
                for value in (_first_license_cdx(item) for item in raw.get("licenses") or [] if isinstance(item, dict))
                if value
            }
        )
        components.append(
            Component(
                name=str(raw.get("name") or ""),
                version=str(raw.get("version") or ""),
                purl=str(raw.get("purl") or ""),
                cpe=str(raw.get("cpe") or ""),
                supplier=supplier_name,
                licenses=licenses,
                ref=str(raw.get("bom-ref") or ""),
            )
        )
    deps = doc.get("dependencies") or []
    edges = sum(len(item.get("dependsOn") or []) for item in deps if isinstance(item, dict))
    meta = doc.get("metadata") or {}
    author = ""
    authors = meta.get("authors") or []
    if authors and isinstance(authors[0], dict):
        author = str(authors[0].get("name") or "")
    if not author:
        tools = meta.get("tools")
        if isinstance(tools, dict):
            comps = tools.get("components") or []
            if comps and isinstance(comps[0], dict):
                author = str(comps[0].get("name") or "")
        elif isinstance(tools, list) and tools and isinstance(tools[0], dict):
            author = str(tools[0].get("name") or "")
    return Document(
        fmt="CycloneDX",
        spec_version=str(doc.get("specVersion") or ""),
        components=components,
        has_dependency_graph=edges > 0,
        dependency_edges=edges,
        author=author,
        timestamp=str(meta.get("timestamp") or ""),
    )


def parse_spdx(doc: dict[str, Any]) -> Document:
    components: list[Component] = []
    for raw in doc.get("packages") or []:
        if not isinstance(raw, dict):
            continue
        purl = ""
        cpe = ""
        for ref in raw.get("externalRefs") or []:
            if not isinstance(ref, dict):
                continue
            rtype = str(ref.get("referenceType") or "")
            locator = str(ref.get("referenceLocator") or "")
            if rtype == "purl" and not purl:
                purl = locator
            elif rtype in ("cpe22Type", "cpe23Type") and not cpe:
                cpe = locator
        licenses: list[str] = []
        for key in ("licenseConcluded", "licenseDeclared"):
            value = str(raw.get(key) or "")
            if value and value not in ("NOASSERTION", "NONE") and value not in licenses:
                licenses.append(value)
        supplier = str(raw.get("supplier") or "")
        if supplier == "NOASSERTION":
            supplier = ""
        components.append(
            Component(
                name=str(raw.get("name") or ""),
                version=str(raw.get("versionInfo") or ""),
                purl=purl,
                cpe=cpe,
                supplier=supplier,
                licenses=licenses,
                ref=str(raw.get("SPDXID") or ""),
            )
        )
    rels = [
        item
        for item in doc.get("relationships") or []
        if isinstance(item, dict) and str(item.get("relationshipType") or "").upper() in ("DEPENDS_ON", "DEPENDENCY_OF", "CONTAINS")
    ]
    info = doc.get("creationInfo") or {}
    creators = [str(item) for item in info.get("creators") or []]
    author = ""
    for prefix in ("Organization:", "Person:", "Tool:"):
        for creator in creators:
            if creator.startswith(prefix):
                author = creator
                break
        if author:
            break
    return Document(
        fmt="SPDX",
        spec_version=str(doc.get("spdxVersion") or ""),
        components=components,
        has_dependency_graph=len(rels) > 0,
        dependency_edges=len(rels),
        author=author,
        timestamp=str(info.get("created") or ""),
    )


def load_document(path: Path) -> Document:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as exc:
        raise ValueError(f"cannot read {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(doc, dict):
        raise ValueError(f"{path}: top level must be a JSON object")
    if doc.get("bomFormat") == "CycloneDX":
        return parse_cyclonedx(doc)
    if str(doc.get("spdxVersion") or "").startswith("SPDX-"):
        return parse_spdx(doc)
    raise ValueError(f"{path}: neither CycloneDX JSON (bomFormat) nor SPDX JSON (spdxVersion)")


def percent(count: int, total: int) -> float:
    return round(100.0 * count / total, 1) if total else 0.0


def analyse(doc: Document) -> dict[str, Any]:
    comps = doc.components
    total = len(comps)
    with_name = sum(1 for c in comps if c.name)
    with_version = sum(1 for c in comps if c.version)
    with_supplier = sum(1 for c in comps if c.supplier)
    with_id = sum(1 for c in comps if c.purl or c.cpe)
    with_license = sum(1 for c in comps if c.licenses)
    coverage = {
        "supplier": percent(with_supplier, total),
        "component_name": percent(with_name, total),
        "version": percent(with_version, total),
        "unique_identifiers": percent(with_id, total),
        "dependency_relationship": 100.0 if doc.has_dependency_graph else 0.0,
        "sbom_author": 100.0 if doc.author else 0.0,
        "timestamp": 100.0 if doc.timestamp else 0.0,
    }
    score = round(sum(coverage[key] for key in NTIA_ELEMENTS) / len(NTIA_ELEMENTS), 1)
    seen: dict[tuple[str, str], int] = {}
    for c in comps:
        key = (c.name.lower(), c.version)
        seen[key] = seen.get(key, 0) + 1
    duplicates = sorted(f"{name}@{version}" if version else name for (name, version), n in seen.items() if n > 1)
    return {
        "format": doc.fmt,
        "spec_version": doc.spec_version,
        "component_count": total,
        "ntia_score": score,
        "coverage": coverage,
        "license_coverage": percent(with_license, total),
        "sbom_author": doc.author,
        "timestamp": doc.timestamp,
        "dependency_edges": doc.dependency_edges,
        "missing": {
            "version": sorted(c.label for c in comps if not c.version),
            "purl": sorted(c.label for c in comps if not c.purl),
            "license": sorted(c.label for c in comps if not c.licenses),
            "supplier": sorted(c.label for c in comps if not c.supplier),
        },
        "duplicates": duplicates,
    }


def render_markdown(path: Path, result: dict[str, Any], min_score: float, max_list: int) -> str:
    lines = [
        f"# SBOM quality: {path.name}",
        "",
        f"- Format: {result['format']} {result['spec_version']}",
        f"- Components: {result['component_count']}",
        f"- NTIA score: {result['ntia_score']} / 100 (threshold {min_score})",
        f"- SBOM author: {result['sbom_author'] or 'MISSING'}",
        f"- Timestamp: {result['timestamp'] or 'MISSING'}",
        f"- Dependency edges: {result['dependency_edges']}",
        f"- License coverage: {result['license_coverage']} %",
        "",
        "| NTIA element | Coverage % |",
        "|---|---|",
    ]
    for key in NTIA_ELEMENTS:
        lines.append(f"| {key} | {result['coverage'][key]} |")
    lines.extend(["", "| Gap | Count | Components |", "|---|---|---|"])
    for key, items in result["missing"].items():
        shown = ", ".join(items[:max_list]) + (" ..." if len(items) > max_list else "")
        lines.append(f"| missing {key} | {len(items)} | {shown or '-'} |")
    dups = result["duplicates"]
    shown = ", ".join(dups[:max_list]) + (" ..." if len(dups) > max_list else "")
    lines.append(f"| duplicates | {len(dups)} | {shown or '-'} |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Score an SBOM against the NTIA minimum elements.")
    parser.add_argument("sbom", help="CycloneDX JSON or SPDX JSON file")
    parser.add_argument("--min-score", type=float, default=0.0, help="exit 1 when the NTIA score is below this value (0 to 100)")
    parser.add_argument("--max-list", type=int, default=10, help="components listed per gap in Markdown output")
    parser.add_argument("--json", action="store_true", help="print JSON instead of Markdown")
    args = parser.parse_args(argv)
    path = Path(args.sbom)
    try:
        doc = load_document(path)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    result = analyse(doc)
    result["file"] = str(path)
    result["min_score"] = args.min_score
    result["passed"] = result["ntia_score"] >= args.min_score
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(render_markdown(path, result, args.min_score, args.max_list), end="")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
