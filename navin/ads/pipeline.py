# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Ingested exports -> analysis, shared by the ``ads`` tool and the Ads desk."""

from __future__ import annotations

from typing import Any

from navin.ads.ingest import IngestResult
from navin.ads.models import AdRow, AdsAnalysis
from navin.ads.rules import AdsThresholds, analyze_rows

ENTITY_LIMIT = 30


def analyze_ingested(
    results: list[IngestResult],
    *,
    thresholds: AdsThresholds,
    monthly_budget: float | None,
    currency: str,
) -> AdsAnalysis:
    rows: list[AdRow] = [row for result in results for row in result.rows]
    gaps: list[dict[str, str]] = []
    for result in results:
        gaps.extend(result.data_gaps)
        gaps.extend({"service": f"export:{result.source}", "reason": warning} for warning in result.warnings)
    return analyze_rows(
        rows,
        thresholds=thresholds,
        monthly_budget=monthly_budget,
        currency=(currency or "").upper(),
        sources=[result.source for result in results],
        columns={result.source: result.columns for result in results},
        data_gaps=gaps,
    )


def compact_payload(payload: dict[str, Any], *, limit: int = ENTITY_LIMIT) -> dict[str, Any]:
    """Top entities per level instead of the raw row dump."""
    entities = payload.get("entities") or {}
    payload["entities"] = {
        level: {"count": len(items), "top_by_spend": items[:limit]}
        for level, items in entities.items()
    }
    return payload
