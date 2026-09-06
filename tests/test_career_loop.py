"""Career desk loop: start, stop, schedule, tick, lock, heartbeat stay isolated."""

from __future__ import annotations

import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from navin.career.errors import CareerError
from navin.career.heartbeat import HEARTBEAT_CAREER_ACTIONS, tick_watch
from navin.career.lock import career_desk_lock
from navin.career.loop import (
    hunt_is_live,
    maybe_tick,
    peek_loop,
    recover_stale_hunt,
    start_loop,
    stop_loop,
    update_loop_schedule,
)
from navin.career.watch import run_watch
from navin.career.store import CareerStore
from navin.loop_schedule import LoopScheduleError, SCHEDULE_KINDS
from navin.webui.career_api import handle_career_action

ROOT = Path(__file__).resolve().parents[1]


def _arm(store: CareerStore) -> None:
    store.save_profile({"titles": ["Data Engineer"], "wizard_complete": True, "track": "freelance"})


def _collect_ok(store: CareerStore, **kwargs: object) -> dict[str, object]:
    return {"added": 2, "scanned": 7, "count": 7}


def _watch_ok(store: CareerStore) -> dict[str, object]:
    return {"count": 1, "delivered": True, "digest": "Match: Staff", "sent": {"webui": True}}


def _watch_fail(store: CareerStore) -> dict[str, object]:
    return {"count": 1, "delivered": False, "digest": "Match: Staff", "sent": {}}


class _SlowCollect:
    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()
        self.calls = 0

    def __call__(self, store: CareerStore, **kwargs: object) -> dict[str, object]:
        self.calls += 1
        self.entered.set()
        if not self.release.wait(timeout=8):
            raise TimeoutError("collect was not released")
        return {"added": 1, "scanned": 3}


class CareerLoopControlTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.store = CareerStore(Path(self.tmp.name))
        _arm(self.store)
        self.now = 1_700_000_000.0

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_start_without_run_now_does_not_hunt(self) -> None:
        calls = {"n": 0}

        def collect(store: CareerStore, **kwargs: object) -> dict[str, object]:
            calls["n"] += 1
            return {"added": 9, "scanned": 9}

        started = start_loop(
            self.store,
            schedule={"kind": "daily", "hour": 9, "minute": 0},
            run_now=False,
            now=self.now,
            collect_fn=collect,
            watch_fn=_watch_ok,
        )
        self.assertFalse(started["did_work"])
        self.assertEqual(started["phase"], "armed")
        self.assertTrue(started["loop"]["enabled"])
        self.assertGreater(float(started["loop"]["next_due"]), self.now)
        self.assertEqual(calls["n"], 0)
        slept = maybe_tick(
            self.store,
            now=self.now,
            collect_fn=collect,
            watch_fn=_watch_ok,
        )
        self.assertEqual(slept["phase"], "sleep")
        self.assertEqual(calls["n"], 0)

    def test_unarmed_start_is_refused(self) -> None:
        empty = CareerStore(Path(self.tmp.name) / "empty")
        with self.assertRaises(CareerError) as ctx:
            start_loop(empty, run_now=False, now=self.now, collect_fn=_collect_ok)
        self.assertEqual(ctx.exception.status, 400)

    def test_unarmed_tick_stays_unarmed(self) -> None:
        empty = CareerStore(Path(self.tmp.name) / "empty")
        result = maybe_tick(empty, now=self.now, collect_fn=_collect_ok)
        self.assertEqual(result["phase"], "unarmed")

    def test_stop_then_supervisor_does_not_hunt(self) -> None:
        start_loop(self.store, run_now=False, now=self.now, collect_fn=_collect_ok)
        paused = stop_loop(self.store, now=self.now)
        self.assertFalse(paused["enabled"])
        self.assertEqual(paused["phase"], "paused")
        ticked = maybe_tick(self.store, now=self.now + 10**7, collect_fn=_collect_ok, watch_fn=_watch_ok)
        self.assertEqual(ticked["phase"], "paused")
        self.assertFalse(ticked["did_work"])

    def test_force_tick_while_paused_stays_paused(self) -> None:
        result = maybe_tick(
            self.store,
            now=self.now,
            force=True,
            collect_fn=_collect_ok,
            watch_fn=_watch_ok,
        )
        self.assertTrue(result["did_work"])
        self.assertFalse(result["loop"]["enabled"])
        self.assertEqual(result["loop"]["phase"], "paused")
        self.assertEqual(result["loop"]["added"], 2)
        self.assertEqual(result["loop"]["alerts"], 1)

    def test_due_tick_hunts_then_sleeps(self) -> None:
        start_loop(self.store, run_now=False, now=self.now, collect_fn=_collect_ok, watch_fn=_watch_ok)
        state = self.store.load_loop()
        due = float(state["next_due"])
        first = maybe_tick(self.store, now=due + 1, collect_fn=_collect_ok, watch_fn=_watch_ok)
        self.assertTrue(first["did_work"])
        self.assertTrue(first["loop"]["enabled"])
        self.assertEqual(first["loop"]["cycle"], 1)
        second = maybe_tick(self.store, now=due + 2, collect_fn=_collect_ok, watch_fn=_watch_ok)
        self.assertEqual(second["phase"], "sleep")

    def test_schedule_kinds_set_a_future_slot(self) -> None:
        for kind in SCHEDULE_KINDS:
            with self.subTest(kind=kind):
                raw = {"kind": kind, "hour": 9, "minute": 30, "weekday": 3, "day": 12}
                started = start_loop(self.store, schedule=raw, run_now=False, now=self.now)
                self.assertGreater(float(started["loop"]["next_due"]), self.now, kind)
                self.assertEqual(started["loop"]["schedule"]["kind"], kind)
                self.assertNotIn("\u2014", started["loop"]["last_result"])
                self.assertNotIn("\u2013", started["loop"]["last_result"])

    def test_update_schedule_while_paused_does_not_enable(self) -> None:
        update_loop_schedule(
            self.store,
            schedule={"kind": "weekdays", "hour": 8, "minute": 15},
            now=self.now,
        )
        state = peek_loop(self.store, now=self.now)
        self.assertFalse(state["enabled"])
        self.assertEqual(state["schedule"]["kind"], "weekdays")
        self.assertEqual(state["schedule"]["hour"], 8)

    def test_invalid_schedule_is_refused(self) -> None:
        with self.assertRaises(LoopScheduleError):
            start_loop(
                self.store,
                schedule={"kind": "whenever", "hour": 9},
                run_now=False,
                now=self.now,
            )

    def test_hunt_error_releases_phase_and_advances_due(self) -> None:
        def boom(store: CareerStore, **kwargs: object) -> dict[str, object]:
            raise RuntimeError("source down")

        start_loop(self.store, run_now=False, now=self.now, collect_fn=boom)
        due = float(self.store.load_loop()["next_due"])
        failed = maybe_tick(self.store, now=due + 1, collect_fn=boom, watch_fn=_watch_ok)
        self.assertFalse(failed["did_work"])
        self.assertEqual(failed["loop"]["skipped_reason"], "error")
        self.assertEqual(failed["loop"]["phase"], "idle")
        self.assertTrue(failed["loop"]["enabled"])
        self.assertGreater(float(failed["loop"]["next_due"]), due)
        self.assertEqual(failed["loop"]["error_streak"], 1)
        self.assertLess(float(failed["loop"]["next_due"]), due + 200)
        stop_loop(self.store, now=due + 2)
        self.assertFalse(peek_loop(self.store)["enabled"])

    def test_failed_watch_does_not_set_last_watch(self) -> None:
        maybe_tick(self.store, force=True, now=self.now, collect_fn=_collect_ok, watch_fn=_watch_fail)
        self.assertFalse(float(self.store.load_loop().get("last_watch") or 0))

    def test_stale_hunt_recovers_to_idle(self) -> None:
        self.store.save_loop(
            {
                **self.store.load_loop(),
                "enabled": True,
                "phase": "hunt",
                "hunt_pid": 2_147_000_000,
            }
        )
        state = peek_loop(self.store)
        self.assertEqual(state["phase"], "idle")
        self.assertTrue(state["enabled"])
        self.assertEqual(self.store.load_loop()["skipped_reason"], "stale_hunt")
        self.assertNotIn("hunt_pid", self.store.load_loop())
        self.assertFalse(hunt_is_live(self.store, self.store.load_loop()))

    def test_stale_hunt_while_paused_recovers_to_paused(self) -> None:
        self.store.save_loop(
            {
                **self.store.load_loop(),
                "enabled": False,
                "phase": "hunt",
                "hunt_pid": 2_147_000_000,
            }
        )
        recovered = recover_stale_hunt(self.store)
        self.assertEqual(recovered["phase"], "paused")
        self.assertFalse(recovered["enabled"])
        self.assertEqual(peek_loop(self.store)["phase"], "paused")

    def test_watch_returns_busy_without_waiting(self) -> None:
        with career_desk_lock(self.store, wait_s=0) as got:
            self.assertTrue(got)
            started = time.monotonic()
            payload = run_watch(self.store)
            self.assertLess(time.monotonic() - started, 0.4)
        self.assertEqual(payload.get("skipped"), "busy")
        self.assertEqual(payload["count"], 0)

    def test_leftover_stop_intent_is_persisted(self) -> None:
        start_loop(self.store, run_now=False, now=self.now, collect_fn=_collect_ok)
        self.store.save_loop_intent({"enabled": False})
        state = peek_loop(self.store)
        self.assertFalse(state["enabled"])
        self.assertEqual(state["phase"], "paused")
        self.assertFalse(self.store.load_loop()["enabled"])
        self.assertFalse(self.store.load_loop_intent())

    def test_expired_hunt_recovers_even_if_marked_live(self) -> None:
        from navin.career.loop import MAX_HUNT_S, mark_loop_hunting

        mark_loop_hunting(self.store, True)
        try:
            self.store.save_loop(
                {
                    **self.store.load_loop(),
                    "enabled": True,
                    "phase": "hunt",
                    "hunt_pid": 1,
                    "hunt_started_at": time.time() - MAX_HUNT_S - 5,
                }
            )
            state = peek_loop(self.store)
            self.assertEqual(state["phase"], "idle")
            self.assertEqual(self.store.load_loop()["skipped_reason"], "stale_hunt")
        finally:
            mark_loop_hunting(self.store, False)


class CareerLoopOverlapTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.store = CareerStore(Path(self.tmp.name))
        _arm(self.store)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_second_tick_is_busy_while_hunt_runs(self) -> None:
        slow = _SlowCollect()
        result: dict[str, object] = {}

        def run() -> None:
            result.update(
                maybe_tick(self.store, force=True, collect_fn=slow, watch_fn=_watch_ok)
            )

        worker = threading.Thread(target=run)
        worker.start()
        self.assertTrue(slow.entered.wait(3))
        busy = maybe_tick(self.store, force=True, collect_fn=_collect_ok, watch_fn=_watch_ok)
        self.assertEqual(busy["phase"], "busy")
        self.assertFalse(busy["did_work"])
        slow.release.set()
        worker.join(5)
        self.assertTrue(result.get("did_work"))
        self.assertEqual(slow.calls, 1)

    def test_live_hunt_is_not_recovered(self) -> None:
        slow = _SlowCollect()
        worker = threading.Thread(
            target=lambda: maybe_tick(self.store, force=True, collect_fn=slow, watch_fn=_watch_ok)
        )
        worker.start()
        self.assertTrue(slow.entered.wait(3))
        raw = self.store.load_loop()
        self.assertEqual(raw["phase"], "hunt")
        self.assertTrue(hunt_is_live(self.store, raw))
        self.assertEqual(peek_loop(self.store)["phase"], "hunt")
        self.assertEqual(self.store.load_loop()["phase"], "hunt")
        self.assertNotEqual(self.store.load_loop().get("skipped_reason"), "stale_hunt")
        hb = tick_watch(self.store)
        self.assertEqual(hb.get("skipped"), "loop_hunting")
        self.assertEqual(hb["count"], 0)
        slow.release.set()
        worker.join(5)

    def test_stop_during_hunt_stays_paused_after(self) -> None:
        slow = _SlowCollect()
        result: dict[str, object] = {}

        def run() -> None:
            start_loop(
                self.store,
                run_now=True,
                collect_fn=slow,
                watch_fn=_watch_ok,
            )
            result["done"] = True

        worker = threading.Thread(target=run)
        worker.start()
        self.assertTrue(slow.entered.wait(3))
        paused = stop_loop(self.store)
        self.assertFalse(paused["enabled"])
        self.assertEqual(paused["phase"], "paused")
        snap = peek_loop(self.store)
        self.assertFalse(snap["enabled"])
        hb = tick_watch(self.store)
        self.assertEqual(hb.get("skipped"), "loop_hunting")
        self.assertEqual(hb["count"], 0)
        slow.release.set()
        worker.join(5)
        self.assertTrue(result.get("done"))
        final = peek_loop(self.store)
        self.assertFalse(final["enabled"])
        self.assertEqual(final["phase"], "paused")
        self.assertFalse(self.store.load_loop_intent())

    def test_start_during_force_hunt_keeps_loop_armed(self) -> None:
        slow = _SlowCollect()
        result: dict[str, object] = {}

        def run() -> None:
            result.update(
                maybe_tick(self.store, force=True, collect_fn=slow, watch_fn=_watch_ok)
            )

        worker = threading.Thread(target=run)
        worker.start()
        self.assertTrue(slow.entered.wait(3))
        started = start_loop(self.store, run_now=False, collect_fn=_collect_ok)
        self.assertTrue(started["loop"]["enabled"])
        slow.release.set()
        worker.join(5)
        self.assertTrue(peek_loop(self.store)["enabled"])
        self.assertNotEqual(result.get("loop", {}).get("phase"), "hunt")

    def test_schedule_during_hunt_is_kept(self) -> None:
        slow = _SlowCollect()
        worker = threading.Thread(
            target=lambda: maybe_tick(self.store, force=True, collect_fn=slow, watch_fn=_watch_ok)
        )
        worker.start()
        self.assertTrue(slow.entered.wait(3))
        update_loop_schedule(self.store, schedule={"kind": "weekend", "hour": 11, "minute": 45})
        self.assertEqual(peek_loop(self.store)["schedule"]["kind"], "weekend")
        slow.release.set()
        worker.join(5)
        self.assertEqual(self.store.load_loop()["schedule"]["kind"], "weekend")

    def test_lock_is_released_after_hunt(self) -> None:
        maybe_tick(self.store, force=True, collect_fn=_collect_ok, watch_fn=_watch_ok)
        with career_desk_lock(self.store, wait_s=0) as got:
            self.assertTrue(got)


class CareerLoopApiAndHeartbeatTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.store = CareerStore(Path(self.tmp.name))
        _arm(self.store)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_api_start_stop_schedule_tick(self) -> None:
        with patch("navin.webui.career_api._store", return_value=self.store):
            with patch("navin.career.collect.collect", _collect_ok):
                started = handle_career_action(
                    "start",
                    {"schedule": {"kind": "daily", "hour": 7, "minute": 0}, "run_now": False},
                )
                self.assertTrue(started["loop"]["enabled"])
                self.assertEqual(started["loop"]["schedule"]["hour"], 7)
                changed = handle_career_action(
                    "schedule",
                    {"schedule": {"kind": "weekly", "hour": 10, "minute": 0, "weekday": 1}},
                )
                self.assertEqual(changed["loop"]["schedule"]["kind"], "weekly")
                ticked = handle_career_action("tick", {"force": True})
                self.assertTrue(ticked["loop_tick"]["did_work"])
                stopped = handle_career_action("stop")
                self.assertFalse(stopped["loop"]["enabled"])

    def test_api_tick_default_force_hunts_while_paused(self) -> None:
        with patch("navin.webui.career_api._store", return_value=self.store):
            with patch("navin.career.collect.collect", _collect_ok):
                with patch("navin.career.watch.run_watch", return_value={"count": 0}):
                    ticked = handle_career_action("tick", {})
        self.assertTrue(ticked["loop_tick"]["did_work"])
        self.assertFalse(ticked["loop"]["enabled"])

    def test_heartbeat_refuses_start_stop_schedule_tick(self) -> None:
        self.assertEqual(
            HEARTBEAT_CAREER_ACTIONS,
            {"status", "dossier", "snapshot", "read", "book", "watch"},
        )
        from navin.agent.tools.context import RequestContext, request_context

        ctx = RequestContext(
            channel="telegram",
            chat_id="1",
            session_key="heartbeat",
            metadata={"heartbeat": True},
        )
        with patch("navin.webui.career_api._store", return_value=self.store):
            with request_context(ctx):
                for action in ("start", "stop", "schedule", "tick", "search", "collect"):
                    with self.assertRaises(CareerError, msg=action):
                        handle_career_action(action, {"schedule": {"kind": "daily", "hour": 9}})

    def test_heartbeat_never_collects(self) -> None:
        with patch("navin.career.collect.collect") as collect:
            payload = tick_watch(self.store)
        collect.assert_not_called()
        self.assertIsNotNone(payload)
        self.assertEqual(payload["count"], 0)

    def test_heartbeat_recovers_stale_hunt_then_watches(self) -> None:
        self.store.save_loop(
            {
                **self.store.load_loop(),
                "enabled": True,
                "phase": "hunt",
                "hunt_pid": 2_147_000_000,
            }
        )
        payload = tick_watch(self.store)
        self.assertIsNotNone(payload)
        self.assertNotEqual(self.store.load_loop()["phase"], "hunt")
        self.assertEqual(payload.get("skipped"), None)

    def test_heartbeat_skips_after_loop_watch(self) -> None:
        maybe_tick(self.store, force=True, now=time.time(), collect_fn=_collect_ok, watch_fn=_watch_ok)
        silent = tick_watch(self.store)
        self.assertEqual(silent.get("skipped"), "loop_just_watched")
        self.assertEqual(silent["count"], 0)

    def test_heartbeat_retries_when_loop_watch_was_not_delivered(self) -> None:
        maybe_tick(self.store, force=True, now=time.time(), collect_fn=_collect_ok, watch_fn=_watch_fail)
        self.store.upsert_opportunities(
            [
                {
                    "id": "job-hot",
                    "title": "Staff Data Engineer",
                    "match_score": 92,
                    "stage": "matched",
                }
            ]
        )
        with patch("navin.career.notify.deliver_alert", return_value={"webui": True}):
            again = tick_watch(self.store)
        self.assertEqual(again["count"], 1)

    def test_snapshot_shows_pause_intent(self) -> None:
        start_loop(self.store, run_now=False, collect_fn=_collect_ok)
        self.store.save_loop_intent({"enabled": False})
        with patch("navin.webui.career_api._store", return_value=self.store):
            snap = handle_career_action("snapshot")
        self.assertFalse(snap["loop"]["enabled"])
        self.assertEqual(snap["loop"]["phase"], "paused")

    def test_supervisor_is_wired_and_does_not_stack(self) -> None:
        src = (ROOT / "navin/cli/commands.py").read_text(encoding="utf-8")
        self.assertIn("navin-career-loop", src)
        self.assertIn("career_tick_inflight", src)
        self.assertIn("career_maybe_tick", src)
        self.assertIn("from navin.career.loop import maybe_tick", src)

    def test_heartbeat_md_says_loop_is_not_heartbeat(self) -> None:
        live = (ROOT / ".navin/HEARTBEAT.md").read_text(encoding="utf-8")
        template = (ROOT / "navin/templates/HEARTBEAT.md").read_text(encoding="utf-8")
        for body in (live, template):
            self.assertIn("career action=watch", body)
            self.assertIn("Studio Start loop", body)
            self.assertIn("Never search", body)
            self.assertNotIn("\u2014", body)
            self.assertNotIn("\u2013", body)


if __name__ == "__main__":
    unittest.main()
