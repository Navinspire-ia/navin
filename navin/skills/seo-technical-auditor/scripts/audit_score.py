#!/usr/bin/env python3
"""Score SEO audit findings by impact × effort for prioritization.

Input JSON: list of objects or {"findings": [...]} with keys:
  id/title, severity (critical|important|nice|critical|...),
  impact (1-5), effort (1-5), url (optional)

Prints a ranked markdown table and a 0-100 health score.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

SEVERITY_WEIGHT = {
    "critical": 5,
    "blocker": 5,
    "important": 3,
    "major": 3,
    "nice": 1,
    "nice_to_have": 1,
    "minor": 1,
}


def _load(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        data = data.get("findings") or data.get("issues") or []
    if not isinstance(data, list):
        raise SystemExit("JSON must be a list of findings or {findings: [...]}")
    return [row for row in data if isinstance(row, dict)]


def _score(row: dict) -> float:
    sev = str(row.get("severity", "important")).lower().replace(" ", "_")
    impact = float(row.get("impact") or SEVERITY_WEIGHT.get(sev, 3))
    effort = float(row.get("effort") or 3)
    effort = max(effort, 0.5)
    return (impact * SEVERITY_WEIGHT.get(sev, 3)) / effort


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: audit_score.py findings.json", file=sys.stderr)
        raise SystemExit(2)
    path = Path(sys.argv[1])
    findings = _load(path)
    if not findings:
        print("No findings.")
        return

    ranked = sorted(findings, key=_score, reverse=True)
    critical = sum(
        1
        for f in findings
        if str(f.get("severity", "")).lower() in {"critical", "blocker"}
    )
    # Health: start 100, subtract weighted open issues (cap floor 0).
    penalty = 0.0
    for f in findings:
        sev = str(f.get("severity", "important")).lower().replace(" ", "_")
        penalty += SEVERITY_WEIGHT.get(sev, 3) * 4
    health = max(0, min(100, int(100 - penalty)))

    print("# Audit priority score\n")
    print(f"- Findings: {len(findings)}")
    print(f"- Critical/blocker: {critical}")
    print(f"- Health score (heuristic): {health}/100\n")
    print("| Priority | Title | Severity | Impact | Effort | URL |")
    print("|----------|-------|----------|--------|--------|-----|")
    for i, row in enumerate(ranked, 1):
        title = str(row.get("title") or row.get("id") or row.get("issue") or "finding")
        sev = str(row.get("severity", ""))
        impact = row.get("impact", "")
        effort = row.get("effort", "")
        url = str(row.get("url") or "")
        print(f"| {i} | {title} | {sev} | {impact} | {effort} | {url} |")


if __name__ == "__main__":
    main()
