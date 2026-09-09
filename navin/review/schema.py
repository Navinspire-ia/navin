# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Structured code-review findings (portable patterns from OCR + pr-agent)."""

from __future__ import annotations

from typing import Any

_SEVERITY_ORDER = {
    "critical": 0,
    "high": 1,
    "medium": 2,
    "low": 3,
    "info": 4,
    "nit": 5,
}

_CATEGORIES = frozenset(
    {
        "bug",
        "security",
        "performance",
        "maintainability",
        "test",
        "style",
        "documentation",
        "api",
        "data",
        "other",
    }
)


def normalize_review_finding(raw: dict[str, Any]) -> dict[str, Any]:
    severity = str(raw.get("severity") or "medium").strip().lower()
    if severity not in _SEVERITY_ORDER:
        severity = "medium"
    category = str(raw.get("category") or "other").strip().lower()
    if category not in _CATEGORIES:
        category = "other"
    try:
        confidence = float(raw.get("confidence") if raw.get("confidence") is not None else 0.7)
    except (TypeError, ValueError):
        confidence = 0.7
    confidence = max(0.0, min(1.0, confidence))
    line = raw.get("start_line", raw.get("line"))
    end_line = raw.get("end_line", line)
    try:
        start_n = int(line) if line is not None else None
    except (TypeError, ValueError):
        start_n = None
    try:
        end_n = int(end_line) if end_line is not None else start_n
    except (TypeError, ValueError):
        end_n = start_n
    return {
        "id": str(raw.get("id") or raw.get("issue_header") or "finding")[:80],
        "severity": severity,
        "category": category,
        "confidence": round(confidence, 2),
        "summary": str(raw.get("summary") or raw.get("issue_header") or "").strip()[:240],
        "explanation": str(
            raw.get("explanation") or raw.get("issue_content") or raw.get("summary") or ""
        ).strip()[:2000],
        "recommendation": str(raw.get("recommendation") or raw.get("suggestion_content") or "")[
            :1000
        ],
        "file_path": str(raw.get("file_path") or raw.get("relevant_file") or raw.get("path") or "")[
            :500
        ],
        "start_line": start_n,
        "end_line": end_n,
        "existing_code": str(raw.get("existing_code") or "")[:2000],
        "suggested_code": str(
            raw.get("suggested_code") or raw.get("improved_code") or ""
        )[:2000],
        "label": str(raw.get("label") or category)[:40],
    }


def dedupe_review_findings(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    best: dict[str, dict[str, Any]] = {}
    for raw in findings:
        item = normalize_review_finding(raw)
        key = "|".join(
            [
                item["file_path"],
                str((item["start_line"] or 0) // 5),
                item["summary"][:80],
            ]
        )
        prev = best.get(key)
        if prev is None or _SEVERITY_ORDER[item["severity"]] < _SEVERITY_ORDER[prev["severity"]]:
            best[key] = item
    return sorted(
        best.values(),
        key=lambda f: (
            _SEVERITY_ORDER.get(str(f.get("severity")), 9),
            -float(f.get("confidence") or 0),
            str(f.get("file_path") or ""),
        ),
    )


def filter_by_confidence(
    findings: list[dict[str, Any]],
    *,
    min_confidence: float = 0.75,
) -> list[dict[str, Any]]:
    return [
        f
        for f in findings
        if float(f.get("confidence") or 0) >= min_confidence
    ]


def severity_counts(findings: list[dict[str, Any]]) -> dict[str, int]:
    counts = {name: 0 for name in _SEVERITY_ORDER}
    for item in findings:
        sev = str(item.get("severity") or "medium").lower()
        if sev in counts:
            counts[sev] += 1
    return counts
