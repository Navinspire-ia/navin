# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Leads desk loop: start, stop, schedule, tick, lock, heartbeat stay isolated.

Same contract as Career / Tenders / Trading. Zero holes.
"""

from __future__ import annotations

import json
import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from navin.leads.errors import LeadsError
from navin.leads.heartbeat import HEARTBEAT_LEADS_ACTIONS, heartbeat_prompt_note, profile_is_armed, tick_watch
from navin.leads.lock import leads_desk_lock
from navin.leads.loop import (
    MAX_HUNT_S,
    hunt_expired,
    hunt_is_live,
    mark_loop_hunting,
    maybe_tick,
    peek_loop,
    recover_stale_hunt,
    start_loop,
    stop_loop,
    update_loop_schedule,
)
from navin.leads.store import LeadsStore
from navin.leads.watch import run_watch
from navin.loop_schedule import SCHEDULE_KINDS, LoopScheduleError
from navin.webui.leads_api import handle_leads_action

ROOT = Path(__file__).resolve().parents[1]


def _arm(store: LeadsStore) -> None:
    store.save_profile(
        {
            "icp_name": "SaaS France",
            "sector": "SaaS",
            "countries": ["FR"],
            "wizard_ready": True,
        }
    )


def _collect_ok(store: LeadsStore, **kwargs: object) -> dict[str, object]:
    return {"added": 3, "found": 5, "kept": 5, "scanned": 5}


def _watch_ok(store: LeadsStore) -> dict[str, object]:
    return {"count": 1, "digest": "Lead: Acme", "sent": {"webui": True}}


def _watch_boom(store: LeadsStore) -> dict[str, object]:
    raise RuntimeError("telegram down")


class _SlowCollect:
    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()
        self.calls = 0

    def __call__(self, store: LeadsStore, **kwargs: object) -> dict[str, object]:
        self.calls += 1
        self.entered.set()
        if not self.release.wait(timeout=8):
            raise TimeoutError("collect was not released")
        return {"added": 1, "found": 3, "scanned": 3}


class LeadsLoopControlTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.store = LeadsStore(Path(self.tmp.name))
        _arm(self.store)
        self.now = 1_700_000_000.0

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_start_without_run_now_does_not_hunt(self) -> None:
        calls = {"n": 0}

        def collect(store: LeadsStore, **kwargs: object) -> dict[str, object]:
            calls["n"] += 1
            return {"added": 9, "found": 9, "scanned": 9}

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
        slept = maybe_tick(self.store, now=self.now, collect_fn=collect, watch_fn=_watch_ok)
        self.assertEqual(slept["phase"], "sleep")
        self.assertEqual(calls["n"], 0)

    def test_unarmed_start_is_refused(self) -> None:
        empty = LeadsStore(Path(self.tmp.name) / "empty")
        with self.assertRaises(LeadsError) as ctx:
            start_loop(empty, run_now=False, now=self.now, collect_fn=_collect_ok)
        self.assertEqual(ctx.exception.status, 400)

    def test_unarmed_tick_stays_unarmed(self) -> None:
        empty = LeadsStore(Path(self.tmp.name) / "empty")
        result = maybe_tick(empty, now=self.now, collect_fn=_collect_ok)
        self.assertEqual(result["phase"], "unarmed")

    def test_stop_then_supervisor_does_not_hunt(self) -> None:
        start_loop(self.store, run_now=False, now=self.now, collect_fn=_collect_ok)
        paused = stop_loop(self.store, now=self.now)
        self.assertFalse(paused["enabled"])
        self.assertEqual(paused["phase"], "paused")
        ticked = maybe_tick(
            self.store,
            now=self.now + 10**7,
            collect_fn=_collect_ok,
            watch_fn=_watch_ok,
        )
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
        self.assertEqual(result["loop"]["added"], 3)
        self.assertEqual(result["loop"]["alerts"], 1)

    def test_due_tick_hunts_then_sleeps(self) -> None:
        start_loop(self.store, run_now=False, now=self.now, collect_fn=_collect_ok, watch_fn=_watch_ok)
        due = float(self.store.load_loop()["next_due"])
        first = maybe_tick(self.store, now=due + 1, collect_fn=_collect_ok, watch_fn=_watch_ok)
        self.assertTrue(first["did_work"])
        self.assertTrue(first["loop"]["enabled"])
        self.assertEqual(first["loop"]["cycle"], 1)
        self.assertEqual(first["loop"]["added"], 3)
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

    def test_update_schedule_while_paused_does_not_arm(self) -> None:
        start_loop(self.store, run_now=False, now=self.now)
        stop_loop(self.store, now=self.now)
        updated = update_loop_schedule(
            self.store,
            schedule={"kind": "weekdays", "hour": 8, "minute": 15},
            now=self.now,
        )
        self.assertFalse(updated["enabled"])
        self.assertEqual(updated["schedule"]["kind"], "weekdays")
        self.assertEqual(updated["schedule"]["hour"], 8)

    def test_invalid_schedule_is_refused(self) -> None:
        with self.assertRaises(LoopScheduleError):
            start_loop(
                self.store,
                schedule={"kind": "whenever", "hour": 9},
                run_now=False,
                now=self.now,
            )

    def test_hunt_error_releases_phase_and_advances_due(self) -> None:
        def boom(store: LeadsStore, **kwargs: object) -> dict[str, object]:
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
        maybe_tick(self.store, force=True, now=self.now, collect_fn=_collect_ok, watch_fn=_watch_boom)
        self.assertFalse(float(self.store.load_loop().get("last_watch") or 0))
        self.assertEqual(self.store.load_loop().get("skipped_reason"), "watch_error")

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
        with leads_desk_lock(self.store, wait_s=0) as got:
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

    def test_expired_hunt_recovers_even_if_a_foreign_pid_is_alive(self) -> None:
        start_loop(self.store, run_now=False, now=self.now, collect_fn=_collect_ok)
        row = {
            **self.store.load_loop(),
            "phase": "hunt",
            "hunt_pid": 1,
            "hunt_started_at": self.now - 9 * 60,
            "enabled": True,
        }
        self.store.save_loop(row)
        self.assertTrue(hunt_expired(row, now=self.now))
        self.assertFalse(hunt_is_live(self.store, row))
        recovered = recover_stale_hunt(self.store, now=self.now)
        self.assertEqual(recovered.get("skipped_reason"), "stale_hunt")
        self.assertNotEqual(recovered.get("phase"), "hunt")

    def test_corrupt_loop_json_is_rebuilt(self) -> None:
        self.store.loop_path.write_text("[]", encoding="utf-8")
        row = self.store.load_loop()
        self.assertIsInstance(row, dict)
        self.assertFalse(row.get("enabled"))
        self.assertEqual(row.get("schedule", {}).get("kind"), "daily")

    def test_pause_wins_while_the_lock_is_held(self) -> None:
        start_loop(self.store, run_now=False, now=self.now, collect_fn=_collect_ok)
        held = threading.Event()
        release = threading.Event()

        def hold() -> None:
            with leads_desk_lock(self.store, wait_s=2) as got:
                self.assertTrue(got)
                held.set()
                release.wait(4)

        worker = threading.Thread(target=hold, daemon=True)
        worker.start()
        self.assertTrue(held.wait(2))
        paused = stop_loop(self.store, now=self.now + 10)
        self.assertFalse(paused["enabled"])
        self.assertEqual(paused["phase"], "paused")
        self.assertTrue(self.store.loop_intent_path.exists())
        release.set()
        worker.join(2)
        idle = maybe_tick(self.store, now=self.now + 10**7, collect_fn=_collect_ok, watch_fn=_watch_ok)
        self.assertEqual(idle["phase"], "paused")
        self.assertFalse(idle["did_work"])

    def test_start_run_now_is_busy_when_the_lock_is_held(self) -> None:
        with leads_desk_lock(self.store, wait_s=0) as got:
            self.assertTrue(got)
            started = start_loop(self.store, run_now=True, now=self.now, collect_fn=_collect_ok)
        self.assertEqual(started["phase"], "busy")
        self.assertFalse(started["did_work"])

    def test_maybe_tick_reports_busy_when_the_lock_is_held(self) -> None:
        start_loop(self.store, run_now=False, now=self.now, collect_fn=_collect_ok)
        with leads_desk_lock(self.store, wait_s=0) as got:
            self.assertTrue(got)
            busy = maybe_tick(
                self.store,
                now=self.now + 10**7,
                collect_fn=_collect_ok,
                watch_fn=_watch_ok,
            )
        self.assertEqual(busy["phase"], "busy")
        self.assertFalse(busy["did_work"])

    def test_profile_must_be_armed(self) -> None:
        self.assertTrue(profile_is_armed(self.store.load_profile()))
        self.assertFalse(profile_is_armed({}))


class LeadsLoopOverlapTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.store = LeadsStore(Path(self.tmp.name))
        _arm(self.store)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_second_tick_is_busy_while_hunt_runs(self) -> None:
        slow = _SlowCollect()
        result: dict[str, object] = {}

        def run() -> None:
            result.update(maybe_tick(self.store, force=True, collect_fn=slow, watch_fn=_watch_ok))

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
            start_loop(self.store, run_now=True, collect_fn=slow, watch_fn=_watch_ok)
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
            result.update(maybe_tick(self.store, force=True, collect_fn=slow, watch_fn=_watch_ok))

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
        with leads_desk_lock(self.store, wait_s=0) as got:
            self.assertTrue(got)

    def test_hung_collect_times_out_and_releases_the_desk(self) -> None:
        started = threading.Event()

        def hung(store: LeadsStore, **kwargs: object) -> dict[str, object]:
            started.set()
            time.sleep(2)
            return {"added": 1, "found": 1, "scanned": 1}

        with patch("navin.leads.loop.MAX_HUNT_S", 0.25):
            result = maybe_tick(
                self.store,
                force=True,
                now=time.time(),
                collect_fn=hung,
                watch_fn=_watch_ok,
            )
        self.assertTrue(started.wait(1))
        self.assertFalse(result["did_work"])
        self.assertIn("exceeded", result["reason"])
        self.assertNotEqual(self.store.load_loop().get("phase"), "hunt")
        with leads_desk_lock(self.store, wait_s=0) as got:
            self.assertTrue(got)
        with patch("navin.leads.watch.run_watch", return_value={"count": 0}) as watch:
            tick_watch(self.store)
        watch.assert_called()

    def test_watch_failure_does_not_lose_the_hunt_or_block_heartbeat(self) -> None:
        start_loop(self.store, run_now=False, now=1_700_000_000.0, collect_fn=_collect_ok)
        result = maybe_tick(
            self.store,
            force=True,
            now=1_700_000_100.0,
            collect_fn=_collect_ok,
            watch_fn=_watch_boom,
        )
        self.assertTrue(result["did_work"])
        self.assertEqual(result["loop"]["added"], 3)
        self.assertEqual(result["loop"].get("skipped_reason"), "watch_error")
        self.assertEqual(float(result["loop"].get("last_watch") or 0), 0)
        self.store.upsert_leads(
            [
                {
                    "company": "Acme",
                    "email": "cto@acme.io",
                    "email_status": "verified",
                    "phone": "+33123456789",
                    "person": "Jean Dupont",
                    "role": "CTO",
                    "country": "FR",
                    "sector": "SaaS",
                    "source": "hunter",
                }
            ]
        )
        with patch("navin.leads.notify.deliver_alert", return_value={"webui": True}):
            again = tick_watch(self.store)
        self.assertGreaterEqual(again["count"], 1)


class LeadsLoopApiAndHeartbeatTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.store = LeadsStore(Path(self.tmp.name))
        _arm(self.store)
        self.now = 1_700_000_000.0

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_api_start_stop_schedule_tick(self) -> None:
        with patch("navin.webui.leads_api._store", return_value=self.store):
            with patch("navin.leads.loop._collect_leads", _collect_ok):
                started = handle_leads_action(
                    "start",
                    {"schedule": {"kind": "daily", "hour": 7, "minute": 0}, "run_now": False},
                )
                self.assertTrue(started["loop"]["enabled"])
                self.assertEqual(started["loop"]["schedule"]["hour"], 7)
                changed = handle_leads_action(
                    "schedule",
                    {"schedule": {"kind": "weekly", "hour": 10, "minute": 0, "weekday": 1}},
                )
                self.assertEqual(changed["loop"]["schedule"]["kind"], "weekly")
                ticked = handle_leads_action("tick", {"force": True})
                self.assertTrue(ticked["loop_tick"]["did_work"])
                stopped = handle_leads_action("stop")
                self.assertFalse(stopped["loop"]["enabled"])

    def test_api_tick_default_force_hunts_while_paused(self) -> None:
        with patch("navin.webui.leads_api._store", return_value=self.store):
            with patch("navin.leads.loop._collect_leads", _collect_ok):
                with patch("navin.leads.watch.run_watch", return_value={"count": 0}):
                    ticked = handle_leads_action("tick", {})
        self.assertTrue(ticked["loop_tick"]["did_work"])
        self.assertFalse(ticked["loop"]["enabled"])

    def test_api_rejects_an_invalid_schedule(self) -> None:
        with patch("navin.webui.leads_api._store", return_value=self.store):
            with self.assertRaises(LeadsError) as ctx:
                handle_leads_action("schedule", {"schedule": {"kind": "never", "hour": 9}})
        self.assertEqual(ctx.exception.status, 400)

    def test_api_start_without_wizard_is_400(self) -> None:
        empty = LeadsStore(Path(self.tmp.name) / "empty")
        with patch("navin.webui.leads_api._store", return_value=empty):
            with self.assertRaises(LeadsError) as ctx:
                handle_leads_action("start", {"schedule": {"kind": "daily", "hour": 9}})
        self.assertEqual(ctx.exception.status, 400)

    def test_heartbeat_refuses_start_stop_schedule_tick(self) -> None:
        self.assertEqual(
            HEARTBEAT_LEADS_ACTIONS,
            {"status", "snapshot", "watch", "follow", "rescore", "score"},
        )
        from navin.agent.tools.context import RequestContext, request_context

        ctx = RequestContext(
            channel="telegram",
            chat_id="1",
            session_key="heartbeat",
            metadata={"heartbeat": True},
        )
        with patch("navin.webui.leads_api._store", return_value=self.store):
            with request_context(ctx):
                for action in ("start", "stop", "schedule", "tick", "hunt", "enrich"):
                    with self.assertRaises(LeadsError, msg=action) as err:
                        handle_leads_action(action, {"schedule": {"kind": "daily", "hour": 9}})
                    self.assertIn("heartbeat", err.exception.message.lower())

    def test_heartbeat_never_collects(self) -> None:
        with patch("navin.leads.waterfall.hunt_companies") as hunt:
            payload = tick_watch(self.store)
        hunt.assert_not_called()
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

    def test_heartbeat_skips_when_loop_is_hunting(self) -> None:
        mark_loop_hunting(self.store, True)
        self.store.save_loop(
            {
                **self.store.load_loop(),
                "phase": "hunt",
                "hunt_started_at": time.time(),
                "hunt_pid": 1,
            }
        )
        try:
            with patch("navin.leads.watch.run_watch") as watch:
                started = time.monotonic()
                silent = tick_watch(self.store)
            self.assertLess(time.monotonic() - started, 0.5)
            self.assertEqual(silent.get("skipped"), "loop_hunting")
            self.assertEqual(silent["count"], 0)
            watch.assert_not_called()
        finally:
            mark_loop_hunting(self.store, False)

    def test_heartbeat_does_not_touch_the_store_while_hunting(self) -> None:
        self.store.save_loop({**self.store.load_loop(), "enabled": True, "phase": "hunt"})
        mark_loop_hunting(self.store, True)
        before = self.store.load_leads()
        try:
            with patch("navin.leads.watch.run_watch") as watch:
                skipped = tick_watch(self.store)
            self.assertEqual(skipped["skipped"], "loop_hunting")
            watch.assert_not_called()
            self.assertEqual(self.store.load_leads(), before)
        finally:
            mark_loop_hunting(self.store, False)

    def test_heartbeat_watch_error_does_not_block(self) -> None:
        def hang(_store: LeadsStore, **_kwargs: object) -> dict[str, object]:
            time.sleep(2)
            return {"count": 0, "events": [], "digest": "", "sent": {}}

        started = time.monotonic()
        with (
            patch("navin.leads.heartbeat.HEARTBEAT_WATCH_S", 0.05),
            patch("navin.leads.watch.run_watch", hang),
        ):
            payload = tick_watch(self.store)
        self.assertLess(time.monotonic() - started, 1.0)
        self.assertEqual(payload["skipped"], "watch_error")
        self.assertEqual(payload["count"], 0)
        self.assertFalse(float(self.store.load_loop().get("last_watch") or 0))
        self.assertFalse(float(self.store.load_profile().get("last_watch") or 0))

    def test_heartbeat_prompt_note_and_desks_tick(self) -> None:
        self.assertEqual(heartbeat_prompt_note({"count": 0, "digest": "nope"}), "")
        note = heartbeat_prompt_note({"count": 2, "digest": "Lead: Acme"})
        self.assertIn("watch.count=2", note)
        self.assertIn("Lead: Acme", note)
        self.assertIn("Never hunt", note)
        self.assertNotIn("\u2014", note)
        self.assertNotIn("\u2013", note)
        from navin.gateway.heartbeat_desks import tick_heartbeat_desks

        with patch("navin.tenders.heartbeat.tick_watch", return_value=None):
            with patch("navin.career.heartbeat.tick_watch", return_value=None):
                with patch(
                    "navin.leads.heartbeat.tick_watch",
                    return_value={"count": 1, "digest": "Lead: Acme"},
                ):
                    with patch("navin.marketing.heartbeat.tick_watch", return_value=None):
                        with patch("navin.trading.heartbeat.tick_watch", return_value=None):
                            combined = tick_heartbeat_desks()
        self.assertIn("Leads watch already ran", combined)
        self.assertIn("Lead: Acme", combined)

    def test_snapshot_shows_pause_intent(self) -> None:
        start_loop(self.store, run_now=False, collect_fn=_collect_ok)
        self.store.save_loop_intent({"enabled": False})
        with patch("navin.webui.leads_api._store", return_value=self.store):
            snap = handle_leads_action("snapshot")
        self.assertFalse(snap["loop"]["enabled"])
        self.assertEqual(snap["loop"]["phase"], "paused")

    def test_snapshot_includes_loop(self) -> None:
        start_loop(
            self.store,
            schedule={"kind": "weekend", "hour": 10, "minute": 0},
            run_now=False,
            now=self.now,
        )
        with patch("navin.webui.leads_api._store", return_value=self.store):
            snap = handle_leads_action("snapshot")
        self.assertTrue(snap["loop"]["enabled"])
        self.assertEqual(snap["loop"]["schedule"]["kind"], "weekend")

    def test_supervisor_and_heartbeat_policy_stay_split(self) -> None:
        commands = (ROOT / "navin/cli/commands.py").read_text(encoding="utf-8")
        self.assertIn("navin-leads-loop", commands)
        self.assertIn("leads_tick_inflight", commands)
        self.assertIn("leads_maybe_tick", commands)
        self.assertIn("from navin.leads.loop import maybe_tick", commands)
        self.assertNotIn('job.name == "leads-loop"', commands)
        self.assertIn("tick_heartbeat_desks", commands)
        pulse = (ROOT / "navin/leads/heartbeat.py").read_text(encoding="utf-8")
        self.assertNotIn("hunt_companies", pulse)
        self.assertIn("loop_hunting", pulse)
        self.assertIn("loop_just_watched", pulse)
        for denied in ("start", "stop", "schedule", "tick", "hunt", "enrich"):
            self.assertNotIn(denied, HEARTBEAT_LEADS_ACTIONS)

    def test_career_contract_no_chat_cron_stop_wins_silent_watch(self) -> None:
        commands = (ROOT / "navin/cli/commands.py").read_text(encoding="utf-8")
        self.assertIn("navin-leads-loop", commands)
        desks = (ROOT / "navin/gateway/heartbeat_desks.py").read_text(encoding="utf-8")
        self.assertIn("from navin.leads.heartbeat import tick_watch as leads_tick", desks)
        seed = json.loads((ROOT / "webui/src/i18n/locales/en/common.json").read_text(encoding="utf-8"))
        leads_seed = seed["thread"]["sessionInfo"]["createSeed"]["leads"]
        self.assertIn("Do not create a chat cron", leads_seed)
        self.assertNotIn("Create a loop for this leads chat", leads_seed)
        self.assertIn("leads action=start", leads_seed)
        self.assertIn("leads action=stop", leads_seed)
        loop_src = (ROOT / "navin/leads/loop.py").read_text(encoding="utf-8")
        self.assertIn("Stop always wins", loop_src)
        self.assertIn("load_loop_intent", loop_src)
        self.assertIn("_finish_hunt_state", loop_src)

    def test_heartbeat_md_says_loop_is_not_heartbeat(self) -> None:
        live = (ROOT / ".navin/HEARTBEAT.md").read_text(encoding="utf-8")
        template = (ROOT / "navin/templates/HEARTBEAT.md").read_text(encoding="utf-8")
        marker = "### Lead scores (silent unless actionable)"
        live_block = live.split(marker, 1)[1].split("### ", 1)[0]
        template_block = template.split(marker, 1)[1].split("### ", 1)[0]
        self.assertEqual(live_block, template_block)
        for body in (live, template):
            self.assertIn("leads action=watch", body)
            self.assertIn("The Leads desk loop", body)
            self.assertIn("That is not this heartbeat", body)
            self.assertIn("Never hunt", body)
            self.assertNotIn("\u2014", body)
            self.assertNotIn("\u2013", body)


if __name__ == "__main__":
    unittest.main()
