"""Market data on the real response shapes: sessions, currencies, FX, NFT floors."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from navin.trading import market
from navin.trading.errors import TradingError
from navin.trading.screen import screen_candidate
from navin.trading.store import TradingStore


def _chart(closes: list[float], currency: str = "USD", name: str = "Test") -> bytes:
    return json.dumps(
        {
            "chart": {
                "result": [
                    {
                        "meta": {"currency": currency, "shortName": name, "fullExchangeName": "NMS", "regularMarketPrice": closes[-1]},
                        "timestamp": [1_700_000_000 + i * 86400 for i in range(len(closes))],
                        "indicators": {"quote": [{"open": closes, "high": [c * 1.01 for c in closes], "low": [c * 0.99 for c in closes], "close": closes, "volume": [1000] * len(closes)}]},
                    }
                ]
            }
        }
    ).encode()


class YahooSessionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = TradingStore(Path(self.tmp.name))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_injected_getter_needs_no_crumb_and_batch_carries_currency(self) -> None:
        seen: list[str] = []

        def http_get(url: str) -> bytes:
            seen.append(url)
            if "v7/finance/quote" in url:
                return json.dumps(
                    {
                        "quoteResponse": {
                            "result": [
                                {"symbol": "AAPL", "regularMarketPrice": 300.0, "currency": "USD", "shortName": "Apple", "marketState": "REGULAR", "quoteType": "EQUITY"},
                                {"symbol": "SHEL.L", "regularMarketPrice": 2650.0, "currency": "GBp", "shortName": "Shell"},
                                {"symbol": "MC.PA", "regularMarketPrice": 600.0, "currency": "EUR", "shortName": "LVMH"},
                            ]
                        }
                    }
                ).encode()
            return b"{}"

        quotes = market.fetch_quotes(["AAPL", "SHEL.L", "MC.PA"], store=self.store, http_get=http_get)
        self.assertEqual(len(seen), 1)
        self.assertNotIn("crumb=", seen[0])
        self.assertEqual(quotes["AAPL"]["currency"], "USD")
        self.assertEqual(quotes["MC.PA"]["currency"], "EUR")
        # Pence become pounds so the book never sums 2650 GBp as 2650 GBP.
        self.assertEqual(quotes["SHEL.L"]["currency"], "GBP")
        self.assertAlmostEqual(quotes["SHEL.L"]["price"], 26.5)
        self.assertEqual(market.symbol_meta("MC.PA", self.store)["currency"], "EUR")

    def test_default_getter_adds_cookie_and_crumb_and_refreshes_on_401(self) -> None:
        calls: list[tuple[str, dict | None]] = []
        sessions = iter([{"cookie": "A3=old", "crumb": "old", "t": 1}, {"cookie": "A3=new", "crumb": "new", "t": 2}])

        def fake_default(url: str, timeout: float = 12.0, headers: dict | None = None) -> bytes:
            calls.append((url, headers))
            if "crumb=old" in url:
                raise TradingError("market data HTTP 401 for x", status=502)
            return json.dumps({"quoteResponse": {"result": [{"symbol": "AAPL", "regularMarketPrice": 1.0, "currency": "USD"}]}}).encode()

        original_default = market.default_http_get
        original_acquire = market.acquire_yahoo_session
        market.default_http_get = fake_default
        market.acquire_yahoo_session = lambda timeout=12.0: next(sessions)
        try:
            payload = market._yahoo_json(self.store, fake_default, "https://query1.finance.yahoo.com/v7/finance/quote?symbols=AAPL")
        finally:
            market.default_http_get = original_default
            market.acquire_yahoo_session = original_acquire
        self.assertEqual(payload["quoteResponse"]["result"][0]["symbol"], "AAPL")
        self.assertEqual(len(calls), 2)
        self.assertIn("crumb=old", calls[0][0])
        self.assertEqual(calls[0][1], {"Cookie": "A3=old"})
        self.assertIn("crumb=new", calls[1][0])
        self.assertEqual(self.store.cache_get("yahoo-session", 3600)["crumb"], "new")

    def test_chart_fallback_learns_currency_from_meta(self) -> None:
        def http_get(url: str) -> bytes:
            if "/chart/" in url:
                return _chart([100 + i for i in range(60)], currency="JPY", name="Toyota")
            return b"{}"

        quotes = market.fetch_quotes(["7203.T"], store=self.store, http_get=http_get)
        self.assertEqual(quotes["7203.T"]["currency"], "JPY")
        self.assertEqual(quotes["7203.T"]["name"], "Toyota")
        self.assertEqual(quotes["7203.T"]["source"], "ohlcv")

    def test_book_prices_convert_to_the_book_currency(self) -> None:
        quotes = {
            "AAPL": {"symbol": "AAPL", "price": 300.0, "currency": "USD"},
            "MC.PA": {"symbol": "MC.PA", "price": 500.0, "currency": "EUR"},
            "EURUSD=X": {"symbol": "EURUSD=X", "price": 1.1, "currency": "USD"},
        }
        prices, fx = market.book_prices(quotes, "USD", rates={"USD": 1.0, "EUR": 1.1})
        self.assertAlmostEqual(prices["MC.PA"], 550.0)
        self.assertEqual(prices["AAPL"], 300.0)
        self.assertEqual(prices["EURUSD=X"], 1.1)
        self.assertEqual(fx["EUR"], 1.1)

    def test_fx_rates_come_from_pairs_against_the_book_currency(self) -> None:
        asked: list[str] = []

        def http_get(url: str) -> bytes:
            if "v7/finance/quote" in url:
                symbols = parse_qs(urlparse(url).query)["symbols"][0].split(",")
                asked.extend(symbols)
                return json.dumps({"quoteResponse": {"result": [{"symbol": s, "regularMarketPrice": 0.9 if s.startswith("USD") else 160.0, "currency": "EUR"} for s in symbols]}}).encode()
            return b"{}"

        rates = market.fetch_fx_rates(["USD", "GBP", "EUR"], "EUR", store=self.store, http_get=http_get)
        self.assertEqual(sorted(asked), ["GBPEUR=X", "USDEUR=X"])
        self.assertEqual(rates["EUR"], 1.0)
        self.assertEqual(rates["USD"], 0.9)

    def test_fundamentals_parse_and_failures_are_retried_later(self) -> None:
        state = {"fail": True}

        def http_get(url: str) -> bytes:
            if "quoteSummary" in url:
                if state["fail"]:
                    raise TradingError("market data HTTP 500", status=502)
                return json.dumps({"quoteSummary": {"result": [{"financialData": {"revenueGrowth": {"raw": 0.164}, "debtToEquity": {"raw": 78.4}, "profitMargins": {"raw": 0.27}, "recommendationKey": "buy", "targetMeanPrice": {"raw": 350}}, "defaultKeyStatistics": {"trailingPE": {"raw": 30.2}}}]}}).encode()
            return b"{}"

        first = market.fetch_fundamentals("AAPL", store=self.store, http_get=http_get)
        self.assertFalse(first["available"])
        state["fail"] = False
        second = market.fetch_fundamentals("AAPL", store=self.store, http_get=http_get)
        self.assertFalse(second["available"], "failure is cached for a short while")
        self.store.cache_set("fund-AAPL", {**first})
        cache_path = self.store.root / "cache" / "fund-AAPL.json"
        stale = json.loads(cache_path.read_text())
        stale["t"] -= 1000
        cache_path.write_text(json.dumps(stale))
        third = market.fetch_fundamentals("AAPL", store=self.store, http_get=http_get)
        self.assertTrue(third["available"])
        self.assertAlmostEqual(third["revenue_growth"], 16.4)
        self.assertEqual(third["pe"], 30.2)
        self.assertEqual(third["target"], 350)

    def test_nft_floor_keeps_native_currency_honest_and_builds_history(self) -> None:
        def http_get(url: str) -> bytes:
            return json.dumps({"name": "Bored Ape Yacht Club", "native_currency": "ethereum", "native_currency_symbol": "ETH", "floor_price": {"native_currency": 7.5}, "floor_price_in_usd_24h_percentage_change": -4.3}).encode()

        quote = market.fetch_nft_quote("NFT-BAYC", store=self.store, http_get=http_get)
        self.assertEqual(quote["currency"], "ETH")
        self.assertEqual(quote["price"], 7.5)
        bars = market.fetch_ohlcv("NFT-BAYC", store=self.store, http_get=http_get)
        self.assertEqual(len(bars), 1)
        snap = {"symbol": "NFT-BAYC", "fundamental": {"kind": "nft", "available": False}, "technical": {"uptrend": False, "bars": 1}, "sentiment": {}}
        screen = screen_candidate(snap, {"require_uptrend": True})
        self.assertTrue(screen["passed"], screen["rejected"])
        self.assertTrue(any("collecting" in item for item in screen["reasons"]))
        screen = screen_candidate({**snap, "technical": {"uptrend": False, "bars": 45}}, {"require_uptrend": True})
        self.assertFalse(screen["passed"])

    def test_news_is_parsed_with_defusedxml_and_scored(self) -> None:
        def http_get(url: str) -> bytes:
            return b"<rss><channel><item><title>Apple beats estimates, raises guidance</title><link>https://x</link><pubDate>Mon</pubDate></item><item><title>Lawsuit probe widens</title><link>https://y</link></item></channel></rss>"

        news = market.fetch_news("AAPL", store=self.store, http_get=http_get)
        self.assertEqual(len(news), 2)
        self.assertEqual(news[0]["published"], "Mon")
        score = market.score_headlines(news)
        self.assertEqual(score["method"], "keywords")
        self.assertEqual(score["hits"], 2)
        self.assertEqual(market.sentiment_for("AAPL", news, store=self.store, ai=False)["method"], "keywords")


if __name__ == "__main__":
    unittest.main()
