# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Marketing desk loop: start, stop, schedule, lock, heal, heartbeat stay isolated."""

from __future__ import annotations

import json
import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from unittest.mock import patch

from navin.loop_schedule import LoopScheduleError, SCHEDULE_KINDS
from navin.marketing.errors import MarketingError
from navin.marketing.heartbeat import HEARTBEAT_MARKETING_ACTIONS, tick_watch
from navin.marketing.lock import marketing_desk_lock
from navin.marketing.loop import maybe_tick, peek_loop, start_loop, stop_loop, update_loop_schedule
from navin.marketing.store import MarketingStore, STUCK_PHASE_S, heal_loop_state
from navin.marketing.understand import understand_product
from navin.webui.marketing_desk_api import handle_marketing_action

ROOT = Path(__file__).resolve().parents[1]


def _arm(store: MarketingStore) -> None:
    understand_product(store, extras={"name": "InvoiceAI"})


def _watch_ok(_desk: MarketingStore, **_kwargs: Any) -> dict[str, Any]:
    return {"count": 1, "events": [{"kind": "winner", "text": "linkedin wins"}], "winners": []}


def _improve_ok(_desk: MarketingStore, **_kwargs: Any) -> dict[str, Any]:
    return {"winners": [{"id": "cnt-1"}], "variants": [{"id": "cnt-2"}]}


class _SlowWatch:
    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()
        self.calls = 0

    def __call__(self, store: MarketingStore) -> dict[str, Any]:
        self.calls += 1
        self.entered.set()
        if not self.release.wait(timeout=8):
            raise TimeoutError("watch was not released")
        return _watch_ok(store)


class MarketingLoopControlTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.store = MarketingStore(Path(self.tmp.name))
        _arm(self.store)
        self.now = 1_700_000_000.0
        self.watches = 0
        self.improves = 0

        def watch_fn(_desk: MarketingStore) -> dict[str, Any]:
            self.watches += 1
            return _watch_ok(_desk)

        def improve_fn(_desk: MarketingStore) -> dict[str, Any]:
            self.improves += 1
            return _improve_ok(_desk)

        self.watch_fn = watch_fn
        self.improve_fn = improve_fn

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_unarmed_store_does_not_start(self) -> None:
        empty = MarketingStore(Path(self.tmp.name) / "empty")
        with self.assertRaises(MarketingError):
            start_loop(empty)

    def test_unarmed_tick_stays_unarmed(self) -> None:
        empty = MarketingStore(Path(self.tmp.name) / "empty")
        result = maybe_tick(empty, now=self.now, watch_fn=self.watch_fn)
        self.assertEqual(result["phase"], "unarmed")
        self.assertEqual(self.watches, 0)

    def test_paused_tick_is_a_no_op(self) -> None:
        result = maybe_tick(self.store, force=False, watch_fn=self.watch_fn)
        self.assertFalse(result["did_work"])
        self.assertEqual(result["phase"], "paused")
        self.assertEqual(self.watches, 0)

    def test_start_without_run_now_does_not_cycle(self) -> None:
        started = start_loop(
            self.store,
            schedule={"kind": "daily", "hour": 9, "minute": 0, "tz": "UTC"},
            run_now=False,
            now=self.now,
            watch_fn=self.watch_fn,
            improve_fn=self.improve_fn,
        )
        self.assertFalse(started["did_work"])
        self.assertEqual(started["phase"], "armed")
        self.assertTrue(started["loop"]["enabled"])
        self.assertGreater(float(started["loop"]["next_due"]), self.now)
        self.assertEqual(self.watches, 0)
        slept = maybe_tick(
            self.store,
            now=self.now,
            watch_fn=self.watch_fn,
            improve_fn=self.improve_fn,
        )
        self.assertEqual(slept["phase"], "sleep")
        self.assertEqual(self.watches, 0)

    def test_start_then_due_tick_does_real_work(self) -> None:
        started = start_loop(
            self.store,
            schedule={"kind": "daily", "hour": 9, "minute": 0, "tz": "UTC"},
            run_now=True,
            now=self.now,
            watch_fn=self.watch_fn,
            improve_fn=self.improve_fn,
        )
        self.assertTrue(started["did_work"])
        self.assertEqual(self.watches, 1)
        self.assertEqual(self.improves, 1)
        self.assertIn("winners", started["reason"])

        later = maybe_tick(
            self.store,
            now=self.now + 100,
            watch_fn=self.watch_fn,
            improve_fn=self.improve_fn,
        )
        self.assertEqual(later["phase"], "sleep")
        self.assertEqual(self.watches, 1)

    def test_stop_then_supervisor_does_not_cycle(self) -> None:
        start_loop(
            self.store,
            run_now=False,
            now=self.now,
            watch_fn=self.watch_fn,
            improve_fn=self.improve_fn,
        )
        paused = stop_loop(self.store, now=self.now)
        self.assertFalse(paused["enabled"])
        self.assertEqual(paused["phase"], "paused")
        ticked = maybe_tick(
            self.store,
            now=self.now + 10**7,
            watch_fn=self.watch_fn,
            improve_fn=self.improve_fn,
        )
        self.assertEqual(ticked["phase"], "paused")
        self.assertFalse(ticked["did_work"])
        self.assertEqual(self.watches, 0)

    def test_force_tick_while_paused_stays_paused(self) -> None:
        result = maybe_tick(
            self.store,
            now=self.now,
            force=True,
            watch_fn=self.watch_fn,
            improve_fn=self.improve_fn,
        )
        self.assertTrue(result["did_work"])
        self.assertFalse(result["loop"]["enabled"])
        self.assertEqual(result["loop"]["phase"], "paused")

    def test_stop_and_schedule_update(self) -> None:
        start_loop(self.store, schedule={"kind": "weekdays", "hour": 10, "minute": 0, "tz": "UTC"})
        updated = update_loop_schedule(
            self.store,
            schedule={"kind": "daily", "hour": 8, "minute": 30, "tz": "UTC"},
        )
        self.assertEqual(updated["schedule"]["kind"], "daily")
        stopped = stop_loop(self.store)
        self.assertFalse(stopped["enabled"])
        self.assertEqual(stopped["phase"], "paused")

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

    def test_cycle_error_releases_phase_and_advances_due(self) -> None:
        def boom(_desk: MarketingStore) -> dict[str, Any]:
            raise RuntimeError("source down")

        start_loop(self.store, run_now=False, now=self.now, watch_fn=boom)
        due = float(self.store.load_loop()["next_due"])
        failed = maybe_tick(self.store, now=due + 1, watch_fn=boom, improve_fn=self.improve_fn)
        self.assertFalse(failed["did_work"])
        self.assertEqual(failed["loop"]["skipped_reason"], "error")
        self.assertEqual(failed["loop"]["phase"], "idle")
        self.assertTrue(failed["loop"]["enabled"])
        self.assertGreater(float(failed["loop"]["next_due"]), due)
        stop_loop(self.store, now=due + 2)
        self.assertFalse(peek_loop(self.store)["enabled"])


class MarketingLoopOverlapTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.store = MarketingStore(Path(self.tmp.name))
        _arm(self.store)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_second_tick_is_busy_while_cycle_runs(self) -> None:
        slow = _SlowWatch()
        result: dict[str, object] = {}

        def run() -> None:
            result.update(
                maybe_tick(self.store, force=True, watch_fn=slow, improve_fn=_improve_ok)
            )

        worker = threading.Thread(target=run)
        worker.start()
        self.assertTrue(slow.entered.wait(3))
        busy = maybe_tick(self.store, force=True, watch_fn=_watch_ok, improve_fn=_improve_ok)
        self.assertEqual(busy["phase"], "busy")
        self.assertFalse(busy["did_work"])
        slow.release.set()
        worker.join(5)
        self.assertTrue(result.get("did_work"))
        self.assertEqual(slow.calls, 1)

    def test_stop_during_cycle_stays_paused_after(self) -> None:
        slow = _SlowWatch()
        result: dict[str, object] = {}

        def run() -> None:
            start_loop(
                self.store,
                run_now=True,
                watch_fn=slow,
                improve_fn=_improve_ok,
            )
            result["done"] = True

        worker = threading.Thread(target=run)
        worker.start()
        self.assertTrue(slow.entered.wait(3))
        paused = stop_loop(self.store)
        self.assertFalse(paused["enabled"])
        self.assertEqual(paused["phase"], "paused")
        self.assertFalse(peek_loop(self.store)["enabled"])
        hb = tick_watch(self.store)
        self.assertEqual(hb.get("skipped"), "busy")
        self.assertEqual(hb["count"], 0)
        slow.release.set()
        worker.join(5)
        self.assertTrue(result.get("done"))
        final = peek_loop(self.store)
        self.assertFalse(final["enabled"])
        self.assertEqual(final["phase"], "paused")
        self.assertFalse(self.store.load_loop_intent())

    def test_start_during_force_cycle_keeps_loop_armed(self) -> None:
        slow = _SlowWatch()
        result: dict[str, object] = {}

        def run() -> None:
            result.update(
                maybe_tick(self.store, force=True, watch_fn=slow, improve_fn=_improve_ok)
            )

        worker = threading.Thread(target=run)
        worker.start()
        self.assertTrue(slow.entered.wait(3))
        started = start_loop(self.store, run_now=False, watch_fn=_watch_ok)
        self.assertTrue(started["loop"]["enabled"])
        slow.release.set()
        worker.join(5)
        self.assertTrue(peek_loop(self.store)["enabled"])
        self.assertNotEqual(result.get("loop", {}).get("phase"), "measure")

    def test_schedule_during_cycle_is_kept(self) -> None:
        slow = _SlowWatch()
        worker = threading.Thread(
            target=lambda: maybe_tick(
                self.store, force=True, watch_fn=slow, improve_fn=_improve_ok
            )
        )
        worker.start()
        self.assertTrue(slow.entered.wait(3))
        update_loop_schedule(self.store, schedule={"kind": "weekend", "hour": 11, "minute": 45})
        self.assertEqual(peek_loop(self.store)["schedule"]["kind"], "weekend")
        slow.release.set()
        worker.join(5)
        self.assertEqual(self.store.load_loop()["schedule"]["kind"], "weekend")

    def test_lock_is_released_after_cycle(self) -> None:
        maybe_tick(self.store, force=True, watch_fn=_watch_ok, improve_fn=_improve_ok)
        with marketing_desk_lock(self.store, wait_s=0) as got:
            self.assertTrue(got)


class MarketingLoopHealTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.store = MarketingStore(Path(self.tmp.name))
        _arm(self.store)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_corrupt_next_due_is_repaired(self) -> None:
        self.store.save_loop({**self.store.load_loop(), "enabled": True, "next_due": "soon"})
        loaded = self.store.load_loop()
        self.assertEqual(loaded["next_due"], 0.0)
        ticked = maybe_tick(self.store, now=time.time(), watch_fn=_watch_ok, improve_fn=_improve_ok)
        self.assertTrue(ticked["did_work"])
        self.assertIsInstance(self.store.load_loop()["next_due"], float)

    def test_stuck_measure_phase_heals_after_grace(self) -> None:
        now = time.time()
        stale = now - STUCK_PHASE_S - 5
        self.store.save_loop(
            {
                **self.store.load_loop(),
                "enabled": True,
                "phase": "measure",
                "last_tick": stale,
            }
        )
        path = self.store.path("loop.json")
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw["updated_at"] = stale
        raw["last_tick"] = stale
        raw["phase"] = "measure"
        path.write_text(json.dumps(raw), encoding="utf-8")
        healed = self.store.load_loop()
        self.assertEqual(healed["phase"], "idle")
        self.assertEqual(healed["skipped_reason"], "recovered_stuck_phase")
        self.assertTrue(healed["enabled"])

    def test_live_measure_phase_is_not_healed(self) -> None:
        now = time.time()
        raw = {
            "enabled": True,
            "phase": "measure",
            "last_tick": now - 3600,
            "updated_at": now,
            "next_due": 0.0,
            "last_watch": 0.0,
            "cycle": 1,
        }
        healed = heal_loop_state(raw, now=now)
        self.assertEqual(healed["phase"], "measure")

    def test_stale_measure_without_live_pid_recovers(self) -> None:
        from navin.marketing.loop import peek_loop, recover_stale_cycle

        self.store.save_loop({**self.store.load_loop(), "enabled": True, "phase": "measure"})
        recovered = recover_stale_cycle(self.store)
        self.assertEqual(recovered["phase"], "idle")
        self.assertEqual(recovered["skipped_reason"], "stale_cycle")
        self.assertEqual(peek_loop(self.store)["phase"], "idle")

    def test_paused_stuck_phase_heals_to_paused(self) -> None:
        now = time.time()
        healed = heal_loop_state(
            {
                "enabled": False,
                "phase": "learn",
                "last_tick": now - STUCK_PHASE_S - 1,
                "updated_at": now - STUCK_PHASE_S - 1,
            },
            now=now,
        )
        self.assertEqual(healed["phase"], "paused")
        self.assertEqual(healed["skipped_reason"], "recovered_stuck_phase")


class MarketingLoopApiAndHeartbeatTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.store = MarketingStore(Path(self.tmp.name))
        _arm(self.store)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_api_start_stop_schedule_tick(self) -> None:
        with patch("navin.webui.marketing_desk_api._store", return_value=self.store):
            started = handle_marketing_action(
                "start",
                {"schedule": {"kind": "daily", "hour": 7, "minute": 0}, "run_now": False},
            )
            self.assertTrue(started["loop"]["enabled"])
            self.assertEqual(started["loop"]["schedule"]["hour"], 7)
            changed = handle_marketing_action(
                "schedule",
                {"schedule": {"kind": "weekly", "hour": 10, "minute": 0, "weekday": 1}},
            )
            self.assertEqual(changed["loop"]["schedule"]["kind"], "weekly")
            with patch("navin.marketing.loop.run_watch", _watch_ok):
                with patch("navin.marketing.loop.run_growth_cycle", _improve_ok):
                    ticked = handle_marketing_action("tick", {"force": True})
            self.assertTrue(ticked["loop_tick"]["did_work"])
            stopped = handle_marketing_action("stop")
            self.assertFalse(stopped["loop"]["enabled"])

    def test_api_tick_default_force_cycles_while_paused(self) -> None:
        with patch("navin.webui.marketing_desk_api._store", return_value=self.store):
            with patch("navin.marketing.loop.run_watch", _watch_ok):
                with patch("navin.marketing.loop.run_growth_cycle", _improve_ok):
                    ticked = handle_marketing_action("tick", {})
        self.assertTrue(ticked["loop_tick"]["did_work"])
        self.assertFalse(ticked["loop"]["enabled"])

    def test_heartbeat_refuses_start_stop_schedule_tick(self) -> None:
        self.assertEqual(HEARTBEAT_MARKETING_ACTIONS, {"status", "snapshot", "watch"})
        from navin.agent.tools.context import RequestContext, request_context

        ctx = RequestContext(
            channel="telegram",
            chat_id="1",
            session_key="heartbeat",
            metadata={"heartbeat": True},
        )
        with patch("navin.webui.marketing_desk_api._store", return_value=self.store):
            with request_context(ctx):
                for action in ("start", "stop", "schedule", "tick", "pipeline", "understand"):
                    with self.assertRaises(MarketingError, msg=action):
                        handle_marketing_action(action, {"schedule": {"kind": "daily", "hour": 9}})

    def test_heartbeat_never_improves(self) -> None:
        with patch("navin.marketing.growth.run_growth_cycle") as improve:
            payload = tick_watch(self.store)
        improve.assert_not_called()
        self.assertIsNotNone(payload)
        self.assertEqual(payload["count"], 0)

    def test_heartbeat_skips_after_loop_watch(self) -> None:
        maybe_tick(
            self.store,
            force=True,
            now=time.time(),
            watch_fn=_watch_ok,
            improve_fn=_improve_ok,
        )
        silent = tick_watch(self.store)
        self.assertEqual(silent.get("skipped"), "loop_just_watched")
        self.assertEqual(silent["count"], 0)

    def test_snapshot_shows_pause_intent(self) -> None:
        start_loop(self.store, run_now=False, watch_fn=_watch_ok)
        self.store.save_loop_intent({"enabled": False})
        with patch("navin.webui.marketing_desk_api._store", return_value=self.store):
            snap = handle_marketing_action("snapshot")
        self.assertFalse(snap["loop"]["enabled"])
        self.assertEqual(snap["loop"]["phase"], "paused")

    def test_supervisor_is_wired_and_does_not_stack(self) -> None:
        src = (ROOT / "navin/cli/commands.py").read_text(encoding="utf-8")
        self.assertIn("navin-marketing-loop", src)
        self.assertIn("marketing_tick_inflight", src)
        self.assertIn("marketing_maybe_tick", src)
        self.assertIn("from navin.marketing.loop import maybe_tick", src)
        desk = (ROOT / "webui/src/components/studio/marketing/MarketingWorkspace.tsx").read_text(
            encoding="utf-8"
        )
        self.assertIn("marketing-start-loop", desk)
        self.assertIn("marketing-pause-loop", desk)
        self.assertIn("marketing-tick-loop", desk)
        self.assertIn("marketing-ship-social", desk)
        self.assertIn('act in {"ship", "social-pack"}', (ROOT / "navin/webui/marketing_desk_api.py").read_text(encoding="utf-8"))

    def test_heartbeat_md_says_loop_is_not_heartbeat(self) -> None:
        live = (ROOT / ".navin/HEARTBEAT.md").read_text(encoding="utf-8")
        template = (ROOT / "navin/templates/HEARTBEAT.md").read_text(encoding="utf-8")
        for body in (live, template):
            self.assertIn("marketing action=watch", body)
            self.assertIn("Studio Start loop", body)
            self.assertIn("Never publish", body)
            self.assertNotIn("\u2014", body)
            self.assertNotIn("\u2013", body)


if __name__ == "__main__":
    unittest.main()
