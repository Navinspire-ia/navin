# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Career heartbeat: gateway tick, notify, tool/HTTP gate, loop stays free."""

from __future__ import annotations

import asyncio
import io
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from navin.agent.tools.career import CareerTool
from navin.agent.tools.context import RequestContext, request_context
from navin.career.errors import CareerError
from navin.career.heartbeat import (
    HEARTBEAT_CAREER_ACTIONS,
    heartbeat_prompt_note,
    profile_is_armed,
    tick_watch,
)
from navin.career.notify import deliver_alert
from navin.career.store import CareerStore
from navin.career.watch import run_watch
from navin.gateway.heartbeat_desks import tick_heartbeat_desks
from navin.webui.career_api import handle_career_action

ROOT = Path(__file__).resolve().parents[1]


def _hb_ctx() -> RequestContext:
    return RequestContext(
        channel="telegram",
        chat_id="1",
        session_key="heartbeat",
        metadata={"heartbeat": True},
    )


class CareerHeartbeatTickTest(unittest.TestCase):
    def test_cli_ticks_desks_before_skipping_empty_md_or_cli_target(self) -> None:
        src = (ROOT / "navin/cli/commands.py").read_text(encoding="utf-8")
        tick_at = src.index("tick_heartbeat_desks")
        empty_md = src.index("HEARTBEAT.md has no active tasks")
        cli_skip = src.index('if channel == "cli":')
        self.assertLess(tick_at, empty_md)
        self.assertLess(tick_at, cli_skip)
        self.assertIn("Heartbeat: desk ticks failed", src)
        self.assertIn("career_note", src)

    def test_prompt_note_stays_empty_unless_count_is_positive(self) -> None:
        self.assertEqual(heartbeat_prompt_note({"count": 0, "digest": "nope"}), "")
        self.assertEqual(heartbeat_prompt_note({"count": "0"}), "")
        self.assertEqual(heartbeat_prompt_note({"count": "x"}), "")
        self.assertEqual(heartbeat_prompt_note([]), "")
        note = heartbeat_prompt_note({"count": "3", "digest": "Match: A"})
        self.assertIn("watch.count=3", note)
        self.assertIn("Match: A", note)
        self.assertNotIn("\u2014", note)
        self.assertNotIn("\u2013", note)

    def test_score_79_is_silent_score_80_and_followup_fire_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = CareerStore(Path(tmp))
            store.save_profile({"titles": ["Data Engineer"], "wizard_complete": True})
            applied_at = time.time() - 4 * 86400
            store.upsert_opportunities(
                [
                    {
                        "id": "job-low",
                        "title": "Almost",
                        "company": "Skip Co",
                        "match_score": 79,
                        "stage": "matched",
                    },
                    {
                        "id": "job-hit",
                        "title": "Staff Data Engineer",
                        "company": "Paris Co",
                        "country": "FR",
                        "match_score": 80,
                        "stage": "matched",
                    },
                    {
                        "id": "job-j3",
                        "title": "Applied Engineer",
                        "company": "Follow Co",
                        "stage": "applied",
                        "applied_at": applied_at,
                    },
                ]
            )
            with patch("navin.career.notify.deliver_alert", return_value={"webui": True}):
                first = tick_watch(store)
                self.assertEqual(first["count"], 2)
                digest = first["digest"]
                self.assertIn("Staff Data Engineer", digest)
                self.assertIn("Follow-up j3", digest)
                self.assertNotIn("Almost", digest)
                self.assertTrue(heartbeat_prompt_note(first))
                second = tick_watch(store)
            self.assertEqual(second["count"], 0)
            self.assertEqual(heartbeat_prompt_note(second), "")

    def test_notify_crash_retries_on_the_next_tick(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = CareerStore(Path(tmp))
            store.save_profile({"titles": ["Data Engineer"]})
            store.upsert_opportunities(
                [
                    {
                        "id": "job-x",
                        "title": "Staff Data Engineer",
                        "match_score": 90,
                        "stage": "matched",
                    }
                ]
            )
            with patch("navin.career.notify.deliver_alert", side_effect=RuntimeError("bus down")):
                payload = run_watch(store)
            self.assertEqual(payload["count"], 1)
            self.assertEqual(payload["sent"], {})
            self.assertFalse(payload.get("delivered"))
            self.assertNotIn("match", store.get_opportunity("job-x").get("alerts_sent") or [])
            with patch("navin.career.notify.deliver_alert", return_value={"webui": True}):
                again = run_watch(store)
            self.assertEqual(again["count"], 1)
            self.assertTrue(again.get("delivered"))

    def test_heartbeat_never_calls_collect(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = CareerStore(Path(tmp))
            store.save_profile({"titles": ["Data Engineer"], "wizard_complete": True})
            with patch("navin.career.collect.collect") as collect:
                payload = tick_watch(store)
            collect.assert_not_called()
            self.assertEqual(payload["count"], 0)

    def test_heartbeat_skips_watch_when_the_loop_just_hunted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = CareerStore(Path(tmp))
            store.save_profile({"titles": ["Data Engineer"], "wizard_complete": True})
            store.upsert_opportunities(
                [
                    {
                        "id": "job-hot",
                        "title": "Staff Data Engineer",
                        "match_score": 92,
                        "stage": "matched",
                    }
                ]
            )
            store.save_loop({**store.load_loop(), "last_watch": time.time()})
            silent = tick_watch(store)
            self.assertEqual(silent["count"], 0)
            self.assertEqual(silent.get("skipped"), "loop_just_watched")


class CareerHeartbeatNotifyTest(unittest.TestCase):
    def test_closed_channels_never_raise_and_webui_uses_career_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = CareerStore(Path(tmp))
            store.save_profile(
                {
                    "titles": ["Data Engineer"],
                    "channels": {
                        "telegram": True,
                        "telegram_to": "",
                        "email": True,
                        "email_to": "not-an-email",
                        "whatsapp": True,
                        "whatsapp_to": "",
                        "teams": False,
                    },
                }
            )
            with patch("navin.career.notify.notify", return_value=True) as notifier:
                sent = deliver_alert(store, title="2 career alerts", detail="Match: A")
            notifier.assert_called_once()
            kwargs = notifier.call_args.kwargs
            self.assertEqual(kwargs["source"], "career")
            self.assertTrue(kwargs["key"].startswith("career-"))
            self.assertTrue(sent["webui_queued"])
            self.assertFalse(sent["webui"])
            self.assertFalse(sent["complete"])
            self.assertFalse(sent["telegram"])
            self.assertFalse(sent["email"])
            self.assertFalse(sent["whatsapp"])

    def test_telegram_chat_id_is_queued_without_claiming_delivery(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = CareerStore(Path(tmp))
            store.save_profile(
                {
                    "titles": ["Data Engineer"],
                    "channels": {"telegram": True, "telegram_to": "4242"},
                }
            )
            with (
                patch("navin.career.notify.notify", return_value=False),
                patch("navin.bus.alerts._cfg", return_value={"enabled": True, "token": "audit-fake-token"}),
                patch("navin.bus.alerts._enqueue", return_value=True) as outbound,
            ):
                sent = deliver_alert(store, title="1 career alert", detail="Match: A")
            outbound.assert_called_once()
            self.assertEqual(outbound.call_args.args[1].channel, "telegram")
            self.assertEqual(outbound.call_args.args[1].chat_id, "4242")
            self.assertEqual(sent["receipts"]["telegram"]["status"], "pending")
            self.assertFalse(sent["telegram"])
            self.assertFalse(sent["complete"])


class CareerHeartbeatGateTest(unittest.TestCase):
    def test_tool_and_http_refuse_the_same_mutating_aliases(self) -> None:
        self.assertEqual(
            HEARTBEAT_CAREER_ACTIONS,
            {"status", "dossier", "snapshot", "read", "book", "watch"},
        )
        with tempfile.TemporaryDirectory() as tmp:
            store = CareerStore(Path(tmp))
            store.save_profile({"titles": ["Data Engineer"], "wizard_complete": True})
            ctx = _hb_ctx()
            tool = CareerTool()
            refused = (
                "search",
                "collect",
                "ingest",
                "hits",
                "apply",
                "mission",
                "cv",
                "classify",
                "rescore",
                "start",
                "stop",
                "schedule",
                "tick",
            )
            with patch("navin.webui.career_api._store", return_value=store):
                with request_context(ctx):
                    for action in refused:
                        result = asyncio.run(tool.execute(action=action, id="job-x", hits="[]"))
                        self.assertTrue(getattr(result, "is_error", False), action)
                        self.assertIn("heartbeat", str(result).lower(), action)
                        with self.assertRaises(CareerError, msg=action):
                            handle_career_action(action, {"id": "job-x", "jobs": []})
                    watch = asyncio.run(tool.execute(action="watch"))
                    snap = handle_career_action("snapshot")
                    book = asyncio.run(tool.execute(action="book", file="book"))
            self.assertFalse(getattr(watch, "is_error", False), watch)
            self.assertIn("watch", watch)
            self.assertEqual(watch["watch"]["count"], 0)
            self.assertIn("profile", snap)
            self.assertIsInstance(book, str)

    def test_loop_turn_can_search_heartbeat_cannot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = CareerStore(Path(tmp))
            store.save_profile({"titles": ["Data Engineer"], "wizard_complete": True})
            tool = CareerTool()
            loop_ctx = RequestContext(
                channel="webui",
                chat_id="career-1",
                session_key="webui:career-1",
                metadata={"product_module": "career"},
            )
            with patch("navin.webui.career_api._store", return_value=store):
                with patch("navin.career.desk.collect", return_value={"added": 0, "query": "x"}):
                    with request_context(loop_ctx):
                        loop_search = asyncio.run(tool.execute(action="search"))
                    with request_context(_hb_ctx()):
                        hb_search = asyncio.run(tool.execute(action="search"))
            self.assertFalse(getattr(loop_search, "is_error", False), loop_search)
            self.assertTrue(getattr(hb_search, "is_error", False))

    def test_tenders_failure_does_not_skip_career_tick(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = CareerStore(Path(tmp))
            store.save_profile({"titles": ["Data Engineer"]})
            store.upsert_opportunities(
                [
                    {
                        "id": "job-ok",
                        "title": "Staff Data Engineer",
                        "match_score": 90,
                        "stage": "matched",
                        "company": "Paris Co",
                        "country": "FR",
                    }
                ]
            )
            with patch(
                "navin.tenders.heartbeat.tick_watch",
                side_effect=RuntimeError("tenders down"),
            ):
                with patch("navin.leads.heartbeat.tick_watch", return_value=None):
                    with patch("navin.trading.heartbeat.tick_watch", return_value=None):
                        with patch("navin.marketing.heartbeat.tick_watch", return_value=None):
                            with patch(
                                "navin.career.notify.deliver_alert",
                                return_value={"webui": True},
                            ):
                                note = tick_heartbeat_desks(career_store=store)
            self.assertIn("watch.count=1", note)
            self.assertIn("Staff Data Engineer", note)
            with patch("navin.tenders.heartbeat.tick_watch", return_value=None):
                with patch("navin.leads.heartbeat.tick_watch", return_value=None):
                    with patch("navin.trading.heartbeat.tick_watch", return_value=None):
                        with patch("navin.marketing.heartbeat.tick_watch", return_value=None):
                            silent = tick_heartbeat_desks(career_store=store)
            self.assertEqual(silent, "")

    def test_career_tick_failure_returns_empty_note(self) -> None:
        with patch("navin.career.heartbeat.tick_watch", side_effect=RuntimeError("career down")):
            with patch("navin.tenders.heartbeat.tick_watch", return_value=None):
                with patch("navin.leads.heartbeat.tick_watch", return_value=None):
                    with patch("navin.marketing.heartbeat.tick_watch", return_value=None):
                        with patch("navin.trading.heartbeat.tick_watch", return_value=None):
                            self.assertEqual(tick_heartbeat_desks(), "")

    def test_j7_fires_after_j3_was_marked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = CareerStore(Path(tmp))
            store.save_profile({"titles": ["Data Engineer"]})
            store.upsert_opportunities(
                [
                    {
                        "id": "job-relance",
                        "title": "Applied Engineer",
                        "stage": "applied",
                        "applied_at": time.time() - 4 * 86400,
                    }
                ]
            )
            with patch("navin.career.notify.deliver_alert", return_value={"webui": True}):
                first = tick_watch(store)
                self.assertEqual(first["count"], 1)
                self.assertIn("Follow-up j3", first["digest"])
                store.update_opportunity("job-relance", {"applied_at": time.time() - 8 * 86400})
                second = tick_watch(store)
                self.assertEqual(second["count"], 1)
                self.assertIn("Follow-up j7", second["digest"])
                self.assertEqual(tick_watch(store)["count"], 0)

    def test_followup_uses_updated_at_when_applied_at_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = CareerStore(Path(tmp))
            store.save_profile({"titles": ["Data Engineer"]})
            store.upsert_opportunities(
                [
                    {
                        "id": "job-old",
                        "title": "Applied Engineer",
                        "stage": "applied",
                        "updated_at": time.time() - 4 * 86400,
                    }
                ]
            )
            payload = tick_watch(store)
            self.assertEqual(payload["count"], 1)
            self.assertIn("Follow-up j3", payload["digest"])

    def test_heartbeat_md_career_section_matches_the_template(self) -> None:
        live = (ROOT / ".navin/HEARTBEAT.md").read_text(encoding="utf-8")
        template = (ROOT / "navin/templates/HEARTBEAT.md").read_text(encoding="utf-8")
        marker = "### Career matches and follow-ups"
        self.assertIn(marker, live)
        self.assertIn(marker, template)
        live_block = live.split(marker, 1)[1].split("### ", 1)[0]
        template_block = template.split(marker, 1)[1].split("### ", 1)[0]
        self.assertEqual(live_block, template_block)
        self.assertIn("already ran", live_block)
        self.assertIn("Never search", live_block)

    def test_heartbeat_watch_error_does_not_block(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = CareerStore(Path(tmp))
            store.save_profile({"titles": ["Data Engineer"]})

            def hang(_store: CareerStore, **_kwargs: object) -> dict[str, object]:
                time.sleep(2)
                return {"count": 0, "events": [], "digest": "", "sent": {}}

            started = time.monotonic()
            with (
                patch("navin.career.heartbeat.HEARTBEAT_WATCH_S", 0.05),
                patch("navin.career.watch.run_watch", hang),
            ):
                payload = tick_watch(store)
            self.assertLess(time.monotonic() - started, 1.0)
            self.assertEqual(payload["skipped"], "watch_error")
            self.assertEqual(payload["count"], 0)

    def test_desk_cli_watch_is_the_same_tick(self) -> None:
        from navin.career.desk_cli import main

        with tempfile.TemporaryDirectory() as tmp:
            store = CareerStore(Path(tmp))
            store.save_profile({"titles": ["Data Engineer"]})
            store.upsert_opportunities(
                [
                    {
                        "id": "job-cli",
                        "title": "Staff Data Engineer",
                        "match_score": 90,
                        "stage": "matched",
                    }
                ]
            )
            buf = io.StringIO()
            with (
                patch("navin.webui.career_api._store", return_value=store),
                patch("navin.career.notify.deliver_alert", return_value={"webui": True}),
                patch("sys.argv", ["navin.career.desk_cli", "watch"]),
                patch("sys.stdin", io.StringIO("{}")),
                patch("sys.stdout", buf),
            ):
                code = main()
            self.assertEqual(code, 0)
            payload = json.loads(buf.getvalue())
            self.assertEqual(payload["watch"]["count"], 1)
            self.assertIn("Staff Data Engineer", payload["watch"]["digest"])
            self.assertEqual(tick_watch(store)["count"], 0)

    def test_alert_needs_recipient_and_running_dispatcher(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict("navin.bus.alerts._RUNTIME", {}, clear=True), patch(
            "navin.bus.alerts._cfg", return_value={"enabled": True, "token": "audit-fake-token"}
        ):
            store = CareerStore(Path(tmp))
            store.save_profile({"channels": {"telegram": True, "telegram_to": ""}})
            missing = deliver_alert(store, title="Audit A", detail="Audit message")
            self.assertFalse(missing["complete"])
            self.assertEqual(missing["receipts"]["telegram"]["error"], "destination_missing")
            store.save_profile({"channels": {"telegram": True, "telegram_to": "99"}})
            closed = deliver_alert(store, title="Audit B", detail="Audit message")
            self.assertFalse(closed["telegram"])
            self.assertEqual(closed["receipts"]["telegram"]["error"], "gateway_not_running")

    def test_armed_profile_matches_watch_skip_rule(self) -> None:
        self.assertFalse(profile_is_armed(None))
        self.assertFalse(profile_is_armed({"titles": ["", "  "]}))
        self.assertTrue(profile_is_armed({"titles": ["  Data  "]}))
        with tempfile.TemporaryDirectory() as tmp:
            store = CareerStore(Path(tmp))
            store.save_profile({"wizard_complete": True, "titles": []})
            self.assertIsNotNone(tick_watch(store))


if __name__ == "__main__":
    unittest.main()
