"""Loop must skip when the tape is unchanged and never invent work."""

from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path

import threading
from unittest.mock import patch

from navin.trading.heartbeat import tick_watch
from navin.trading.lock import trading_desk_lock
from navin.trading.loop import (
    cycle_is_live,
    maybe_tick,
    peek_loop,
    recover_stale_cycle,
    start_loop,
    stop_loop,
    update_loop_schedule,
)
from navin.trading.store import TradingStore
from navin.trading.watch import run_watch
from navin.webui.trading_api import handle_trading_action


def _bars(start: float = 100.0, n: int = 80) -> list[dict[str, float]]:
    rows = []
    price = start
    for i in range(n):
        price *= 1.004
        rows.append({"t": 1_700_000_000 + i * 86400, "o": price, "h": price * 1.01, "l": price * 0.99, "c": price, "v": 1_000_000})
    return rows


_LAST = _bars()[-1]["c"]


class TradingLoopTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = TradingStore(Path(self.tmp.name))
        self.calls = {"n": 0}

        def http_get(url: str) -> bytes:
            self.calls["n"] += 1
            if "quote?" in url:
                symbols = url.split("symbols=")[-1].split("&")[0]
                names = symbols.replace("%2C", ",").split(",")
                return json.dumps(
                    {
                        "quoteResponse": {
                            "result": [
                                {
                                    "symbol": name,
                                    "regularMarketPrice": _LAST,
                                    "regularMarketChangePercent": 0.1,
                                    "shortName": name,
                                    "currency": "USD",
                                }
                                for name in names
                                if name
                            ]
                        }
                    }
                ).encode()
            if "/chart/" in url:
                return json.dumps(
                    {
                        "chart": {
                            "result": [
                                {
                                    "timestamp": [int(row["t"]) for row in _bars()],
                                    "indicators": {
                                        "quote": [
                                            {
                                                "open": [row["o"] for row in _bars()],
                                                "high": [row["h"] for row in _bars()],
                                                "low": [row["l"] for row in _bars()],
                                                "close": [row["c"] for row in _bars()],
                                                "volume": [row["v"] for row in _bars()],
                                            }
                                        ]
                                    },
                                }
                            ]
                        }
                    }
                ).encode()
            if "quoteSummary" in url:
                return json.dumps(
                    {
                        "quoteSummary": {
                            "result": [
                                {
                                    "financialData": {
                                        "revenueGrowth": {"raw": 0.22},
                                        "debtToEquity": {"raw": 0.4},
                                        "profitMargins": {"raw": 0.25},
                                        "recommendationKey": "buy",
                                    }
                                }
                            ]
                        }
                    }
                ).encode()
            if "headline" in url:
                return b"<rss><channel><item><title>Steady growth beat</title><link>https://example.com</link></item></channel></rss>"
            return b"{}"

        self.http_get = http_get

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_paused_loop_does_nothing(self) -> None:
        result = maybe_tick(self.store, http_get=self.http_get)
        self.assertFalse(result["did_work"])
        self.assertEqual(result["phase"], "paused")
        self.assertEqual(self.calls["n"], 0)

    def test_second_tick_skips_same_fingerprint(self) -> None:
        first = maybe_tick(self.store, force=True, http_get=self.http_get)
        self.assertTrue(first["did_work"])
        state = self.store.load_loop()
        state["enabled"] = True
        state["next_due"] = 0
        self.store.save_loop(state)
        second = maybe_tick(self.store, http_get=self.http_get)
        self.assertFalse(second["did_work"])
        self.assertEqual(second["reason"], "fingerprint")
        kinds = [row.get("kind") for row in self.store.load_journal(20)]
        self.assertIn("skip", kinds)
        self.assertIn("cycle", kinds)

    def test_start_and_stop(self) -> None:
        start_loop(self.store, http_get=self.http_get)
        self.assertTrue(self.store.load_loop()["enabled"])
        stop_loop(self.store)
        self.assertFalse(self.store.load_loop()["enabled"])

    def test_start_without_run_now_does_not_cycle(self) -> None:
        started = start_loop(
            self.store,
            schedule={"kind": "daily", "hour": 7, "minute": 15},
            http_get=self.http_get,
        )
        self.assertFalse(started["did_work"])
        self.assertEqual(started["phase"], "armed")
        self.assertEqual(started["loop"]["schedule"]["hour"], 7)
        self.assertEqual(self.calls["n"], 0)

    def test_force_tick_while_paused_stays_paused(self) -> None:
        result = maybe_tick(self.store, force=True, http_get=self.http_get)
        self.assertTrue(result["did_work"])
        self.assertFalse(self.store.load_loop()["enabled"])
        self.assertEqual(peek_loop(self.store)["phase"], "paused")

    def test_stop_then_supervisor_does_not_cycle(self) -> None:
        start_loop(self.store, http_get=self.http_get)
        paused = stop_loop(self.store)
        self.assertFalse(paused["enabled"])
        self.assertEqual(paused["phase"], "paused")
        ticked = maybe_tick(self.store, now=10**12, http_get=self.http_get)
        self.assertEqual(ticked["phase"], "paused")
        self.assertFalse(ticked["did_work"])
        self.assertEqual(self.calls["n"], 0)

    def test_api_tick_default_force_cycles_while_paused(self) -> None:
        with (
            patch("navin.webui.trading_api._store", return_value=self.store),
            patch("navin.trading.loop.fetch_quotes", return_value={}),
        ):
            ticked = handle_trading_action("tick", {})
        self.assertTrue(ticked["did_work"])
        self.assertFalse(ticked["loop"]["enabled"])
        self.assertEqual(ticked["loop"]["phase"], "paused")

    def test_failed_watch_does_not_set_last_watch(self) -> None:
        with patch(
            "navin.trading.watch.run_watch",
            return_value={"count": 1, "delivered": False, "digest": "Approval: NVDA", "sent": {}},
        ):
            maybe_tick(self.store, force=True, http_get=self.http_get)
        self.assertFalse(float(self.store.load_loop().get("last_watch") or 0))

    def test_heartbeat_retries_when_cycle_watch_was_not_delivered(self) -> None:
        self.store.save_orders(
            [
                {
                    "id": "ord-hot",
                    "symbol": "NVDA",
                    "side": "buy",
                    "qty": 1,
                    "price": 100,
                    "status": "pending",
                }
            ]
        )
        with patch(
            "navin.trading.watch.run_watch",
            return_value={"count": 1, "delivered": False, "digest": "Approval: NVDA", "sent": {}},
        ):
            maybe_tick(self.store, force=True, http_get=self.http_get)
        with patch("navin.trading.notify.deliver_alert", return_value={"webui": True}):
            again = tick_watch(self.store)
        self.assertEqual(again["count"], 1)
        self.assertIn("NVDA", again["digest"])

    def test_stale_cycle_recovers_to_idle(self) -> None:
        self.store.save_loop(
            {
                **self.store.load_loop(),
                "enabled": True,
                "phase": "scan",
                "cycle_pid": 2_147_000_000,
            }
        )
        state = peek_loop(self.store)
        self.assertEqual(state["phase"], "idle")
        self.assertTrue(state["enabled"])
        self.assertEqual(self.store.load_loop()["skipped_reason"], "stale_cycle")
        self.assertNotIn("cycle_pid", self.store.load_loop())
        self.assertFalse(cycle_is_live(self.store, self.store.load_loop()))

    def test_stale_cycle_while_paused_recovers_to_paused(self) -> None:
        self.store.save_loop(
            {
                **self.store.load_loop(),
                "enabled": False,
                "phase": "scan",
                "cycle_pid": 2_147_000_000,
            }
        )
        recovered = recover_stale_cycle(self.store)
        self.assertEqual(recovered["phase"], "paused")
        self.assertFalse(recovered["enabled"])
        self.assertEqual(peek_loop(self.store)["phase"], "paused")

    def test_watch_returns_busy_without_waiting(self) -> None:
        with trading_desk_lock(self.store, wait_s=0) as got:
            self.assertTrue(got)
            started = time.monotonic()
            payload = run_watch(self.store)
            self.assertLess(time.monotonic() - started, 0.4)
        self.assertEqual(payload.get("skipped"), "busy")
        self.assertEqual(payload["count"], 0)

    def test_leftover_stop_intent_is_persisted(self) -> None:
        start_loop(self.store, run_now=False, http_get=self.http_get)
        self.store.save_loop_intent({"enabled": False})
        state = peek_loop(self.store)
        self.assertFalse(state["enabled"])
        self.assertEqual(state["phase"], "paused")
        self.assertFalse(self.store.load_loop()["enabled"])
        self.assertFalse(self.store.load_loop_intent())

    def test_expired_cycle_recovers_even_if_marked_live(self) -> None:
        from navin.trading.loop import MAX_CYCLE_S, mark_loop_cycling

        mark_loop_cycling(self.store, True)
        try:
            self.store.save_loop(
                {
                    **self.store.load_loop(),
                    "enabled": True,
                    "phase": "scan",
                    "cycle_pid": 1,
                    "cycle_started_at": time.time() - MAX_CYCLE_S - 5,
                }
            )
            state = peek_loop(self.store)
            self.assertEqual(state["phase"], "idle")
            self.assertEqual(self.store.load_loop()["skipped_reason"], "stale_cycle")
        finally:
            mark_loop_cycling(self.store, False)

    def test_cycle_error_retries_soon(self) -> None:
        def boom(*_args, **_kwargs):
            raise RuntimeError("quotes down")

        with patch("navin.trading.loop.fetch_quotes", boom):
            failed = maybe_tick(self.store, force=True, now=1_700_000_000.0)
        self.assertFalse(failed["did_work"])
        self.assertEqual(failed["loop"]["skipped_reason"], "error")
        self.assertEqual(failed["loop"]["error_streak"], 1)
        self.assertLess(float(failed["loop"]["next_due"]), 1_700_000_000.0 + 200)

    def test_heartbeat_recovers_stale_scan_on_unused_desk(self) -> None:
        self.store.save_loop(
            {
                **self.store.load_loop(),
                "enabled": False,
                "phase": "scan",
                "cycle_pid": 2_147_000_000,
                "last_tick": 0,
            }
        )
        self.assertIsNone(tick_watch(self.store))
        self.assertEqual(self.store.load_loop()["phase"], "paused")


class TradingLoopOverlapTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = TradingStore(Path(self.tmp.name))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_second_tick_is_busy_while_cycle_runs(self) -> None:
        entered = threading.Event()
        release = threading.Event()
        result: dict[str, object] = {}

        def slow(*_args, **_kwargs):
            entered.set()
            release.wait(5)
            return {}

        def run() -> None:
            result.update(maybe_tick(self.store, force=True, http_get=lambda _url: b"{}"))

        with patch("navin.trading.loop.fetch_quotes", slow):
            worker = threading.Thread(target=run)
            worker.start()
            self.assertTrue(entered.wait(3))
            busy = maybe_tick(self.store, force=True)
            self.assertEqual(busy["phase"], "busy")
            self.assertFalse(busy["did_work"])
            release.set()
            worker.join(5)
        self.assertIn("phase", result)

    def test_live_cycle_is_not_recovered(self) -> None:
        entered = threading.Event()
        release = threading.Event()

        def slow(*_args, **_kwargs):
            entered.set()
            release.wait(5)
            return {}

        worker = threading.Thread(
            target=lambda: maybe_tick(self.store, force=True, http_get=lambda _url: b"{}")
        )
        with patch("navin.trading.loop.fetch_quotes", slow):
            worker.start()
            self.assertTrue(entered.wait(3))
            raw = self.store.load_loop()
            self.assertEqual(raw["phase"], "scan")
            self.assertTrue(cycle_is_live(self.store, raw))
            self.assertEqual(peek_loop(self.store)["phase"], "scan")
            self.assertEqual(self.store.load_loop()["phase"], "scan")
            self.assertNotEqual(self.store.load_loop().get("skipped_reason"), "stale_cycle")
            release.set()
            worker.join(5)

    def test_stop_during_cycle_stays_paused_after(self) -> None:
        entered = threading.Event()
        release = threading.Event()

        def slow(*_args, **_kwargs):
            entered.set()
            release.wait(5)
            return {}

        def run() -> None:
            start_loop(self.store, run_now=True, http_get=lambda _url: b"{}")

        with patch("navin.trading.loop.fetch_quotes", slow):
            worker = threading.Thread(target=run)
            worker.start()
            self.assertTrue(entered.wait(3))
            paused = stop_loop(self.store)
            self.assertFalse(paused["enabled"])
            self.assertEqual(paused["phase"], "paused")
            self.assertFalse(peek_loop(self.store)["enabled"])
            release.set()
            worker.join(5)
        final = peek_loop(self.store)
        self.assertFalse(final["enabled"])
        self.assertEqual(final["phase"], "paused")
        self.assertFalse(self.store.load_loop_intent())

    def test_schedule_during_cycle_is_kept(self) -> None:
        entered = threading.Event()
        release = threading.Event()

        def slow(*_args, **_kwargs):
            entered.set()
            release.wait(5)
            return {}

        with patch("navin.trading.loop.fetch_quotes", slow):
            worker = threading.Thread(
                target=lambda: maybe_tick(self.store, force=True, http_get=lambda _url: b"{}")
            )
            worker.start()
            self.assertTrue(entered.wait(3))
            update_loop_schedule(self.store, schedule={"kind": "weekend", "hour": 11, "minute": 45})
            self.assertEqual(peek_loop(self.store)["schedule"]["kind"], "weekend")
            release.set()
            worker.join(5)
        self.assertEqual(self.store.load_loop()["schedule"]["kind"], "weekend")

    def test_start_during_force_cycle_keeps_loop_armed(self) -> None:
        entered = threading.Event()
        release = threading.Event()
        result: dict[str, object] = {}

        def slow(*_args, **_kwargs):
            entered.set()
            release.wait(5)
            return {}

        def run() -> None:
            result.update(maybe_tick(self.store, force=True, http_get=lambda _url: b"{}"))

        with patch("navin.trading.loop.fetch_quotes", slow):
            worker = threading.Thread(target=run)
            worker.start()
            self.assertTrue(entered.wait(3))
            started = start_loop(self.store, run_now=False, http_get=lambda _url: b"{}")
            self.assertTrue(started["loop"]["enabled"])
            release.set()
            worker.join(5)
        self.assertTrue(peek_loop(self.store)["enabled"])
        self.assertNotEqual(result.get("loop", {}).get("phase"), "scan")

    def test_heartbeat_watch_is_busy_while_cycle_runs(self) -> None:
        entered = threading.Event()
        release = threading.Event()

        def slow(*_args, **_kwargs):
            entered.set()
            release.wait(5)
            return {}

        with patch("navin.trading.loop.fetch_quotes", slow):
            worker = threading.Thread(
                target=lambda: maybe_tick(self.store, force=True, http_get=lambda _url: b"{}")
            )
            worker.start()
            self.assertTrue(entered.wait(3))
            hb = tick_watch(self.store)
            self.assertEqual(hb.get("skipped"), "loop_cycling")
            self.assertEqual(hb["count"], 0)
            release.set()
            worker.join(5)


if __name__ == "__main__":
    unittest.main()
