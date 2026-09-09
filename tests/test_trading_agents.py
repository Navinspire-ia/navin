# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Debate judge must refuse a thin bull case."""

from __future__ import annotations

import unittest

from navin.trading.agents import debate, run_specialists, strategy_trader


class TradingAgentsTest(unittest.TestCase):
    def test_judge_waits_when_specialists_disagree(self) -> None:
        reports = [
            {"name": "fundamental", "score": 88.0, "reasons": ["growth"]},
            {"name": "technical", "score": 40.0, "reasons": ["no trend"]},
            {"name": "sentiment", "score": 42.0, "reasons": ["mixed"]},
            {"name": "macro", "score": 41.0, "reasons": ["risk off"]},
            {"name": "risk", "score": 40.0, "reasons": ["vol"]},
        ]
        fused = strategy_trader(reports)
        fused["action"] = "BUY"
        argued = debate(reports, fused)
        self.assertEqual(argued["judge"]["action"], "WAIT")
        self.assertIn("rejects", argued["judge"]["why"].lower())

    def test_specialists_return_full_card(self) -> None:
        result = run_specialists(
            {
                "symbol": "NVDA",
                "quote": {"name": "NVIDIA", "price": 100},
                "technical": {
                    "last": 100,
                    "uptrend": True,
                    "rsi": 58,
                    "macd_hist": 0.4,
                    "sma50": 90,
                    "atr": 2,
                    "support": 92,
                },
                "fundamental": {
                    "available": True,
                    "kind": "equity",
                    "revenue_growth": 30,
                    "debt_equity": 0.3,
                    "profit_margin": 0.3,
                    "recommendation": "buy",
                },
                "sentiment": {"score": 70, "bias": "positive", "reasons": ["beat"]},
                "news": [{"title": "beat"}],
            },
            {"positions": [], "cash": 20000},
            bench_change_pct=0.5,
        )
        self.assertEqual(result["symbol"], "NVDA")
        self.assertEqual(result["action"], "BUY")
        self.assertIn("fundamental", result["scores"])
        self.assertTrue(result["thesis"])


if __name__ == "__main__":
    unittest.main()
