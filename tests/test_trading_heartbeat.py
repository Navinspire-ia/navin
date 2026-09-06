"""Trading heartbeat is watch only and never ticks the desk loop."""

from __future__ import annotations

import asyncio
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from navin.agent.tools.context import RequestContext, request_context
from navin.agent.tools.trading import TradingTool
from navin.gateway.heartbeat_desks import tick_heartbeat_desks
from navin.trading.errors import TradingError
from navin.trading.heartbeat import HEARTBEAT_TRADING_ACTIONS, tick_watch
from navin.trading.store import TradingStore
from navin.trading.watch import run_watch
from navin.webui.trading_api import handle_trading_action

ROOT = Path(__file__).resolve().parents[1]


def _hb_ctx() -> RequestContext:
    return RequestContext(
        channel="telegram",
        chat_id="1",
        session_key="heartbeat",
        metadata={"heartbeat": True},
    )


class TradingHeartbeatGateTest(unittest.TestCase):
    def test_allowed_actions(self) -> None:
        self.assertEqual(
            HEARTBEAT_TRADING_ACTIONS,
            {"status", "snapshot", "journal", "watch"},
        )

    def test_tool_and_http_refuse_start_stop_schedule_tick(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = TradingStore(Path(tmp))
            tool = TradingTool()
            with patch("navin.webui.trading_api._store", return_value=store):
                with request_context(_hb_ctx()):
                    for action in ("start", "stop", "schedule", "tick", "research"):
                        result = asyncio.run(tool.execute(action=action))
                        self.assertTrue(getattr(result, "is_error", False), action)
                        self.assertIn("heartbeat", str(result).lower(), action)
                        with self.assertRaises(TradingError, msg=action):
                            handle_trading_action(action, {"schedule": {"kind": "daily", "hour": 9}})
                    watch = asyncio.run(tool.execute(action="watch"))
                    snap = handle_trading_action("snapshot")
            self.assertFalse(getattr(watch, "is_error", False), watch)
            self.assertEqual(watch["watch"]["count"], 0)
            self.assertIn("portfolio", snap)

    def test_unused_desk_skips_heartbeat_tick(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = TradingStore(Path(tmp))
            self.assertIsNone(tick_watch(store))

    def test_pending_order_is_reported_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = TradingStore(Path(tmp))
            store.save_orders(
                [
                    {
                        "id": "ord-1",
                        "symbol": "NVDA",
                        "side": "buy",
                        "qty": 1,
                        "price": 100,
                        "status": "pending",
                    }
                ]
            )
            with patch("navin.trading.notify.deliver_alert", return_value={"webui": True}):
                first = tick_watch(store)
                self.assertIsNotNone(first)
                self.assertEqual(first["count"], 1)
                self.assertIn("NVDA", first["digest"])
                self.assertTrue(first.get("delivered"))
                second = tick_watch(store)
            self.assertEqual(second["count"], 0)
            self.assertNotEqual(second.get("skipped"), "loop_just_watched")

    def test_notify_crash_retries_on_the_next_tick(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = TradingStore(Path(tmp))
            store.save_orders(
                [
                    {
                        "id": "ord-x",
                        "symbol": "MSFT",
                        "side": "buy",
                        "qty": 1,
                        "price": 10,
                        "status": "pending",
                    }
                ]
            )
            with patch("navin.trading.notify.deliver_alert", side_effect=RuntimeError("bus down")):
                payload = run_watch(store)
            self.assertEqual(payload["count"], 1)
            self.assertEqual(payload["sent"], {})
            self.assertFalse(payload.get("delivered"))
            self.assertNotIn("pending", store.load_orders()[0].get("alerts_sent") or [])
            with patch("navin.trading.notify.deliver_alert", return_value={"webui": True}):
                again = run_watch(store)
            self.assertEqual(again["count"], 1)
            self.assertTrue(again.get("delivered"))

    def test_heartbeat_skips_watch_when_the_loop_just_cycled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = TradingStore(Path(tmp))
            store.save_orders(
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
            store.save_loop({**store.load_loop(), "last_watch": time.time(), "enabled": True})
            silent = tick_watch(store)
            self.assertEqual(silent["count"], 0)
            self.assertEqual(silent.get("skipped"), "loop_just_watched")

    def test_watch_does_not_overwrite_a_pause(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = TradingStore(Path(tmp))
            store.save_loop({**store.load_loop(), "enabled": True, "phase": "armed"})
            store.save_loop_intent({"enabled": False})
            with patch("navin.trading.notify.deliver_alert", return_value={"webui": True}):
                tick_watch(store)
            self.assertFalse(store.load_loop()["enabled"])
            from navin.trading.loop import peek_loop

            self.assertFalse(peek_loop(store)["enabled"])
            self.assertEqual(peek_loop(store)["phase"], "paused")
            self.assertFalse(store.load_loop_intent())

    def test_heartbeat_snapshot_does_not_fetch_quotes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = TradingStore(Path(tmp))
            store.save_loop({**store.load_loop(), "enabled": True})
            with patch("navin.webui.trading_api._store", return_value=store):
                with request_context(_hb_ctx()):
                    with patch("navin.trading.loop.fetch_quotes") as fetch:
                        snap = handle_trading_action("snapshot")
                        journal = handle_trading_action("journal")
            fetch.assert_not_called()
            self.assertIn("portfolio", snap)
            self.assertEqual(snap["quotes"], {})
            self.assertIn("journal", journal)

    def test_heartbeat_desks_include_trading_note(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = TradingStore(Path(tmp))
            store.save_orders(
                [
                    {
                        "id": "ord-2",
                        "symbol": "AAPL",
                        "side": "buy",
                        "qty": 2,
                        "price": 10,
                        "status": "pending",
                    }
                ]
            )
            with patch("navin.tenders.heartbeat.tick_watch", return_value=None):
                with patch("navin.career.heartbeat.tick_watch", return_value=None):
                    with patch("navin.leads.heartbeat.tick_watch", return_value=None):
                        with patch("navin.marketing.heartbeat.tick_watch", return_value=None):
                            with patch(
                                "navin.trading.notify.deliver_alert",
                                return_value={"webui": True},
                            ):
                                note = tick_heartbeat_desks(trading_store=store)
            self.assertIn("watch.count=1", note)
            self.assertIn("AAPL", note)

    def test_heartbeat_watch_error_does_not_block(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = TradingStore(Path(tmp))
            store.save_loop({**store.load_loop(), "enabled": True})

            def hang(_store, **_kwargs):
                time.sleep(2)
                return {"count": 0, "events": [], "digest": "", "sent": {}}

            started = time.monotonic()
            with (
                patch("navin.trading.heartbeat.HEARTBEAT_WATCH_S", 0.05),
                patch("navin.trading.watch.run_watch", hang),
            ):
                payload = tick_watch(store)
            self.assertLess(time.monotonic() - started, 1.0)
            self.assertEqual(payload["skipped"], "watch_error")
            self.assertEqual(payload["count"], 0)

    def test_heartbeat_md_trading_section_matches_the_template(self) -> None:
        live = (ROOT / ".navin/HEARTBEAT.md").read_text(encoding="utf-8")
        template = (ROOT / "navin/templates/HEARTBEAT.md").read_text(encoding="utf-8")
        marker = "### Trading paper book"
        self.assertIn(marker, live)
        self.assertIn(marker, template)
        live_block = live.split(marker, 1)[1].split("### ", 1)[0]
        template_block = template.split(marker, 1)[1].split("### ", 1)[0]
        self.assertEqual(live_block, template_block)
        self.assertIn("already ran", live_block)
        self.assertIn("Never tick", live_block)
        self.assertNotIn("\u2014", live_block)
        self.assertNotIn("\u2013", live_block)


if __name__ == "__main__":
    unittest.main()
