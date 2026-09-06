"""Technicals stay numeric and refuse to invent a series."""

from __future__ import annotations

import unittest

from navin.trading.indicators import rsi, sma, support_resistance, summarize_technicals


class TradingIndicatorsTest(unittest.TestCase):
    def test_sma_needs_a_full_window(self) -> None:
        self.assertIsNone(sma([1, 2], 5))
        self.assertEqual(sma([1, 2, 3, 4, 5], 5), 3.0)

    def test_rsi_of_a_straight_climb_is_high(self) -> None:
        values = [float(i) for i in range(1, 30)]
        value = rsi(values)
        self.assertIsNotNone(value)
        self.assertGreater(value or 0, 70)

    def test_support_resistance_from_highs_and_lows(self) -> None:
        bars = [
            {"h": 12, "l": 8, "c": 10},
            {"h": 15, "l": 7, "c": 11},
            {"h": 13, "l": 9, "c": 12},
        ]
        levels = support_resistance(bars)
        self.assertEqual(levels["support"], 7)
        self.assertEqual(levels["resistance"], 15)

    def test_summarize_marks_uptrend_on_rising_closes(self) -> None:
        bars = []
        price = 50.0
        for i in range(60):
            price *= 1.01
            bars.append({"o": price, "h": price * 1.01, "l": price * 0.99, "c": price, "v": 1_000})
        row = summarize_technicals(bars)
        self.assertTrue(row["uptrend"])
        self.assertIsNotNone(row["sma50"])
        self.assertIsNotNone(row["rsi"])


if __name__ == "__main__":
    unittest.main()
