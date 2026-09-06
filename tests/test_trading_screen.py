"""Screen rejects names that miss the mandate before debate starts."""

from __future__ import annotations

import unittest

from navin.trading.screen import screen_candidate
from navin.trading.store import default_strategy


class TradingScreenTest(unittest.TestCase):
    def test_rejects_low_growth_and_no_trend(self) -> None:
        strat = default_strategy()
        row = screen_candidate(
            {
                "symbol": "SLOW",
                "fundamental": {
                    "available": True,
                    "kind": "equity",
                    "revenue_growth": 4.0,
                    "debt_equity": 0.3,
                },
                "technical": {"uptrend": False},
                "sentiment": {"bias": "neutral", "score": 50},
            },
            strat,
        )
        self.assertFalse(row["passed"])
        self.assertTrue(any("growth" in item for item in row["rejected"]))

    def test_keeps_growth_uptrend_reasonable_debt(self) -> None:
        strat = default_strategy()
        row = screen_candidate(
            {
                "symbol": "FAST",
                "fundamental": {
                    "available": True,
                    "kind": "equity",
                    "revenue_growth": 22.0,
                    "debt_equity": 0.4,
                },
                "technical": {"uptrend": True},
                "sentiment": {"bias": "neutral", "score": 55},
            },
            strat,
        )
        self.assertTrue(row["passed"])

    def test_crypto_skips_growth_filter(self) -> None:
        strat = default_strategy()
        row = screen_candidate(
            {
                "symbol": "BTC-USD",
                "fundamental": {"available": False, "kind": "crypto"},
                "technical": {"uptrend": True},
                "sentiment": {"bias": "neutral", "score": 50},
            },
            strat,
        )
        self.assertTrue(row["passed"])

    def test_strong_negative_news_blocks(self) -> None:
        strat = default_strategy()
        row = screen_candidate(
            {
                "symbol": "BAD",
                "fundamental": {
                    "available": True,
                    "kind": "equity",
                    "revenue_growth": 20.0,
                    "debt_equity": 0.2,
                },
                "technical": {"uptrend": True},
                "sentiment": {"bias": "negative", "score": 20},
            },
            strat,
        )
        self.assertFalse(row["passed"])


if __name__ == "__main__":
    unittest.main()
