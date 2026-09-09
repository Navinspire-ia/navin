# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from navin.gateway.heartbeat_desks import tick_heartbeat_desks
from navin.marketing.content import generate_content
from navin.marketing.growth import ingest_metrics
from navin.agent.tools.marketing import MarketingTool
from navin.marketing.heartbeat import HEARTBEAT_MARKETING_ACTIONS, heartbeat_prompt_note, tick_watch
from navin.marketing.positioning import build_positioning
from navin.marketing.store import MarketingStore
from navin.marketing.understand import understand_product
from navin.webui.marketing_desk_api import handle_marketing_action


class MarketingHeartbeatTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = MarketingStore(Path(self.tmp.name))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_unarmed_watch_is_none(self) -> None:
        self.assertIsNone(tick_watch(self.store))
        self.assertEqual(heartbeat_prompt_note(None), "")
        self.assertEqual(heartbeat_prompt_note({"count": 0}), "")

    def test_watch_reports_a_winner(self) -> None:
        understand_product(self.store, extras={"name": "InvoiceAI"})
        build_positioning(self.store)
        rows = generate_content(self.store, channels=["linkedin", "x"])
        ingest_metrics(
            self.store,
            {
                "by_content": {
                    rows[0]["id"]: {"views": 400, "clicks": 80, "conversions": 20},
                    rows[1]["id"]: {"views": 300, "clicks": 6, "conversions": 1},
                }
            },
        )
        payload = tick_watch(self.store)
        assert payload is not None
        self.assertGreaterEqual(payload["count"], 1)
        note = heartbeat_prompt_note(payload)
        self.assertIn("watch.count=", note)
        self.assertIn("Never publish", note)

    def test_watch_winner_is_not_realerted(self) -> None:
        understand_product(self.store, extras={"name": "InvoiceAI"})
        build_positioning(self.store)
        rows = generate_content(self.store, channels=["linkedin", "x"])
        ingest_metrics(
            self.store,
            {
                "by_content": {
                    rows[0]["id"]: {"views": 400, "clicks": 80, "conversions": 20},
                    rows[1]["id"]: {"views": 300, "clicks": 6, "conversions": 1},
                }
            },
        )
        with patch("navin.marketing.notify.deliver_alert", return_value={"webui": True}):
            first = tick_watch(self.store)
            self.store.save_loop({**self.store.load_loop(), "last_watch": 0})
            second = tick_watch(self.store)
        assert first is not None
        self.assertGreaterEqual(first["count"], 1)
        self.assertTrue(first.get("delivered"))
        assert second is not None
        self.assertEqual(second["count"], 0)

    def test_tool_refuses_writes_on_heartbeat(self) -> None:
        from navin.agent.tools.context import RequestContext, request_context

        ctx = RequestContext(
            channel="telegram",
            chat_id="1",
            session_key="heartbeat",
            metadata={"heartbeat": True},
        )
        tool = MarketingTool()
        with request_context(ctx):
            import asyncio

            result = asyncio.run(tool.execute(action="pipeline", goal="go"))
        self.assertTrue(getattr(result, "is_error", False))
        self.assertIn("heartbeat", str(result).lower())
        self.assertEqual(HEARTBEAT_MARKETING_ACTIONS, {"status", "snapshot", "watch"})

    def test_api_refuses_writes_on_heartbeat(self) -> None:
        with patch("navin.agent.tools.context.is_heartbeat_turn", return_value=True):
            with patch("navin.webui.marketing_desk_api._store", return_value=self.store):
                with self.assertRaises(Exception) as ctx:
                    handle_marketing_action("pipeline", {"goal": "go"})
        self.assertIn("heartbeat", str(ctx.exception).lower())

    def test_corrupt_last_watch_does_not_crash(self) -> None:
        understand_product(self.store, extras={"name": "InvoiceAI"})
        self.store.save_loop({**self.store.load_loop(), "last_watch": "not-a-time"})
        payload = tick_watch(self.store)
        self.assertIsNotNone(payload)
        self.assertIsInstance(payload["count"], int)

    def test_watch_crash_returns_empty_payload(self) -> None:
        understand_product(self.store, extras={"name": "InvoiceAI"})
        with patch("navin.marketing.watch.run_watch", side_effect=RuntimeError("disk full")):
            payload = tick_watch(self.store)
        self.assertEqual(payload["count"], 0)
        self.assertEqual(payload.get("skipped"), "error")

    def test_tick_heartbeat_desks_isolates_marketing(self) -> None:
        payload = {"count": 1, "digest": "linkedin post converts 4.8x"}
        with patch("navin.tenders.heartbeat.tick_watch", return_value=None):
            with patch("navin.career.heartbeat.tick_watch", return_value=None):
                with patch("navin.leads.heartbeat.tick_watch", return_value=None):
                    with patch("navin.marketing.heartbeat.tick_watch", return_value=payload):
                        note = tick_heartbeat_desks(marketing_store=self.store)
        self.assertIn("watch.count=1", note)
        self.assertIn("4.8x", note)

        with patch("navin.tenders.heartbeat.tick_watch", return_value=None):
            with patch("navin.career.heartbeat.tick_watch", return_value=None):
                with patch("navin.leads.heartbeat.tick_watch", return_value=None):
                    with patch(
                        "navin.marketing.heartbeat.tick_watch",
                        side_effect=RuntimeError("marketing down"),
                    ):
                        note = tick_heartbeat_desks()
        self.assertEqual(note, "")


if __name__ == "__main__":
    unittest.main()
