# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Tenders wall-clock loop: start, schedule, pause, forced cycle. Never sends a bid."""

from __future__ import annotations

import io
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from threading import Event, Thread

from navin.tenders.errors import TenderError
from navin.tenders.heartbeat import heartbeat_prompt_note, tick_watch
from navin.tenders.lock import tenders_desk_lock
from navin.tenders.loop import (
    format_loop_status,
    hunt_is_live,
    mark_loop_hunting,
    maybe_tick,
    peek_loop,
    recover_stale_hunt,
    start_loop,
    stop_loop,
    update_loop_schedule,
)
from navin.tenders.store import TenderStore
from navin.webui.tenders_api import handle_tenders_action


def _ready_profile() -> dict:
    return {
        "name": "Acme Bid",
        "country": "FR",
        "currency": "EUR",
        "specialty": "Cloud",
        "countries": ["FR"],
        "crafts": ["Cloud"],
        "source_ids": ["ted"],
        "wizard_complete": True,
    }


class TendersLoopTests(unittest.TestCase):
    def test_snapshot_exposes_a_paused_loop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = TenderStore(Path(tmp))
            store.save_profile(_ready_profile())
            with patch("navin.webui.tenders_api._store", return_value=store):
                snap = handle_tenders_action("snapshot")
            loop = snap["loop"]
            self.assertFalse(loop.get("enabled"))
            self.assertEqual(loop.get("schedule", {}).get("kind"), "daily")
            self.assertEqual(int(loop.get("schedule", {}).get("hour") or 0), 9)

    def test_start_needs_a_ready_desk(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = TenderStore(Path(tmp))
            store.save_profile({"name": "Acme"})
            with self.assertRaises(TenderError) as ctx:
                start_loop(store, schedule={"kind": "daily", "hour": 10, "minute": 0})
            self.assertEqual(ctx.exception.status, 400)

    def test_start_stop_and_change_schedule(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = TenderStore(Path(tmp))
            store.save_profile(_ready_profile())
            now = 1_700_000_000.0
            started = start_loop(
                store,
                schedule={"kind": "weekdays", "hour": 8, "minute": 30},
                tz="Europe/Paris",
                now=now,
            )
            self.assertEqual(started["phase"], "armed")
            state = store.load_loop()
            self.assertTrue(state["enabled"])
            self.assertEqual(state["schedule"]["kind"], "weekdays")
            self.assertEqual(state["schedule"]["hour"], 8)
            self.assertEqual(state["schedule"]["minute"], 30)
            self.assertGreater(float(state["next_due"] or 0), now)

            sleeping = maybe_tick(store, now=now + 10)
            self.assertEqual(sleeping["phase"], "sleep")
            self.assertFalse(sleeping["did_work"])

            update_loop_schedule(
                store,
                schedule={"kind": "weekend", "hour": 9, "minute": 0},
                tz="Europe/Paris",
                now=now,
            )
            self.assertEqual(store.load_loop()["schedule"]["kind"], "weekend")

            paused = stop_loop(store)
            self.assertFalse(paused["enabled"])
            self.assertEqual(paused["phase"], "paused")
            idle = maybe_tick(store, now=now + 10_000)
            self.assertEqual(idle["phase"], "paused")

    def test_forced_cycle_collects_and_watches_without_sending_mail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = TenderStore(Path(tmp))
            store.save_profile(_ready_profile())
            start_loop(store, schedule={"kind": "daily", "hour": 7, "minute": 0}, now=1_700_000_000.0)

            def fake_collect(desk: TenderStore) -> dict:
                return {"collect": [{"source_id": "ted", "ok": True, "count": 2}]}

            def fake_watch(desk: TenderStore) -> dict:
                return {"count": 1, "delivered": True, "sent": {"telegram": True}}

            with patch("navin.tenders.desk.send_mail") as mail:
                result = maybe_tick(
                    store,
                    force=True,
                    now=1_700_000_100.0,
                    collect_fn=fake_collect,
                    watch_fn=fake_watch,
                )
            self.assertTrue(result["did_work"])
            self.assertIn("new notices", result["reason"])
            self.assertEqual(result["loop"]["added"], 2)
            self.assertEqual(result["loop"]["alerts"], 1)
            mail.assert_not_called()

    def test_api_start_schedule_tick_and_stop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = TenderStore(Path(tmp))
            store.save_profile(_ready_profile())
            with patch("navin.webui.tenders_api._store", return_value=store):
                started = handle_tenders_action(
                    "start",
                    {
                        "schedule": {"kind": "monthly", "hour": 11, "minute": 15, "day": 1},
                        "tz": "Europe/Paris",
                        "run_now": False,
                    },
                )
                self.assertTrue(started["loop"]["enabled"])
                self.assertEqual(started["loop"]["schedule"]["kind"], "monthly")
                self.assertEqual(started["loop"]["schedule"]["hour"], 11)

                changed = handle_tenders_action(
                    "schedule",
                    {"schedule": {"kind": "daily", "hour": 6, "minute": 0}, "tz": "Europe/Paris"},
                )
                self.assertEqual(changed["loop"]["schedule"]["kind"], "daily")
                self.assertEqual(changed["loop"]["schedule"]["hour"], 6)

                with patch(
                    "navin.webui.tenders_api.maybe_tick",
                    return_value={"did_work": True, "reason": "cycle 1: 0 new notices", "loop": {}},
                ) as tick:
                    ticked = handle_tenders_action("tick", {"force": True})
                tick.assert_called_once()
                self.assertEqual(ticked["loop_tick"]["reason"], "cycle 1: 0 new notices")

                stopped = handle_tenders_action("stop")
                self.assertFalse(stopped["loop"]["enabled"])

    def test_pause_wins_while_a_collect_holds_the_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = TenderStore(Path(tmp))
            store.save_profile(_ready_profile())
            start_loop(store, schedule={"kind": "daily", "hour": 9, "minute": 0}, now=1_700_000_000.0)
            held = Event()
            release = Event()

            def hold() -> None:
                with tenders_desk_lock(store, wait_s=2) as got:
                    self.assertTrue(got)
                    held.set()
                    release.wait(4)

            worker = Thread(target=hold, daemon=True)
            worker.start()
            self.assertTrue(held.wait(2))
            paused = stop_loop(store, now=1_700_000_010.0)
            self.assertFalse(paused["enabled"])
            self.assertEqual(paused["phase"], "paused")
            self.assertTrue(store.loop_intent_path.exists())
            release.set()
            worker.join(2)
            idle = maybe_tick(store, now=1_700_000_100.0)
            self.assertEqual(idle["phase"], "paused")
            self.assertFalse(idle["did_work"])

    def test_heartbeat_stands_aside_while_the_loop_hunts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = TenderStore(Path(tmp))
            store.save_profile({"name": "Acme Bid"})
            store.save_loop({**store.load_loop(), "enabled": True, "phase": "hunt"})
            mark_loop_hunting(store, True)
            try:
                with patch("navin.tenders.watch.run_watch") as watch:
                    skipped = tick_watch(store)
                self.assertEqual(skipped["skipped"], "loop_hunting")
                self.assertEqual(skipped["count"], 0)
                watch.assert_not_called()
            finally:
                mark_loop_hunting(store, False)

    def test_heartbeat_prompt_note_and_desks_tick(self) -> None:
        self.assertEqual(heartbeat_prompt_note({"count": 0, "digest": "nope"}), "")
        note = heartbeat_prompt_note({"count": 2, "digest": "GO: Cloud platform"})
        self.assertIn("watch.count=2", note)
        self.assertIn("GO: Cloud platform", note)
        self.assertIn("Never collect", note)
        self.assertNotIn("\u2014", note)
        self.assertNotIn("\u2013", note)
        from navin.gateway.heartbeat_desks import tick_heartbeat_desks

        with patch(
            "navin.tenders.heartbeat.tick_watch",
            return_value={"count": 1, "digest": "D-3: Bridge works"},
        ):
            with patch("navin.career.heartbeat.tick_watch", return_value=None):
                with patch("navin.leads.heartbeat.tick_watch", return_value=None):
                    with patch("navin.marketing.heartbeat.tick_watch", return_value=None):
                        combined = tick_heartbeat_desks()
        self.assertIn("Tenders watch already ran", combined)
        self.assertIn("D-3: Bridge works", combined)

    def test_start_run_now_collects_without_buyer_mail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = TenderStore(Path(tmp))
            store.save_profile(_ready_profile())

            def fake_collect(desk: TenderStore) -> dict:
                return {"collect": [{"source_id": "ted", "ok": True, "count": 4}]}

            def fake_watch(desk: TenderStore) -> dict:
                return {"count": 2, "delivered": True, "sent": {"telegram": True}}

            with patch("navin.tenders.desk.send_mail") as mail:
                result = start_loop(
                    store,
                    schedule={"kind": "daily", "hour": 7, "minute": 0},
                    run_now=True,
                    now=1_700_000_000.0,
                    collect_fn=fake_collect,
                    watch_fn=fake_watch,
                )
            self.assertTrue(result["did_work"])
            self.assertEqual(result["loop"]["added"], 4)
            self.assertEqual(result["loop"]["alerts"], 2)
            self.assertTrue(result["loop"]["enabled"])
            mail.assert_not_called()

    def test_all_schedule_kinds_arm_a_future_slot(self) -> None:
        now = 1_700_000_000.0
        kinds = (
            {"kind": "daily", "hour": 10, "minute": 15},
            {"kind": "weekdays", "hour": 8, "minute": 0},
            {"kind": "weekend", "hour": 10, "minute": 30},
            {"kind": "weekly", "hour": 9, "minute": 0, "weekday": 2},
            {"kind": "monthly", "hour": 11, "minute": 15, "day": 15},
        )
        with tempfile.TemporaryDirectory() as tmp:
            store = TenderStore(Path(tmp))
            store.save_profile(_ready_profile())
            for schedule in kinds:
                started = start_loop(store, schedule=schedule, tz="Europe/Paris", now=now)
                self.assertTrue(started["loop"]["enabled"], schedule["kind"])
                self.assertEqual(started["loop"]["schedule"]["kind"], schedule["kind"])
                self.assertGreater(float(started["loop"]["next_due"] or 0), now, schedule["kind"])
                self.assertEqual(maybe_tick(store, now=now + 10)["phase"], "sleep")
                stop_loop(store, now=now)

    def test_restart_after_pause_and_forced_tick_stays_paused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = TenderStore(Path(tmp))
            store.save_profile(_ready_profile())
            now = 1_700_000_000.0
            start_loop(store, schedule={"kind": "daily", "hour": 9, "minute": 0}, now=now)
            stop_loop(store, now=now)
            restarted = start_loop(
                store,
                schedule={"kind": "weekdays", "hour": 8, "minute": 0},
                tz="Europe/Paris",
                now=now,
            )
            self.assertTrue(restarted["loop"]["enabled"])
            self.assertEqual(restarted["phase"], "armed")
            stop_loop(store, now=now)

            def fake_collect(desk: TenderStore) -> dict:
                return {"collect": [{"source_id": "ted", "ok": True, "count": 1}]}

            def fake_watch(desk: TenderStore) -> dict:
                return {"count": 0, "delivered": True}

            forced = maybe_tick(
                store,
                force=True,
                now=now + 50,
                collect_fn=fake_collect,
                watch_fn=fake_watch,
            )
            self.assertTrue(forced["did_work"])
            self.assertFalse(forced["loop"]["enabled"])
            self.assertEqual(forced["loop"]["phase"], "paused")
            idle = maybe_tick(store, now=now + 10_000)
            self.assertEqual(idle["phase"], "paused")
            self.assertFalse(idle["did_work"])

    def test_snapshot_shows_pause_while_the_lock_is_held(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = TenderStore(Path(tmp))
            store.save_profile(_ready_profile())
            start_loop(store, schedule={"kind": "daily", "hour": 9, "minute": 0}, now=1_700_000_000.0)
            held = Event()
            release = Event()

            def hold() -> None:
                with tenders_desk_lock(store, wait_s=2) as got:
                    self.assertTrue(got)
                    held.set()
                    release.wait(4)

            worker = Thread(target=hold, daemon=True)
            worker.start()
            self.assertTrue(held.wait(2))
            stop_loop(store, now=1_700_000_010.0)
            with patch("navin.webui.tenders_api._store", return_value=store):
                snap = handle_tenders_action("snapshot")
            self.assertFalse(snap["loop"]["enabled"])
            self.assertEqual(snap["loop"]["phase"], "paused")
            disk = store.load_loop()
            self.assertTrue(disk.get("enabled"))
            release.set()
            worker.join(2)

    def test_schedule_change_wins_while_the_lock_is_held(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = TenderStore(Path(tmp))
            store.save_profile(_ready_profile())
            start_loop(store, schedule={"kind": "daily", "hour": 9, "minute": 0}, now=1_700_000_000.0)
            held = Event()
            release = Event()

            def hold() -> None:
                with tenders_desk_lock(store, wait_s=2) as got:
                    self.assertTrue(got)
                    held.set()
                    release.wait(4)

            worker = Thread(target=hold, daemon=True)
            worker.start()
            self.assertTrue(held.wait(2))
            changed = update_loop_schedule(
                store,
                schedule={"kind": "weekend", "hour": 10, "minute": 30},
                tz="Europe/Paris",
                now=1_700_000_010.0,
            )
            self.assertEqual(changed["schedule"]["kind"], "weekend")
            self.assertEqual(changed["schedule"]["hour"], 10)
            self.assertTrue(changed["enabled"])
            self.assertTrue(store.loop_intent_path.exists())
            self.assertEqual(store.load_loop()["schedule"]["kind"], "daily")
            release.set()
            worker.join(2)
            persisted = peek_loop(store, now=1_700_000_020.0)
            self.assertEqual(persisted["schedule"]["kind"], "weekend")

    def test_start_run_now_is_busy_when_the_lock_is_held(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = TenderStore(Path(tmp))
            store.save_profile(_ready_profile())
            held = Event()
            release = Event()

            def hold() -> None:
                with tenders_desk_lock(store, wait_s=2) as got:
                    self.assertTrue(got)
                    held.set()
                    release.wait(4)

            worker = Thread(target=hold, daemon=True)
            worker.start()
            self.assertTrue(held.wait(2))
            result = start_loop(
                store,
                schedule={"kind": "daily", "hour": 7, "minute": 0},
                run_now=True,
                now=1_700_000_000.0,
            )
            self.assertEqual(result["phase"], "busy")
            self.assertFalse(result["did_work"])
            self.assertTrue(peek_loop(store)["enabled"])
            self.assertTrue(store.loop_intent_path.exists())
            release.set()
            worker.join(2)

    def test_maybe_tick_reports_busy_when_the_lock_is_held(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = TenderStore(Path(tmp))
            store.save_profile(_ready_profile())
            start_loop(store, schedule={"kind": "daily", "hour": 9, "minute": 0}, now=1_700_000_000.0)
            store.save_loop({**store.load_loop(), "next_due": 0})
            held = Event()
            release = Event()

            def hold() -> None:
                with tenders_desk_lock(store, wait_s=2) as got:
                    self.assertTrue(got)
                    held.set()
                    release.wait(4)

            worker = Thread(target=hold, daemon=True)
            worker.start()
            self.assertTrue(held.wait(2))
            busy = maybe_tick(store, now=1_700_010_000.0)
            self.assertEqual(busy["phase"], "busy")
            self.assertFalse(busy["did_work"])
            release.set()
            worker.join(2)

    def test_pause_during_collect_is_applied_when_the_cycle_finishes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = TenderStore(Path(tmp))
            store.save_profile(_ready_profile())
            start_loop(store, schedule={"kind": "daily", "hour": 9, "minute": 0}, now=1_700_000_000.0)
            started = Event()
            release = Event()
            result_box: list[dict] = []

            def slow_collect(desk: TenderStore) -> dict:
                started.set()
                self.assertTrue(release.wait(6))
                return {"collect": [{"source_id": "ted", "ok": True, "count": 3}]}

            def fake_watch(desk: TenderStore) -> dict:
                return {"count": 1, "delivered": True, "sent": {"telegram": True}}

            def hunt() -> None:
                result_box.append(
                    maybe_tick(
                        store,
                        force=True,
                        now=1_700_000_100.0,
                        collect_fn=slow_collect,
                        watch_fn=fake_watch,
                    )
                )

            worker = Thread(target=hunt, daemon=True)
            worker.start()
            self.assertTrue(started.wait(3))
            paused = stop_loop(store, now=1_700_000_110.0)
            self.assertFalse(paused["enabled"])
            self.assertEqual(paused["phase"], "paused")
            self.assertTrue(store.loop_intent_path.exists())
            disk = store.load_loop()
            self.assertEqual(disk.get("phase"), "hunt")
            self.assertTrue(disk.get("enabled"))
            self.assertFalse(peek_loop(store)["enabled"])
            with patch("navin.webui.tenders_api._store", return_value=store):
                snap = handle_tenders_action("snapshot")
            self.assertFalse(snap["loop"]["enabled"])
            with patch("navin.tenders.watch.run_watch") as heartbeat_watch:
                skipped = tick_watch(store)
            self.assertEqual(skipped["skipped"], "loop_hunting")
            heartbeat_watch.assert_not_called()
            release.set()
            worker.join(6)
            self.assertTrue(result_box)
            finished = result_box[0]
            self.assertTrue(finished["did_work"])
            self.assertFalse(finished["loop"]["enabled"])
            self.assertEqual(finished["loop"]["phase"], "paused")
            self.assertFalse(store.loop_intent_path.exists())
            persisted = store.load_loop()
            self.assertFalse(persisted["enabled"])
            self.assertEqual(persisted["phase"], "paused")
            self.assertEqual(persisted.get("added"), 3)
            idle = maybe_tick(store, now=1_700_010_000.0)
            self.assertEqual(idle["phase"], "paused")
            self.assertFalse(idle["did_work"])

    def test_schedule_change_during_collect_is_applied_when_the_cycle_finishes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = TenderStore(Path(tmp))
            store.save_profile(_ready_profile())
            start_loop(store, schedule={"kind": "daily", "hour": 9, "minute": 0}, now=1_700_000_000.0)
            started = Event()
            release = Event()

            def slow_collect(desk: TenderStore) -> dict:
                started.set()
                self.assertTrue(release.wait(6))
                return {"collect": [{"source_id": "ted", "ok": True, "count": 1}]}

            def fake_watch(desk: TenderStore) -> dict:
                return {"count": 0, "delivered": True}

            worker = Thread(
                target=lambda: maybe_tick(
                    store,
                    force=True,
                    now=1_700_000_100.0,
                    collect_fn=slow_collect,
                    watch_fn=fake_watch,
                ),
                daemon=True,
            )
            worker.start()
            self.assertTrue(started.wait(3))
            update_loop_schedule(
                store,
                schedule={"kind": "weekend", "hour": 10, "minute": 30},
                tz="Europe/Paris",
                now=1_700_000_110.0,
            )
            release.set()
            worker.join(6)
            persisted = store.load_loop()
            self.assertTrue(persisted["enabled"])
            self.assertEqual(persisted["schedule"]["kind"], "weekend")
            self.assertEqual(persisted["schedule"]["hour"], 10)
            self.assertFalse(store.loop_intent_path.exists())

    def test_cycle_error_still_applies_a_pause_intent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = TenderStore(Path(tmp))
            store.save_profile(_ready_profile())
            start_loop(store, schedule={"kind": "daily", "hour": 9, "minute": 0}, now=1_700_000_000.0)
            started = Event()
            release = Event()
            result_box: list[dict] = []

            def boom(desk: TenderStore) -> dict:
                started.set()
                self.assertTrue(release.wait(6))
                raise RuntimeError("portal down")

            def hunt() -> None:
                result_box.append(
                    maybe_tick(
                        store,
                        force=True,
                        now=1_700_000_100.0,
                        collect_fn=boom,
                        watch_fn=lambda desk: {"count": 0},
                    )
                )

            worker = Thread(target=hunt, daemon=True)
            worker.start()
            self.assertTrue(started.wait(3))
            stop_loop(store, now=1_700_000_110.0)
            release.set()
            worker.join(6)
            self.assertTrue(result_box)
            finished = result_box[0]
            self.assertFalse(finished["did_work"])
            self.assertIn("portal down", finished["reason"])
            persisted = store.load_loop()
            self.assertFalse(persisted["enabled"])
            self.assertEqual(persisted["phase"], "paused")
            self.assertEqual(persisted.get("skipped_reason"), "error")
            self.assertFalse(store.loop_intent_path.exists())

    def test_heartbeat_skips_when_the_loop_just_watched(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = TenderStore(Path(tmp))
            store.save_profile({"name": "Acme Bid"})
            store.save_loop({**store.load_loop(), "enabled": True, "phase": "idle", "last_watch": time.time()})
            with patch("navin.tenders.watch.run_watch") as watch:
                skipped = tick_watch(store)
            self.assertEqual(skipped["skipped"], "loop_just_watched")
            self.assertEqual(skipped["count"], 0)
            watch.assert_not_called()

    def test_heartbeat_skips_when_the_desk_lock_is_held(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = TenderStore(Path(tmp))
            store.save_profile({"name": "Acme Bid"})
            store.save_loop({**store.load_loop(), "enabled": True, "phase": "idle", "last_watch": 0})
            held = Event()
            release = Event()

            def hold() -> None:
                with tenders_desk_lock(store, wait_s=2) as got:
                    self.assertTrue(got)
                    held.set()
                    release.wait(4)

            worker = Thread(target=hold, daemon=True)
            worker.start()
            self.assertTrue(held.wait(2))
            with patch("navin.tenders.watch.run_watch") as watch:
                skipped = tick_watch(store)
            self.assertEqual(skipped["skipped"], "loop_busy")
            self.assertEqual(skipped["count"], 0)
            watch.assert_not_called()
            release.set()
            worker.join(2)

    def test_heartbeat_never_collects_and_stamps_last_watch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = TenderStore(Path(tmp))
            store.save_profile({"name": "Acme Bid"})
            store.save_loop({**store.load_loop(), "enabled": True, "phase": "idle", "last_watch": 0})
            with patch("navin.tenders.desk.run_collect") as collect:
                with patch(
                    "navin.tenders.watch.run_watch",
                    return_value={"count": 1, "digest": "GO: Cloud platform", "delivered": True},
                ):
                    first = tick_watch(store)
            collect.assert_not_called()
            self.assertEqual(first["count"], 1)
            self.assertGreater(float(store.load_loop().get("last_watch") or 0), 0)
            with patch("navin.tenders.watch.run_watch") as watch:
                second = tick_watch(store)
            self.assertEqual(second["skipped"], "loop_just_watched")
            watch.assert_not_called()

    def test_heartbeat_stamp_does_not_clobber_a_live_hunt(self) -> None:
        from navin.tenders.heartbeat import _stamp_watch

        with tempfile.TemporaryDirectory() as tmp:
            store = TenderStore(Path(tmp))
            store.save_profile({"name": "Acme Bid"})
            store.save_loop({**store.load_loop(), "phase": "hunt", "last_watch": 0, "enabled": True})
            mark_loop_hunting(store, True)
            try:
                _stamp_watch(store, store.load_loop())
                current = store.load_loop()
                self.assertEqual(current.get("phase"), "hunt")
                self.assertEqual(float(current.get("last_watch") or 0), 0)
            finally:
                mark_loop_hunting(store, False)

    def test_api_start_without_wizard_is_400(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = TenderStore(Path(tmp))
            store.save_profile({"name": "Acme"})
            with patch("navin.webui.tenders_api._store", return_value=store):
                with self.assertRaises(TenderError) as ctx:
                    handle_tenders_action(
                        "start",
                        {"schedule": {"kind": "daily", "hour": 9, "minute": 0}},
                    )
            self.assertEqual(ctx.exception.status, 400)

    def test_api_rejects_an_invalid_schedule(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = TenderStore(Path(tmp))
            store.save_profile(_ready_profile())
            with patch("navin.webui.tenders_api._store", return_value=store):
                with self.assertRaises(TenderError) as ctx:
                    handle_tenders_action("schedule", {"schedule": {"kind": "never", "hour": 9}})
            self.assertEqual(ctx.exception.status, 400)

    def test_api_heartbeat_refuses_loop_controls(self) -> None:
        from navin.agent.tools.context import RequestContext, request_context

        with tempfile.TemporaryDirectory() as tmp:
            store = TenderStore(Path(tmp))
            store.save_profile(_ready_profile())
            ctx = RequestContext(
                channel="telegram",
                chat_id="1",
                session_key="heartbeat",
                metadata={"heartbeat": True},
            )
            with patch("navin.webui.tenders_api._store", return_value=store):
                with request_context(ctx):
                    for action, body in (
                        ("start", {"schedule": {"kind": "daily", "hour": 9}}),
                        ("stop", {}),
                        ("schedule", {"schedule": {"kind": "daily", "hour": 6}}),
                        ("tick", {"force": True}),
                    ):
                        with self.assertRaises(TenderError) as err:
                            handle_tenders_action(action, body)
                        self.assertEqual(err.exception.status, 403, action)
                        self.assertIn("heartbeat", err.exception.message.lower())

    def test_heartbeat_does_not_touch_the_store_while_hunting(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = TenderStore(Path(tmp))
            store.save_profile({"name": "Acme Bid"})
            store.save_loop({**store.load_loop(), "enabled": True, "phase": "hunt"})
            mark_loop_hunting(store, True)
            try:
                with patch("navin.tenders.retention.apply_retention") as retain:
                    with patch("navin.tenders.watch.run_watch") as watch:
                        skipped = tick_watch(store)
                self.assertEqual(skipped["skipped"], "loop_hunting")
                retain.assert_not_called()
                watch.assert_not_called()
            finally:
                mark_loop_hunting(store, False)

    def test_supervisor_and_heartbeat_policy_stay_split(self) -> None:
        root = Path(__file__).resolve().parents[1]
        commands = (root / "navin/cli/commands.py").read_text(encoding="utf-8")
        self.assertIn("navin-tenders-loop", commands)
        self.assertIn("tenders_tick_inflight", commands)
        self.assertIn("tenders_maybe_tick", commands)
        self.assertIn("Tenders loop supervisor crashed - restarting in 5s", commands)
        pulse = (root / "navin/tenders/heartbeat.py").read_text(encoding="utf-8")
        self.assertNotIn("run_collect", pulse)
        self.assertNotIn("send_mail", pulse)
        self.assertIn("loop_hunting", pulse)
        self.assertIn("loop_just_watched", pulse)
        self.assertIn("loop_busy", pulse)
        from navin.tenders.heartbeat import HEARTBEAT_TENDERS_ACTIONS

        for denied in ("start", "stop", "schedule", "tick", "collect", "write", "send"):
            self.assertNotIn(denied, HEARTBEAT_TENDERS_ACTIONS)

    def test_career_contract_no_chat_cron_stop_wins_silent_watch(self) -> None:
        """The three Trading holes: chat cron, Stop overwritten, no heartbeat watch."""
        root = Path(__file__).resolve().parents[1]
        commands = (root / "navin/cli/commands.py").read_text(encoding="utf-8")
        self.assertNotIn('job.name == "tenders-loop"', commands)
        self.assertIn("navin-tenders-loop", commands)
        self.assertIn("tick_heartbeat_desks", commands)
        desks = (root / "navin/gateway/heartbeat_desks.py").read_text(encoding="utf-8")
        self.assertIn("from navin.tenders.heartbeat import heartbeat_prompt_note, tick_watch", desks)
        seed = json.loads((root / "webui/src/i18n/locales/en/common.json").read_text(encoding="utf-8"))
        tenders_seed = seed["thread"]["sessionInfo"]["createSeed"]["tenders"]
        self.assertIn("Do not create a chat cron", tenders_seed)
        self.assertNotIn("Create a loop for this tenders chat", tenders_seed)
        self.assertIn("tenders action=start", tenders_seed)
        self.assertIn("tenders action=stop", tenders_seed)
        from navin.tenders.heartbeat import HEARTBEAT_TENDERS_ACTIONS

        self.assertTrue({"follow", "watch"} <= set(HEARTBEAT_TENDERS_ACTIONS))
        self.assertNotIn("start", HEARTBEAT_TENDERS_ACTIONS)
        self.assertNotIn("tick", HEARTBEAT_TENDERS_ACTIONS)
        loop_src = (root / "navin/tenders/loop.py").read_text(encoding="utf-8")
        self.assertIn("Stop always wins", loop_src)
        self.assertIn("load_loop_intent", loop_src)
        self.assertIn("_finish_cycle_state", loop_src)

    def test_desk_cli_flags_start_stop_schedule_same_store(self) -> None:
        from navin.tenders.desk_cli import HELP, main

        self.assertIn("navin tenders start", HELP)
        self.assertIn("Studio #/tenders", HELP)
        self.assertIn("Tauri", HELP)
        self.assertIn("Never send a buyer mail", HELP)
        with tempfile.TemporaryDirectory() as tmp:
            store = TenderStore(Path(tmp))
            store.save_profile(_ready_profile())
            with patch("navin.webui.tenders_api._store", return_value=store):
                started = io.StringIO()
                with patch("sys.stdout", started):
                    with patch("sys.stdin", io.StringIO("")):
                        code = main(
                            ["start", "--kind", "weekdays", "--hour", "8", "--minute", "30", "--tz", "Europe/Paris"]
                        )
                self.assertEqual(code, 0)
                payload = json.loads(started.getvalue())
                self.assertTrue(payload["loop"]["enabled"])
                self.assertEqual(payload["loop"]["schedule"]["kind"], "weekdays")
                self.assertEqual(payload["loop"]["schedule"]["hour"], 8)

                changed = io.StringIO()
                with patch("sys.stdout", changed):
                    with patch("sys.stdin", io.StringIO("")):
                        code = main(["schedule", "--kind", "weekend", "--hour", "10", "--minute", "30"])
                self.assertEqual(code, 0)
                self.assertEqual(json.loads(changed.getvalue())["loop"]["schedule"]["kind"], "weekend")

                paused = io.StringIO()
                with patch("sys.stdout", paused):
                    with patch("sys.stdin", io.StringIO("")):
                        code = main(["pause"])
                self.assertEqual(code, 0)
                self.assertFalse(json.loads(paused.getvalue())["loop"]["enabled"])

    def test_desk_cli_stdin_json_still_works_for_vite(self) -> None:
        from navin.tenders.desk_cli import main

        with tempfile.TemporaryDirectory() as tmp:
            store = TenderStore(Path(tmp))
            store.save_profile(_ready_profile())
            body = json.dumps({"schedule": {"kind": "daily", "hour": 7, "minute": 0}, "run_now": False})
            with patch("navin.webui.tenders_api._store", return_value=store):
                buf = io.StringIO()
                with patch("sys.stdout", buf):
                    with patch("sys.stdin", io.StringIO(body)):
                        self.assertEqual(main(["start"]), 0)
            payload = json.loads(buf.getvalue())
            self.assertTrue(payload["loop"]["enabled"])
            self.assertEqual(payload["loop"]["schedule"]["hour"], 7)

    def test_desk_cli_does_not_block_on_a_tty(self) -> None:
        from navin.tenders.desk_cli import main

        with tempfile.TemporaryDirectory() as tmp:
            store = TenderStore(Path(tmp))
            store.save_profile(_ready_profile())
            tty = io.StringIO("")
            tty.isatty = lambda: True  # type: ignore[method-assign]
            with patch("navin.webui.tenders_api._store", return_value=store):
                buf = io.StringIO()
                with patch("sys.stdout", buf):
                    with patch("sys.stdin", tty):
                        self.assertEqual(main(["stop"]), 0)
            self.assertFalse(json.loads(buf.getvalue())["loop"]["enabled"])

    def test_stale_hunt_recovers_so_heartbeat_and_pause_work(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = TenderStore(Path(tmp))
            store.save_profile(_ready_profile())
            start_loop(store, schedule={"kind": "daily", "hour": 9, "minute": 0}, now=1_700_000_000.0)
            store.save_loop({**store.load_loop(), "phase": "hunt", "hunt_pid": 2_000_000_001})
            self.assertFalse(hunt_is_live(store, store.load_loop()))
            recovered = recover_stale_hunt(store, now=1_700_000_010.0)
            self.assertTrue(recovered["enabled"])
            self.assertEqual(recovered["phase"], "idle")
            self.assertEqual(recovered.get("skipped_reason"), "stale_hunt")
            self.assertNotEqual(peek_loop(store).get("phase"), "hunt")
            with patch("navin.tenders.watch.run_watch", return_value={"count": 0}) as watch:
                tick_watch(store)
            watch.assert_called_once()
            stop_loop(store)
            store.save_loop({**store.load_loop(), "phase": "hunt", "enabled": False, "hunt_pid": 2_000_000_001})
            paused = peek_loop(store)
            self.assertEqual(paused["phase"], "paused")
            self.assertFalse(paused["enabled"])

    def test_live_hunt_is_not_recovered(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = TenderStore(Path(tmp))
            store.save_profile({"name": "Acme Bid"})
            store.save_loop({**store.load_loop(), "enabled": True, "phase": "hunt"})
            mark_loop_hunting(store, True)
            try:
                self.assertTrue(hunt_is_live(store, store.load_loop()))
                peeked = peek_loop(store)
                self.assertEqual(peeked["phase"], "hunt")
            finally:
                mark_loop_hunting(store, False)

    def test_agent_book_states_the_loop_so_autonomy_has_no_surprise(self) -> None:
        from navin.tenders.index import format_agent_status

        block = format_loop_status({"enabled": True, "phase": "armed", "schedule": {"kind": "daily", "hour": 9, "minute": 0}, "next_due": 1_700_000_000, "last_result": "loop armed"})
        self.assertIn("LOOP ON", block)
        self.assertIn("tenders action=start", block)
        self.assertIn("navin tenders", block)
        self.assertIn("Heartbeat is follow only", block)
        self.assertNotIn("\u2014", block)
        with tempfile.TemporaryDirectory() as tmp:
            store = TenderStore(Path(tmp))
            store.save_profile(_ready_profile())
            start_loop(store, schedule={"kind": "weekend", "hour": 10, "minute": 30}, now=1_700_000_000.0)
            book = format_agent_status(
                {"profile": store.load_profile(), "kpis": {}, "tenders": [], "wizard_ready": True, "loop": peek_loop(store)},
                store,
            )
            self.assertIn("LOOP ON", book)
            self.assertIn("tenders action=start", book)
            self.assertIn("Never collect, write, start or tick from heartbeat", book)
            self.assertIn("The desk loop collects on its schedule", book)
            with patch("navin.webui.tenders_api._store", return_value=store):
                snap = handle_tenders_action("snapshot")
            self.assertIn("LOOP ON", snap["book"])
            self.assertTrue(snap["loop"]["enabled"])

    def test_typer_navin_tenders_is_registered(self) -> None:
        from navin.cli.commands import app, tenders_desk

        names = {cmd.name for cmd in app.registered_commands}
        self.assertIn("tenders", names)
        self.assertIn("career", names)
        self.assertTrue(callable(tenders_desk))

    def test_cycle_error_retries_soon_instead_of_waiting_a_day(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = TenderStore(Path(tmp))
            store.save_profile(_ready_profile())
            now = 1_700_000_000.0
            start_loop(store, schedule={"kind": "daily", "hour": 9, "minute": 0}, now=now)

            def boom(desk: TenderStore) -> dict:
                raise RuntimeError("TED timeout")

            failed = maybe_tick(
                store,
                force=True,
                now=now + 50,
                collect_fn=boom,
                watch_fn=lambda desk: {"count": 0, "delivered": True},
            )
            self.assertFalse(failed["did_work"])
            due = float(failed["loop"]["next_due"] or 0)
            self.assertGreater(due, now + 50)
            self.assertLess(due, now + 50 + 180)
            self.assertEqual(int(failed["loop"].get("error_streak") or 0), 1)

    def test_watch_failure_does_not_lose_the_hunt_or_block_heartbeat(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = TenderStore(Path(tmp))
            store.save_profile(_ready_profile())
            start_loop(store, schedule={"kind": "daily", "hour": 7, "minute": 0}, now=1_700_000_000.0)

            def fake_collect(desk: TenderStore) -> dict:
                return {"collect": [{"source_id": "ted", "ok": True, "count": 5}]}

            def boom_watch(desk: TenderStore) -> dict:
                raise RuntimeError("telegram down")

            result = maybe_tick(
                store,
                force=True,
                now=1_700_000_100.0,
                collect_fn=fake_collect,
                watch_fn=boom_watch,
            )
            self.assertTrue(result["did_work"])
            self.assertEqual(result["loop"]["added"], 5)
            self.assertEqual(result["loop"].get("skipped_reason"), "watch_error")
            self.assertEqual(float(result["loop"].get("last_watch") or 0), 0)
            with patch("navin.tenders.watch.run_watch", return_value={"count": 1, "digest": "GO", "delivered": True}):
                followed = tick_watch(store)
            self.assertEqual(followed["count"], 1)

    def test_hung_collect_times_out_and_releases_the_desk(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = TenderStore(Path(tmp))
            store.save_profile(_ready_profile())
            start_loop(store, schedule={"kind": "daily", "hour": 9, "minute": 0}, now=1_700_000_000.0)
            started = Event()

            def hung(desk: TenderStore) -> dict:
                started.set()
                time.sleep(2)
                return {"collect": [{"source_id": "ted", "ok": True, "count": 1}]}

            with patch("navin.tenders.loop.MAX_HUNT_S", 0.25):
                result = maybe_tick(
                    store,
                    force=True,
                    now=1_700_000_100.0,
                    collect_fn=hung,
                    watch_fn=lambda desk: {"count": 0, "delivered": True},
                )
            self.assertTrue(started.wait(1))
            self.assertFalse(result["did_work"])
            self.assertIn("exceeded", result["reason"])
            self.assertNotEqual(store.load_loop().get("phase"), "hunt")
            with tenders_desk_lock(store, wait_s=0) as got:
                self.assertTrue(got)
            with patch("navin.tenders.watch.run_watch", return_value={"count": 0}) as watch:
                tick_watch(store)
            watch.assert_called()

    def test_expired_hunt_recovers_even_if_a_foreign_pid_is_alive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = TenderStore(Path(tmp))
            store.save_profile(_ready_profile())
            start_loop(store, schedule={"kind": "daily", "hour": 9, "minute": 0}, now=1_700_000_000.0)
            now = 1_700_000_100.0
            store.save_loop(
                {
                    **store.load_loop(),
                    "phase": "hunt",
                    "hunt_pid": 1,
                    "hunt_started_at": now - 9 * 60,
                    "enabled": True,
                }
            )
            from navin.tenders.loop import hunt_expired

            row = store.load_loop()
            self.assertTrue(hunt_expired(row, now=now))
            self.assertFalse(hunt_is_live(store, row))
            recovered = recover_stale_hunt(store, now=now)
            self.assertEqual(recovered.get("skipped_reason"), "stale_hunt")
            self.assertNotEqual(recovered.get("phase"), "hunt")

    def test_leftover_stop_intent_is_written_when_the_lock_is_free(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = TenderStore(Path(tmp))
            store.save_profile(_ready_profile())
            start_loop(store, schedule={"kind": "daily", "hour": 9, "minute": 0}, now=1_700_000_000.0)
            store.save_loop_intent({"enabled": False})
            self.assertTrue(store.load_loop()["enabled"])
            peeked = peek_loop(store, now=1_700_000_010.0)
            self.assertFalse(peeked["enabled"])
            self.assertEqual(peeked["phase"], "paused")
            self.assertFalse(store.loop_intent_path.exists())
            self.assertFalse(store.load_loop()["enabled"])

    def test_corrupt_loop_json_is_rebuilt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = TenderStore(Path(tmp))
            store.loop_path.write_text("[]", encoding="utf-8")
            row = store.load_loop()
            self.assertIsInstance(row, dict)
            self.assertFalse(row.get("enabled"))
            self.assertEqual(row.get("schedule", {}).get("kind"), "daily")

    def test_heartbeat_watch_timeout_does_not_stamp_last_watch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = TenderStore(Path(tmp))
            store.save_profile({"name": "Acme Bid"})
            store.save_loop({**store.load_loop(), "enabled": True, "phase": "idle", "last_watch": 0})

            def hung_watch(desk: TenderStore, *, send: bool = True) -> dict:
                time.sleep(2)
                return {"count": 1, "delivered": True}

            with patch("navin.tenders.heartbeat.HEARTBEAT_WATCH_S", 0.2):
                with patch("navin.tenders.watch.run_watch", side_effect=hung_watch):
                    skipped = tick_watch(store)
            self.assertEqual(skipped["skipped"], "watch_error")
            self.assertEqual(float(store.load_loop().get("last_watch") or 0), 0)


if __name__ == "__main__":
    unittest.main()
