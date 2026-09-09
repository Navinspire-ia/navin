"""Deterministic paid-media health scoring."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from navin.ads.models import AdsAnalysis, Severity

_PENALTIES = {
    Severity.CRITICAL: 20,
    Severity.HIGH: 10,
    Severity.MEDIUM: 4,
    Severity.LOW: 1,
    Severity.INFO: 0,
}
_MAX_SEVERITY_PENALTY = 60
_MAX_WASTE_PENALTY = 40


def health_score(analysis: AdsAnalysis) -> dict[str, Any]:
    category_penalties: dict[str, int] = defaultdict(int)
    counts: dict[str, int] = defaultdict(int)
    for finding in analysis.findings:
        category_penalties[finding.category] += _PENALTIES[finding.severity]
        counts[finding.severity.value] += 1
    severity_penalty = min(_MAX_SEVERITY_PENALTY, sum(category_penalties.values()))
    wasted = float(analysis.metadata.get("wasted_cost") or 0.0)
    total = analysis.totals.cost or 0.0
    waste_share = round(wasted / total, 4) if total > 0 else 0.0
    waste_penalty = min(_MAX_WASTE_PENALTY, round(waste_share * 100))
    score = max(0, min(100, 100 - severity_penalty - waste_penalty))
    grade = "A" if score >= 90 else "B" if score >= 75 else "C" if score >= 60 else "D" if score >= 40 else "F"
    return {
        "score": score,
        "grade": grade,
        "finding_count": len(analysis.findings),
        "change_count": len(analysis.changes),
        "counts": dict(sorted(counts.items())),
        "penalties": dict(sorted(category_penalties.items())),
        "wasted_cost": round(wasted, 2),
        "waste_share": waste_share,
        "estimated_monthly_savings": round(
            sum(change.estimated_monthly_savings or 0.0 for change in analysis.changes), 2
        ),
        "method": (
            "100 minus severity penalties (capped at 60) minus one point per percent of "
            "spend without conversions (capped at 40)"
        ),
    }
