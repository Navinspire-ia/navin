# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Deterministic universe screens. A rejected name never reaches debate."""

from __future__ import annotations

from typing import Any


def _num(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def screen_candidate(
    snapshot: dict[str, Any],
    strategy: dict[str, Any],
) -> dict[str, Any]:
    symbol = str(snapshot.get("symbol") or "").upper()
    fund = snapshot.get("fundamental") or {}
    tech = snapshot.get("technical") or {}
    sent = snapshot.get("sentiment") or {}
    reasons: list[str] = []
    rejected: list[str] = []

    growth = _num(fund.get("revenue_growth"))
    min_growth = _num(strategy.get("min_revenue_growth")) or 0.0
    if fund.get("kind") in {"crypto", "nft"}:
        reasons.append(f"{fund.get('kind')}: fundamental growth screen skipped")
    elif growth is None and min_growth > 0 and fund.get("available"):
        rejected.append("revenue growth missing")
    elif growth is not None and growth < min_growth:
        rejected.append(f"revenue growth {growth:.1f}% below {min_growth:.0f}%")
    elif growth is not None:
        reasons.append(f"revenue growth {growth:.1f}%")

    debt = _num(fund.get("debt_equity"))
    max_debt = _num(strategy.get("max_debt_equity"))
    if debt is not None and max_debt is not None:
        # Yahoo often reports D/E as a percent (80) or a ratio (0.8).
        ratio = debt / 100.0 if debt > 8 else debt
        if ratio > max_debt:
            rejected.append(f"debt/equity {ratio:.2f} above {max_debt:.2f}")
        else:
            reasons.append(f"debt/equity {ratio:.2f}")

    if strategy.get("require_uptrend"):
        history = _num(tech.get("bars"))
        if tech.get("uptrend"):
            reasons.append("price above SMA50 with constructive trend")
        elif fund.get("kind") == "nft" and (history is None or history < 30):
            # Floors are collected one point a day; a collection cannot be
            # rejected for a trend nobody can read yet.
            reasons.append("nft: floor history too short for a trend read (collecting)")
        else:
            rejected.append("no confirmed uptrend")

    if str(sent.get("bias") or "") == "negative" and (sent.get("score") or 50) < 35:
        rejected.append("news sentiment strongly negative")

    passed = not rejected
    return {
        "symbol": symbol,
        "passed": passed,
        "reasons": reasons,
        "rejected": rejected,
    }


def screen_many(
    snapshots: list[dict[str, Any]],
    strategy: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    kept: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    for snap in snapshots:
        row = screen_candidate(snap, strategy)
        (kept if row["passed"] else dropped).append({**row, "snapshot": snap})
    return kept, dropped
