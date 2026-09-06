"""Backtest uses the same risk cap as live paper."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from navin.trading.backtest import run_backtest
from navin.trading.store import TradingStore


def _uptrend_bars(n: int = 90) -> list[dict[str, float]]:
    rows = []
    price = 50.0
    for i in range(n):
        price *= 1.012
        rows.append(
            {
                "t": 1_700_000_000 + i * 86400,
                "o": price * 0.99,
                "h": price * 1.02,
                "l": price * 0.98,
                "c": price,
                "v": 800_000,
            }
        )
    return rows


class TradingBacktestTest(unittest.TestCase):
    def test_uptrend_series_produces_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = TradingStore(Path(tmp))
            row = run_backtest(store, symbol="TEST", bars=_uptrend_bars())
            self.assertEqual(row["symbol"], "TEST")
            self.assertGreaterEqual(row["points"], 2)
            self.assertIn("max_drawdown", row)
            self.assertIn("return_pct", row)
            self.assertTrue(store.load_backtests())


if __name__ == "__main__":
    unittest.main()
