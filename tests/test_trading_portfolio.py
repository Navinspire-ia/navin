# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Paper book fills, approvals, and stops stay deterministic."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from navin.trading.errors import TradingError
from navin.trading.portfolio import decide_order, monitor_stops, place_order
from navin.trading.store import TradingStore


class TradingPortfolioTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = TradingStore(Path(self.tmp.name))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_autonomous_buy_then_sell_records_fill_pnl(self) -> None:
        buy = place_order(
            self.store,
            side="buy",
            symbol="AAPL",
            qty=5,
            price=100,
            reason="test buy",
            confidence=90,
            execution_mode="autonomous",
            prices={"AAPL": 100},
        )
        self.assertEqual(buy["status"], "filled")
        book = self.store.load_portfolio()
        self.assertEqual(book["cash"], 19_500)
        self.assertEqual(len(book["positions"]), 1)
        self.assertEqual(book["positions"][0]["qty"], 5)

        sell = place_order(
            self.store,
            side="sell",
            symbol="AAPL",
            qty=5,
            price=110,
            reason="test sell",
            confidence=90,
            execution_mode="autonomous",
            prices={"AAPL": 110},
        )
        self.assertEqual(sell["status"], "filled")
        self.assertAlmostEqual(float(sell["pnl"]), 50.0)
        book = self.store.load_portfolio()
        self.assertEqual(book["cash"], 20_050)
        self.assertEqual(book["positions"], [])
        self.assertAlmostEqual(float(book["realized_pnl"]), 50.0)

    def test_approval_accept_and_reject(self) -> None:
        pending = place_order(
            self.store,
            side="buy",
            symbol="MSFT",
            qty=4,
            price=100,
            reason="need approval",
            confidence=88,
            execution_mode="approval",
            prices={"MSFT": 100},
        )
        self.assertEqual(pending["status"], "pending")
        filled = decide_order(self.store, pending["id"], True, {"MSFT": 100})
        self.assertEqual(filled["status"], "filled")
        self.assertEqual(len(self.store.load_portfolio()["positions"]), 1)

        other = place_order(
            self.store,
            side="buy",
            symbol="AAPL",
            qty=3,
            price=100,
            reason="reject me",
            confidence=88,
            execution_mode="approval",
            prices={"AAPL": 100, "MSFT": 100},
        )
        rejected = decide_order(self.store, other["id"], False, {"AAPL": 100, "MSFT": 100})
        self.assertEqual(rejected["status"], "rejected")
        kinds = [row.get("kind") for row in self.store.load_journal(20)]
        self.assertIn("reject", kinds)

    def test_missing_order_is_not_found(self) -> None:
        with self.assertRaises(TradingError) as ctx:
            decide_order(self.store, "ord-missing", True, {})
        self.assertEqual(ctx.exception.status, 404)

    def test_stop_hit_sells_autonomously(self) -> None:
        place_order(
            self.store,
            side="buy",
            symbol="NVDA",
            qty=4,
            price=100,
            reason="open",
            confidence=90,
            execution_mode="autonomous",
            prices={"NVDA": 100},
        )
        pos = self.store.load_portfolio()["positions"][0]
        self.assertIsNotNone(pos.get("stop"))
        fills = monitor_stops(self.store, {"NVDA": float(pos["stop"]) - 0.5})
        self.assertTrue(fills)
        self.assertEqual(fills[0]["side"], "sell")
        self.assertIn(fills[0]["status"], {"filled", "pending"})
        if fills[0]["status"] == "filled":
            self.assertEqual(self.store.load_portfolio()["positions"], [])


if __name__ == "__main__":
    unittest.main()
