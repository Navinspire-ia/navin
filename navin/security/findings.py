# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Structured AppSec findings: normalize, dedupe, rank.

Inspired by research-agent pipelines (phased scan → structured findings →
dedupe) but implemented independently under Navin's license - no AGPL code.
"""

from __future__ import annotations

from typing import Any

_SEVERITY_ORDER = {
    "critical": 0,
    "high": 1,
    "medium": 2,
    "low": 3,
    "info": 4,
}


def normalize_finding(raw: dict[str, Any]) -> dict[str, Any]:
    """Return a finding with a stable schema for agent + report use."""
    severity = str(raw.get("severity") or "medium").strip().lower()
    if severity not in _SEVERITY_ORDER:
        severity = "medium"
    file_path = str(raw.get("file_path") or raw.get("file") or "").strip()
    line = raw.get("line")
    try:
        line_n = int(line) if line is not None else None
    except (TypeError, ValueError):
        line_n = None
    return {
        "id": str(raw.get("id") or raw.get("rule_id") or "finding").strip()[:80],
        "severity": severity,
        "category": str(raw.get("category") or raw.get("vulnerability_type") or "Security")[
            :80
        ],
        "summary": str(raw.get("summary") or raw.get("message") or "").strip()[:240],
        "explanation": str(
            raw.get("explanation") or raw.get("message") or raw.get("summary") or ""
        ).strip()[:2000],
        "recommendation": str(raw.get("recommendation") or "").strip()[:1000],
        "file_path": file_path[:500],
        "line": line_n,
        "source": str(raw.get("source") or "heuristic").strip()[:40],
        "malicious_input_example": str(raw.get("malicious_input_example") or "")[
            :500
        ],
        "poc_sketch": str(raw.get("poc_sketch") or "")[:1200],
        "confidence": raw.get("confidence"),
        "exploit_scenario": str(raw.get("exploit_scenario") or "")[:1000],
    }


def finding_key(finding: dict[str, Any]) -> str:
    """Dedupe key: rule + path + nearby line bucket."""
    line = finding.get("line")
    bucket = (int(line) // 5) if isinstance(line, int) else -1
    return "|".join(
        [
            str(finding.get("id") or ""),
            str(finding.get("file_path") or ""),
            str(bucket),
            str(finding.get("summary") or "")[:80],
        ]
    )


def dedupe_findings(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep the highest-severity finding per key."""
    best: dict[str, dict[str, Any]] = {}
    for raw in findings:
        item = normalize_finding(raw)
        key = finding_key(item)
        prev = best.get(key)
        if prev is None:
            best[key] = item
            continue
        if _SEVERITY_ORDER[item["severity"]] < _SEVERITY_ORDER[prev["severity"]]:
            best[key] = item
    return sorted(
        best.values(),
        key=lambda f: (
            _SEVERITY_ORDER.get(str(f.get("severity")), 9),
            str(f.get("file_path") or ""),
            f.get("line") or 0,
        ),
    )


def severity_counts(findings: list[dict[str, Any]]) -> dict[str, int]:
    counts = {name: 0 for name in _SEVERITY_ORDER}
    for item in findings:
        sev = str(item.get("severity") or "medium").lower()
        if sev in counts:
            counts[sev] += 1
    return counts
