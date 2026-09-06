"""Trading loop schedules fire on the clock, not from the last tick."""

from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from croniter import croniter

from navin.trading.loop import maybe_tick, start_loop, stop_loop, update_loop_schedule
from navin.trading.schedule import (
    LoopScheduleError,
    describe_schedule,
    next_due_ts,
    normalize_schedule,
    schedule_to_expr,
)
from navin.trading.store import TradingStore


def _utc(year: int, month: int, day: int, hour: int, minute: int = 0) -> float:
    return datetime(year, month, day, hour, minute, tzinfo=UTC).timestamp()


class TradingScheduleMathTest(unittest.TestCase):
    def test_presets_compile_to_valid_cron(self) -> None:
        cases = {
            "daily": "0 9 * * *",
            "weekdays": "0 9 * * 1-5",
            "weekend": "0 9 * * 0,6",
            "weekly": "0 9 * * 1",
            "monthly": "0 9 1 * *",
        }
        for kind, expr in cases.items():
            row = normalize_schedule({"kind": kind, "hour": 9, "minute": 0, "weekday": 1, "day": 1})
            self.assertEqual(row["expr"], expr)
            self.assertTrue(croniter.is_valid(expr), expr)

    def test_weekly_sunday_is_cron_zero(self) -> None:
        self.assertEqual(
            schedule_to_expr(normalize_schedule({"kind": "weekly", "weekday": 7, "hour": 8})),
            "0 8 * * 0",
        )

    def test_daily_waits_until_the_chosen_hour(self) -> None:
        now = _utc(2026, 8, 30, 10, 0)
        due = next_due_ts({"kind": "daily", "hour": 9, "minute": 0, "tz": "UTC"}, now)
        self.assertEqual(datetime.fromtimestamp(due, UTC), datetime(2026, 8, 31, 9, 0, tzinfo=UTC))

    def test_weekdays_skip_the_weekend(self) -> None:
        friday_afternoon = _utc(2026, 8, 28, 10, 0)
        due = next_due_ts({"kind": "weekdays", "hour": 9, "minute": 0, "tz": "UTC"}, friday_afternoon)
        self.assertEqual(datetime.fromtimestamp(due, UTC), datetime(2026, 8, 31, 9, 0, tzinfo=UTC))

    def test_weekend_fires_saturday_then_sunday(self) -> None:
        friday = _utc(2026, 8, 28, 10, 0)
        saturday = next_due_ts({"kind": "weekend", "hour": 9, "minute": 0, "tz": "UTC"}, friday)
        self.assertEqual(datetime.fromtimestamp(saturday, UTC), datetime(2026, 8, 29, 9, 0, tzinfo=UTC))
        sunday = next_due_ts({"kind": "weekend", "hour": 9, "minute": 0, "tz": "UTC"}, saturday)
        self.assertEqual(datetime.fromtimestamp(sunday, UTC), datetime(2026, 8, 30, 9, 0, tzinfo=UTC))

    def test_monthly_moves_to_the_next_month(self) -> None:
        now = _utc(2026, 8, 30, 12, 0)
        due = next_due_ts({"kind": "monthly", "day": 1, "hour": 9, "minute": 0, "tz": "UTC"}, now)
        self.assertEqual(datetime.fromtimestamp(due, UTC), datetime(2026, 9, 1, 9, 0, tzinfo=UTC))

    def test_unknown_kind_is_rejected(self) -> None:
        with self.assertRaises(LoopScheduleError):
            normalize_schedule({"kind": "whenever", "hour": 9})

    def test_describe_keeps_one_sentence(self) -> None:
        self.assertEqual(
            describe_schedule({"kind": "weekend", "hour": 10, "minute": 30}),
            "weekends at 10:30",
        )


class TradingScheduleLoopTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = TradingStore(Path(self.tmp.name))
        self.calls = {"n": 0}

        def http_get(_url: str) -> bytes:
            self.calls["n"] += 1
            return b"{}"

        self.http_get = http_get

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_start_arms_without_a_cycle(self) -> None:
        now = _utc(2026, 8, 30, 10, 0)
        result = start_loop(
            self.store,
            schedule={"kind": "daily", "hour": 9, "minute": 0, "tz": "UTC"},
            run_now=False,
            now=now,
            http_get=self.http_get,
        )
        state = self.store.load_loop()
        self.assertTrue(state["enabled"])
        self.assertEqual(state["schedule"]["kind"], "daily")
        self.assertGreater(state["next_due"], now)
        self.assertFalse(result["did_work"])
        self.assertEqual(self.calls["n"], 0)
        self.assertEqual(maybe_tick(self.store, now=now, http_get=self.http_get)["phase"], "sleep")

    def test_stop_keeps_the_schedule(self) -> None:
        start_loop(
            self.store,
            schedule={"kind": "weekend", "hour": 11, "minute": 0, "tz": "UTC"},
            now=_utc(2026, 8, 30, 8, 0),
        )
        stop_loop(self.store)
        state = self.store.load_loop()
        self.assertFalse(state["enabled"])
        self.assertEqual(state["schedule"]["kind"], "weekend")
        self.assertEqual(state["schedule"]["hour"], 11)

    def test_update_changes_the_next_slot(self) -> None:
        now = _utc(2026, 8, 30, 8, 0)
        start_loop(
            self.store,
            schedule={"kind": "daily", "hour": 9, "minute": 0, "tz": "UTC"},
            now=now,
        )
        first = self.store.load_loop()["next_due"]
        update_loop_schedule(
            self.store,
            schedule={"kind": "monthly", "day": 1, "hour": 9, "minute": 0, "tz": "UTC"},
            now=now,
        )
        second = self.store.load_loop()
        self.assertEqual(second["schedule"]["kind"], "monthly")
        self.assertNotEqual(second["next_due"], first)
        self.assertTrue(second["enabled"])

    def test_paused_loop_still_does_nothing(self) -> None:
        result = maybe_tick(self.store, now=_utc(2026, 8, 30, 9, 0), http_get=self.http_get)
        self.assertFalse(result["did_work"])
        self.assertEqual(result["phase"], "paused")
        self.assertEqual(self.calls["n"], 0)


class TradingScheduleApiTest(unittest.TestCase):
    def test_start_and_schedule_actions_accept_a_clock(self) -> None:
        from navin.trading.errors import TradingError
        from navin.webui import trading_api

        tmp = tempfile.TemporaryDirectory()
        store = TradingStore(Path(tmp.name))
        original = trading_api._store
        trading_api._store = lambda: store
        try:
            started = trading_api.start_payload(
                {
                    "schedule": {"kind": "weekdays", "hour": 7, "minute": 30, "tz": "UTC"},
                    "tz": "UTC",
                    "run_now": False,
                }
            )
            self.assertFalse(started["did_work"])
            self.assertEqual(store.load_loop()["schedule"]["kind"], "weekdays")
            saved = trading_api.schedule_payload(
                {"schedule": {"kind": "weekend", "hour": 8, "minute": 0, "tz": "UTC"}, "tz": "UTC"}
            )
            self.assertEqual(saved["loop"]["schedule"]["kind"], "weekend")
            with self.assertRaises(TradingError):
                trading_api.handle_trading_action(
                    "schedule",
                    {"schedule": {"kind": "never", "hour": 9}},
                )
        finally:
            trading_api._store = original
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
