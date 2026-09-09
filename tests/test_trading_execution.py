# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Manual orders, broker adapters, reconciliation and the intraday guard. No network."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from navin.trading import brokers
from navin.trading.errors import TradingError
from navin.trading.loop import guard_tick
from navin.trading.metrics import daily_closes, summarize_curve, vs_benchmark
from navin.trading.portfolio import (
    close_position,
    manual_order,
    place_order,
    reconcile_orders,
    update_stop,
)
from navin.trading.risk import evaluate_order, limits_from, size_buy
from navin.trading.store import TradingStore
from navin.webui import trading_api


class ManualOrderTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = TradingStore(Path(self.tmp.name))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_manual_buy_then_close_books_pnl_and_keeps_quote_currency(self) -> None:
        prices = {"MC.PA": 500.0}
        order = manual_order(self.store, side="buy", symbol="MC.PA", prices=prices, qty=1, quote_currency="EUR", fx_rate=1.1, thesis="desk")
        self.assertEqual(order["status"], "filled")
        self.assertEqual(order["origin"], "manual")
        self.assertEqual(order["quote_currency"], "EUR")
        pos = self.store.load_portfolio()["positions"][0]
        self.assertEqual(pos["currency"], "EUR")
        self.assertEqual(pos["broker"], "paper")
        closed = close_position(self.store, "MC.PA", {"MC.PA": 550.0})
        self.assertEqual(closed["status"], "filled")
        self.assertAlmostEqual(float(closed["pnl"]), 50.0)
        self.assertEqual(self.store.load_portfolio()["positions"], [])

    def test_notional_too_small_for_one_share_is_a_clear_400(self) -> None:
        with self.assertRaises(TradingError) as ctx:
            manual_order(self.store, side="buy", symbol="AAPL", prices={"AAPL": 320.0}, notional=100)
        self.assertEqual(ctx.exception.status, 400)
        self.assertIn("less than one AAPL", ctx.exception.message)

    def test_notional_sizes_crypto_fractionally(self) -> None:
        order = manual_order(self.store, side="buy", symbol="BTC-USD", prices={"BTC-USD": 60_000.0}, notional=300)
        self.assertEqual(order["status"], "filled")
        self.assertAlmostEqual(order["qty"], 0.005)

    def test_manual_buy_still_obeys_hard_limits(self) -> None:
        order = manual_order(self.store, side="buy", symbol="AAPL", prices={"AAPL": 320.0}, qty=5)
        self.assertEqual(order["status"], "blocked")
        self.assertTrue(any("max_position" in item for item in order["blocks"]))

    def test_sell_is_never_blocked_by_drawdown_or_block_list(self) -> None:
        place_order(self.store, side="buy", symbol="NVDA", qty=2, price=100, reason="open", confidence=90, execution_mode="autonomous", prices={"NVDA": 100})
        settings = self.store.load_settings()
        settings["risk"]["blocked_assets"] = ["NVDA"]
        settings["risk"]["max_drawdown_pct"] = 1.0
        self.store.save_settings(settings)
        book = self.store.load_portfolio()
        book["high_water"] = 50_000.0  # deep drawdown on paper
        self.store.save_portfolio(book)
        verdict = evaluate_order(side="sell", symbol="NVDA", qty=2, price=60, portfolio=self.store.load_portfolio(), prices={"NVDA": 60}, limits=limits_from(settings), orders_today=0, execution_mode="autonomous")
        self.assertTrue(verdict["allow"], verdict["blocks"])
        verdict = evaluate_order(side="sell", symbol="NVDA", qty=5, price=60, portfolio=self.store.load_portfolio(), prices={"NVDA": 60}, limits=limits_from(settings), orders_today=0, execution_mode="autonomous")
        self.assertFalse(verdict["allow"])
        self.assertTrue(any("held" in item for item in verdict["blocks"]))

    def test_size_buy_takes_one_share_when_it_fits(self) -> None:
        limits = limits_from(None)
        self.assertEqual(size_buy(equity=20_000, price=320, limits=limits, confidence=72, symbol="AAPL"), 1.0)
        self.assertEqual(size_buy(equity=20_000, price=900, limits=limits, confidence=99, symbol="XYZ"), 0.0)
        self.assertAlmostEqual(size_buy(equity=20_000, price=60_000, limits=limits, confidence=100, symbol="BTC-USD"), 0.01)

    def test_update_stop_validates_against_last_price(self) -> None:
        place_order(self.store, side="buy", symbol="NVDA", qty=2, price=100, reason="open", confidence=90, execution_mode="autonomous", prices={"NVDA": 100})
        pos = update_stop(self.store, "NVDA", stop=90, take=130)
        self.assertEqual(pos["stop"], 90)
        self.assertEqual(pos["take"], 130)
        with self.assertRaises(TradingError):
            update_stop(self.store, "NVDA", stop=120)
        with self.assertRaises(TradingError):
            update_stop(self.store, "AAPL", stop=1)
        pos = update_stop(self.store, "NVDA", clear_take=True)
        self.assertIsNone(pos["take"])

    def test_curve_is_sampled_and_sharpe_uses_daily_closes(self) -> None:
        for _ in range(5):
            place_order(self.store, side="buy", symbol="NVDA", qty=0.0, price=100, reason="noop", confidence=90, execution_mode="autonomous", prices={"NVDA": 100})
        book = self.store.load_portfolio()
        self.assertLessEqual(len(book["equity_curve"]), 2)
        curve = [{"t": 86400 * day + 3600 * hour, "equity": 100 + day + hour / 100} for day in range(5) for hour in range(6)]
        self.assertEqual(len(daily_closes(curve)), 5)
        stats = summarize_curve(curve)
        self.assertEqual(stats["days"], 5)
        self.assertIsNotNone(stats["sharpe"])

    def test_vs_benchmark_alpha_and_beta_from_aligned_days(self) -> None:
        moves = [0.02, -0.01, 0.03, -0.02, 0.01, 0.04, -0.03, 0.02, 0.01]
        bench_vals = [50.0]
        port_vals = [100.0]
        for move in moves:
            bench_vals.append(bench_vals[-1] * (1 + move))
            port_vals.append(port_vals[-1] * (1 + move / 2))
        curve = [{"t": 86400 * day, "equity": value} for day, value in enumerate(port_vals)]
        bench = [{"t": 86400 * day, "c": value} for day, value in enumerate(bench_vals)]
        out = vs_benchmark(curve, bench)
        self.assertEqual(out["days"], 10)
        self.assertAlmostEqual(out["beta"], 0.5, places=2)
        self.assertLess(out["alpha"], 0)


class BrokerAdapterTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = TradingStore(Path(self.tmp.name))
        self.calls: list[tuple[str, str, dict[str, str], bytes | None]] = []

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _http(self, responses: dict[str, tuple[int, dict]]):
        def http(method: str, url: str, headers: dict[str, str], body: bytes | None) -> tuple[int, bytes]:
            self.calls.append((method, url, headers, body))
            for key, (status, payload) in responses.items():
                if key in url and (method != "GET" or "orders/" in url or "account" in url or "order?" in url):
                    return status, json.dumps(payload).encode()
            return 404, b"{}"

        return http

    def test_paper_status_and_secrets_never_leak(self) -> None:
        status = brokers.broker_status(self.store)
        self.assertEqual(status["broker"], "paper")
        self.assertFalse(status["live"])
        self.store.save_secret("alpaca_key", "AK")
        status = brokers.broker_status(self.store)
        alpaca = next(row for row in status["brokers"] if row["id"] == "alpaca")
        self.assertFalse(alpaca["configured"])
        self.assertEqual([row["set"] for row in alpaca["secrets"]], [True, False])
        self.assertNotIn("AK", json.dumps(status))
        self.assertEqual(oct(self.store.secrets_path().stat().st_mode & 0o777), "0o600")

    def test_alpaca_submit_polls_and_reconciles_a_fill(self) -> None:
        self.store.save_secret("alpaca_key", "AK")
        self.store.save_secret("alpaca_secret", "AS")
        settings = self.store.load_settings()
        settings["execution"] = {"broker": "alpaca", "alpaca_paper": True}
        self.store.save_settings(settings)
        self.assertEqual(brokers.can_route(self.store, "AAPL"), ("alpaca", "AAPL"))
        self.assertEqual(brokers.can_route(self.store, "BTC-USD"), ("alpaca", "BTC/USD"))
        self.assertEqual(brokers.can_route(self.store, "MC.PA"), ("paper", "MC.PA"))
        http = self._http({"/v2/orders": (200, {"id": "abc", "status": "accepted", "filled_qty": "0"})})
        order = place_order(self.store, side="buy", symbol="AAPL", qty=1, price=100, reason="t", confidence=90, execution_mode="autonomous", prices={"AAPL": 100}, http=http)
        self.assertEqual(order["status"], "submitted")
        self.assertEqual(order["broker"], "alpaca")
        self.assertEqual(order["broker_order_id"], "abc")
        self.assertEqual(self.store.load_portfolio()["positions"], [])
        method, url, headers, body = self.calls[0]
        self.assertEqual(method, "POST")
        self.assertTrue(url.startswith(brokers.ALPACA_PAPER))
        self.assertEqual(headers["APCA-API-KEY-ID"], "AK")
        self.assertEqual(json.loads(body)["symbol"], "AAPL")
        http = self._http({"/v2/orders/abc": (200, {"id": "abc", "status": "filled", "filled_qty": "1", "filled_avg_price": "101.5"})})
        changed = reconcile_orders(self.store, {"AAPL": 101.5}, http=http)
        self.assertEqual(len(changed), 1)
        self.assertEqual(changed[0]["status"], "filled")
        self.assertAlmostEqual(changed[0]["fill_price"], 101.5)
        book = self.store.load_portfolio()
        self.assertEqual(book["positions"][0]["qty"], 1)
        self.assertAlmostEqual(book["positions"][0]["avg"], 101.5)
        self.assertAlmostEqual(book["cash"], 20_000 - 101.5)

    def test_alpaca_rejection_is_a_failed_order_not_a_fill(self) -> None:
        self.store.save_secret("alpaca_key", "AK")
        self.store.save_secret("alpaca_secret", "AS")
        settings = self.store.load_settings()
        settings["execution"] = {"broker": "alpaca"}
        self.store.save_settings(settings)
        http = self._http({"/v2/orders": (403, {"message": "insufficient buying power"})})
        order = place_order(self.store, side="buy", symbol="AAPL", qty=1, price=100, reason="t", confidence=90, execution_mode="autonomous", prices={"AAPL": 100}, http=http)
        self.assertEqual(order["status"], "failed")
        self.assertIn("insufficient", order["error"])
        self.assertEqual(self.store.load_portfolio()["cash"], 20_000)

    def test_binance_signed_market_order_fills_from_fills_array(self) -> None:
        self.store.save_secret("binance_key", "BK")
        self.store.save_secret("binance_secret", "BS")
        settings = self.store.load_settings()
        settings["execution"] = {"broker": "binance", "binance_testnet": True}
        self.store.save_settings(settings)
        self.assertEqual(brokers.can_route(self.store, "ETH-USD"), ("binance", "ETHUSDT"))
        self.assertEqual(brokers.can_route(self.store, "AAPL"), ("paper", "AAPL"))
        payload = {"orderId": 77, "status": "FILLED", "executedQty": "0.1", "fills": [{"price": "3000", "qty": "0.05"}, {"price": "3010", "qty": "0.05"}]}
        http = self._http({"/api/v3/order?": (200, payload)})
        order = place_order(self.store, side="buy", symbol="ETH-USD", qty=0.1, price=3000, reason="t", confidence=90, execution_mode="autonomous", prices={"ETH-USD": 3000}, http=http)
        self.assertEqual(order["status"], "filled")
        self.assertEqual(order["broker"], "binance")
        self.assertAlmostEqual(order["fill_price"], 3005.0)
        method, url, headers, _body = self.calls[0]
        self.assertTrue(url.startswith(brokers.BINANCE_TESTNET))
        self.assertIn("signature=", url)
        self.assertIn("quantity=0.1", url)
        self.assertEqual(headers["X-MBX-APIKEY"], "BK")

    def test_connection_test_reports_missing_keys_and_account(self) -> None:
        self.assertEqual(brokers.test_connection(self.store, "alpaca")["ok"], False)
        self.store.save_secret("alpaca_key", "AK")
        self.store.save_secret("alpaca_secret", "AS")
        http = self._http({"/v2/account": (200, {"account_number": "PA1", "status": "ACTIVE", "currency": "USD", "cash": "1000", "equity": "1200", "buying_power": "2000"})})
        out = brokers.test_connection(self.store, "alpaca", http=http)
        self.assertTrue(out["ok"])
        self.assertEqual(out["account"], "PA1")
        self.assertEqual(out["equity"], 1200.0)


class GuardTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = TradingStore(Path(self.tmp.name))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_guard_sells_a_stop_and_warns_before_the_engine(self) -> None:
        place_order(self.store, side="buy", symbol="NVDA", qty=2, price=100, reason="open", confidence=90, execution_mode="autonomous", prices={"NVDA": 100})
        place_order(self.store, side="buy", symbol="AAPL", qty=1, price=300, reason="open", confidence=90, execution_mode="autonomous", prices={"AAPL": 300, "NVDA": 100})
        state = self.store.load_loop()
        state["enabled"] = True
        self.store.save_loop(state)
        delivered: list[dict] = []

        def fake_quotes(symbols, *, store=None, http_get=None):
            return {
                "NVDA": {"symbol": "NVDA", "price": 94.0, "currency": "USD"},
                "AAPL": {"symbol": "AAPL", "price": 286.0, "currency": "USD"},
            }

        with mock.patch("navin.trading.loop.fetch_quotes", side_effect=fake_quotes), mock.patch(
            "navin.trading.loop.deliver_alert", side_effect=lambda store, **kw: delivered.append(kw) or {"webui": True}
        ), mock.patch("navin.trading.loop.alert_cycle", return_value={"webui": True}):
            out = guard_tick(self.store, force=True)
        self.assertTrue(out["did_work"])
        self.assertEqual([row["symbol"] for row in out["stops"]], ["NVDA"])
        self.assertEqual(out["stops"][0]["status"], "filled")
        self.assertEqual([pos["symbol"] for pos in self.store.load_portfolio()["positions"]], ["AAPL"])
        keys = [row["key"] for row in out["alerts"]]
        self.assertIn("stop-AAPL", keys)
        self.assertTrue(delivered)
        self.assertIn("AAPL", delivered[0]["detail"])
        # Same warning is not repeated the same day.
        with mock.patch("navin.trading.loop.fetch_quotes", side_effect=fake_quotes), mock.patch("navin.trading.loop.deliver_alert"), mock.patch("navin.trading.loop.alert_cycle"):
            again = guard_tick(self.store, force=True)
        self.assertEqual(again["alerts"], [])
        self.assertIn("guard", self.store.load_loop()["last_guard_result"])

    def test_guard_respects_its_interval_and_the_pause(self) -> None:
        out = guard_tick(self.store)
        self.assertFalse(out["did_work"])
        self.assertEqual(out["reason"], "loop paused")
        state = self.store.load_loop()
        state["enabled"] = True
        self.store.save_loop(state)
        with mock.patch("navin.trading.loop.fetch_quotes") as quotes:
            out = guard_tick(self.store)
            self.assertFalse(out["did_work"])
            self.assertEqual(out["reason"], "nothing to guard")
            self.assertFalse(quotes.called)
        out = guard_tick(self.store)
        self.assertEqual(out["reason"], "not due")


class TradingApiOrderActionsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = TradingStore(Path(self.tmp.name))
        self.patch = mock.patch.object(trading_api, "_store", return_value=self.store)
        self.patch.start()
        quotes = {
            "AAPL": {"symbol": "AAPL", "price": 300.0, "currency": "USD", "name": "Apple"},
            "MC.PA": {"symbol": "MC.PA", "price": 500.0, "currency": "EUR", "name": "LVMH"},
            "EURUSD=X": {"symbol": "EURUSD=X", "price": 1.1, "currency": "USD"},
        }

        def fake_quotes(symbols, *, store=None, http_get=None):
            return {symbol: quotes[symbol] for symbol in symbols if symbol in quotes}

        self.quotes = mock.patch("navin.trading.market.fetch_quotes", side_effect=fake_quotes)
        self.quotes.start()
        self.loop_quotes = mock.patch("navin.trading.loop.fetch_quotes", side_effect=fake_quotes)
        self.loop_quotes.start()

    def tearDown(self) -> None:
        self.loop_quotes.stop()
        self.quotes.stop()
        self.patch.stop()
        self.tmp.cleanup()

    def test_order_close_set_stop_and_broker_actions(self) -> None:
        out = trading_api.handle_trading_action("buy", {"symbol": "MC.PA", "qty": 1, "thesis": "desk"})
        order = out["order"]
        self.assertEqual(order["status"], "filled")
        self.assertEqual(order["quote_currency"], "EUR")
        self.assertAlmostEqual(order["price"], 550.0)  # 500 EUR at 1.10
        self.assertAlmostEqual(order["fx_rate"], 1.1)
        pos = out["portfolio"]["positions"][0]
        self.assertEqual(pos["currency"], "EUR")
        out = trading_api.handle_trading_action("set_stop", {"symbol": "MC.PA", "stop": 500})
        self.assertEqual(out["position"]["stop"], 500)
        with self.assertRaises(TradingError):
            trading_api.handle_trading_action("set_stop", {"symbol": "MC.PA", "stop": 600})
        out = trading_api.handle_trading_action("close", {"symbol": "MC.PA"})
        self.assertEqual(out["order"]["status"], "filled")
        self.assertEqual(out["portfolio"]["positions"], [])
        with self.assertRaises(TradingError) as ctx:
            trading_api.handle_trading_action("order", {"side": "sell", "symbol": "AAPL", "qty": 1})
        self.assertEqual(ctx.exception.status, 404)
        out = trading_api.handle_trading_action("broker", {"broker": "alpaca", "alpaca_paper": True, "fee_bps": 2})
        self.assertEqual(out["settings"]["execution"]["broker"], "alpaca")
        self.assertEqual(out["settings"]["execution"]["fee_bps"], 2.0)
        self.assertFalse(out["broker"]["live"])
        out = trading_api.handle_trading_action("secret", {"name": "alpaca_key", "value": "AK"})
        self.assertTrue(next(row for row in out["broker"]["brokers"] if row["id"] == "alpaca")["secrets"][0]["set"])
        with self.assertRaises(TradingError):
            trading_api.handle_trading_action("secret", {"name": "password", "value": "x"})
        with self.assertRaises(TradingError):
            trading_api.handle_trading_action("broker", {"broker": "robinhood"})
        # Without both keys the desk stays on paper even if alpaca is selected.
        out = trading_api.handle_trading_action("buy", {"symbol": "AAPL", "qty": 1})
        self.assertEqual(out["order"]["broker"], "paper")

    def test_settings_accepts_every_risk_limit_and_alerts(self) -> None:
        out = trading_api.handle_trading_action(
            "settings",
            {
                "risk": {"max_sector_pct": 25, "max_daily_orders": "6", "min_cash_pct": 8, "take_profit_pct": "", "allowed_assets": "AAPL, msft", "blocked_assets": []},
                "alerts": {"stop_approach_pct": 2, "price_levels": [{"symbol": "aapl", "price": 350, "when": "above"}, {"symbol": "", "price": 1}]},
                "ai_assist": False,
            },
        )
        risk = out["settings"]["risk"]
        self.assertEqual(risk["max_sector_pct"], 25.0)
        self.assertEqual(risk["max_daily_orders"], 6)
        self.assertIsNone(risk["take_profit_pct"])
        self.assertEqual(risk["allowed_assets"], ["AAPL", "MSFT"])
        self.assertEqual(out["settings"]["alerts"]["price_levels"], [{"symbol": "AAPL", "price": 350.0, "when": "above"}])
        self.assertFalse(out["settings"]["ai_assist"])
        with self.assertRaises(TradingError):
            trading_api.handle_trading_action("settings", {"risk": {"max_sector_pct": "wide"}})


if __name__ == "__main__":
    unittest.main()
