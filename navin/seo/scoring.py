"""Deterministic SEO health scoring."""

from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from navin.seo.models import Finding, Severity

_PENALTIES = {
    Severity.CRITICAL: 20,
    Severity.HIGH: 10,
    Severity.MEDIUM: 4,
    Severity.LOW: 1,
    Severity.INFO: 0,
}


def health_score(findings: Iterable[Finding], *, page_count: int = 1) -> dict[str, object]:
    items = list(findings)
    divisor = max(1, page_count)
    category_penalties: dict[str, int] = defaultdict(int)
    counts: dict[str, int] = defaultdict(int)
    for finding in items:
        penalty = _PENALTIES[finding.severity]
        category_penalties[finding.category] += penalty
        counts[finding.severity.value] += 1
    raw_penalty = sum(category_penalties.values())
    normalized_penalty = round(raw_penalty / divisor)
    score = max(0, min(100, 100 - normalized_penalty))
    grade = "A" if score >= 90 else "B" if score >= 75 else "C" if score >= 60 else "D" if score >= 40 else "F"
    return {
        "score": score,
        "grade": grade,
        "page_count": page_count,
        "finding_count": len(items),
        "counts": dict(sorted(counts.items())),
        "penalties": dict(sorted(category_penalties.items())),
        "method": "100 minus severity penalties normalized by crawled page count",
    }
