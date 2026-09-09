# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Deterministic risk engine: it may only block, never enlarge a proposal."""

from __future__ import annotations

import unittest

from navin.trading.risk import evaluate_order, limits_from, size_buy
from navin.trading.store import default_risk


def _book(cash: float = 20_000, positions=None):
    return {
        "cash": cash,
        "positions": positions or [],
        "high_water": cash,
        "day_start_equity": cash,
    }


class TradingRiskTest(unittest.TestCase):
    def test_blocks_oversized_buy(self) -> None:
        limits = default_risk()
        verdict = evaluate_order(
            side="buy",
            symbol="NVDA",
            qty=10,
            price=1000,
            portfolio=_book(),
            prices={"NVDA": 1000},
            limits=limits,
            orders_today=0,
            execution_mode="autonomous",
        )
        self.assertFalse(verdict["allow"])
        self.assertTrue(any("max_position" in item for item in verdict["blocks"]))

    def test_blocks_when_daily_loss_hit(self) -> None:
        book = _book(cash=9_000)
        book["day_start_equity"] = 10_000
        verdict = evaluate_order(
            side="buy",
            symbol="AAPL",
            qty=1,
            price=100,
            portfolio=book,
            prices={"AAPL": 100},
            limits=default_risk(),
            orders_today=0,
            execution_mode="autonomous",
        )
        self.assertFalse(verdict["allow"])
        self.assertTrue(any("daily loss" in item for item in verdict["blocks"]))

    def test_blocks_drawdown(self) -> None:
        book = _book(cash=8_500)
        book["high_water"] = 10_000
        verdict = evaluate_order(
            side="buy",
            symbol="MSFT",
            qty=1,
            price=100,
            portfolio=book,
            prices={"MSFT": 100},
            limits=default_risk(),
            orders_today=0,
            execution_mode="autonomous",
        )
        self.assertFalse(verdict["allow"])
        self.assertTrue(any("drawdown" in item for item in verdict["blocks"]))

    def test_blocks_disallowed_asset(self) -> None:
        limits = default_risk()
        limits["allowed_assets"] = ["AAPL"]
        verdict = evaluate_order(
            side="buy",
            symbol="NVDA",
            qty=1,
            price=100,
            portfolio=_book(),
            prices={"NVDA": 100},
            limits=limits,
            orders_today=0,
            execution_mode="autonomous",
        )
        self.assertFalse(verdict["allow"])

    def test_research_mode_never_places(self) -> None:
        verdict = evaluate_order(
            side="buy",
            symbol="AAPL",
            qty=1,
            price=100,
            portfolio=_book(),
            prices={"AAPL": 100},
            limits=default_risk(),
            orders_today=0,
            execution_mode="research",
        )
        self.assertFalse(verdict["allow"])
        self.assertTrue(any("research-only" in item for item in verdict["blocks"]))

    def test_approval_mode_allows_but_flags(self) -> None:
        verdict = evaluate_order(
            side="buy",
            symbol="AAPL",
            qty=1,
            price=100,
            portfolio=_book(),
            prices={"AAPL": 100},
            limits=default_risk(),
            orders_today=0,
            execution_mode="approval",
        )
        self.assertTrue(verdict["allow"])
        self.assertTrue(verdict["approval_needed"])

    def test_size_never_exceeds_max_position(self) -> None:
        qty = size_buy(equity=10_000, price=100, limits=default_risk(), confidence=95)
        self.assertLessEqual(qty * 100, 10_000 * 0.03 + 1e-6)

    def test_limits_from_merges(self) -> None:
        merged = limits_from({"risk": {"max_position_pct": 2.0}})
        self.assertEqual(merged["max_position_pct"], 2.0)
        self.assertEqual(merged["stop_loss_pct"], 5.0)

    def test_blocks_daily_order_cap(self) -> None:
        verdict = evaluate_order(
            side="buy",
            symbol="AAPL",
            qty=1,
            price=100,
            portfolio=_book(),
            prices={"AAPL": 100},
            limits=default_risk(),
            orders_today=8,
            execution_mode="autonomous",
        )
        self.assertFalse(verdict["allow"])
        self.assertTrue(any("daily order" in item for item in verdict["blocks"]))


if __name__ == "__main__":
    unittest.main()
