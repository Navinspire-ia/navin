"""Wiring and HTTP/tool surface for the Marketing Agent OS desk."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from navin.agent.model_routes import (
    PRODUCT_MODULE_ROUTE_ROLES,
    WORKFLOW_ROUTE_ROLES,
    product_module_role,
    workflow_role_for_content,
)
from navin.agent.tools.marketing import MarketingTool
from navin.command.builtin import (
    _DELIVERY_WORKFLOWS,
    _HTML_REPORT_WORKFLOWS,
    _WORKFLOW_BRIEFS,
    BUILTIN_COMMAND_SPECS,
    builtin_command_palette,
)
from navin.command.modules import (
    CODE_HIDDEN_COMMANDS,
    STUDIO_OWNED_TOOLS,
    VALID_PRODUCT_MODULES,
    extra_denied_tools_for_module,
    is_command_allowed_for_module,
)
from navin.marketing.errors import MarketingError
from navin.marketing.store import MarketingStore
from navin.webui.marketing_desk_api import handle_marketing_action

ROOT = Path(__file__).resolve().parents[1]


class MarketingProductModuleTest(unittest.TestCase):
    def test_module_registered(self) -> None:
        self.assertIn("marketing", VALID_PRODUCT_MODULES)
        self.assertIn("/marketing", CODE_HIDDEN_COMMANDS)
        self.assertEqual(STUDIO_OWNED_TOOLS["marketing"], "marketing")
        self.assertTrue(is_command_allowed_for_module("/marketing", "marketing"))
        self.assertTrue(is_command_allowed_for_module("/campaign", "marketing"))
        self.assertTrue(is_command_allowed_for_module("/montage", "marketing"))
        self.assertFalse(is_command_allowed_for_module("/marketing", "code"))
        self.assertFalse(is_command_allowed_for_module("/marketing", "trading"))
        self.assertIn("/marketing", _HTML_REPORT_WORKFLOWS)
        self.assertIn("/marketing", _DELIVERY_WORKFLOWS)
        denied = extra_denied_tools_for_module("marketing")
        self.assertIn("trading", denied)
        self.assertNotIn("marketing", denied)
        self.assertIn("marketing", extra_denied_tools_for_module("career"))
        self.assertIn("marketing", extra_denied_tools_for_module("trading"))
        self.assertEqual(WORKFLOW_ROUTE_ROLES.get("/marketing"), "docs")
        self.assertEqual(workflow_role_for_content("/marketing Lance le marketing"), "docs")
        self.assertEqual(PRODUCT_MODULE_ROUTE_ROLES.get("marketing"), "docs")
        self.assertEqual(product_module_role("marketing"), "docs")
        self.assertEqual(product_module_role("montage"), "docs")

    def test_palette_and_brief(self) -> None:
        specs = {spec.command: spec for spec in BUILTIN_COMMAND_SPECS}
        self.assertIn("/marketing", specs)
        title, skills, brief = _WORKFLOW_BRIEFS["/marketing"]
        self.assertIn("Marketing", title)
        self.assertIn("marketing-strategist", skills)
        self.assertIn("growth-marketing", skills)
        self.assertIn("marketing action=status", brief)
        self.assertIn("navin.marketing.desk_cli", brief)
        self.assertIn("Do not create a chat cron", brief)
        self.assertIn("Never publish", brief)
        campaign_brief = _WORKFLOW_BRIEFS["/campaign"][2]
        self.assertIn("marketing action=status", campaign_brief)
        self.assertIn("navin.marketing.desk_cli", campaign_brief)
        palette = {row["command"] for row in builtin_command_palette("marketing")}
        self.assertIn("/marketing", palette)
        self.assertIn("/campaign", palette)
        self.assertNotIn("/trading", palette)

    def test_shell_opens_marketing_desk(self) -> None:
        app = (ROOT / "webui/src/App.tsx").read_text(encoding="utf-8")
        sidebar = (ROOT / "webui/src/components/Sidebar.tsx").read_text(encoding="utf-8")
        self.assertIn('path === "/marketing"', app)
        self.assertIn("MarketingWorkspace", app)
        self.assertIn('"marketing"', sidebar)
        self.assertIn("onOpenMarketingStudio", sidebar)
        http = (ROOT / "navin/webui/ws_http.py").read_text(encoding="utf-8")
        self.assertIn("async def _handle_marketing_desk", http)
        self.assertIn('if re.match(r"^/api/marketing$"', http)
        api = (ROOT / "webui/src/lib/marketing-api.ts").read_text(encoding="utf-8")
        self.assertIn('apiBodyHeaders("{}")', api)
        desk = (ROOT / "webui/src/components/studio/marketing/MarketingWorkspace.tsx").read_text(
            encoding="utf-8"
        )
        self.assertIn('id: "home"', desk)
        self.assertIn('id: "qa"', desk)
        self.assertIn("MarketingQA", desk)
        self.assertIn('id: "settings"', desk)
        self.assertIn("MarketingKpiGrid", desk)
        self.assertIn("MarketingScene", desk)
        self.assertIn('run("pipeline"', desk)
        self.assertIn('run("launch"', desk)
        self.assertIn("source_kind", desk)
        self.assertIn("recentProjects", desk)
        self.assertIn("Bind and understand", desk)
        self.assertIn("Harvest the live site", desk)
        self.assertIn("Generate brand kit", desk)
        self.assertIn("socialCalendar", desk)
        self.assertIn('"harvest"', desk)
        self.assertIn('"produce"', desk)
        self.assertIn('pack: "brand"', desk)
        self.assertIn('pack: "posts"', desk)
        self.assertIn("ingestRanking", desk)
        self.assertIn("by_content", desk)
        self.assertIn('run("metrics"', desk)
        self.assertIn('run("competitor"', desk)
        self.assertIn('run("vision"', desk)
        cli = (ROOT / "navin/cli/commands.py").read_text(encoding="utf-8")
        self.assertIn("navin-marketing-loop", cli)
        self.assertIn("marketing-loop", cli)
        heartbeat = (ROOT / "navin/templates/HEARTBEAT.md").read_text(encoding="utf-8")
        self.assertIn("Marketing winners", heartbeat)
        self.assertIn("marketing action=watch", heartbeat)
        self.assertIn("Never publish", heartbeat)


class MarketingApiTest(unittest.TestCase):
    def test_pipeline_then_loop_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = MarketingStore(Path(tmp))
            with patch("navin.webui.marketing_desk_api._store", return_value=store):
                snap = handle_marketing_action("snapshot")
                self.assertFalse(snap["armed"])
                self.assertEqual(snap["kpis"]["campaigns"], 0)
                handle_marketing_action(
                    "pipeline",
                    {
                        "product": {
                            "name": "InvoiceAI",
                            "one_liner": "Invoice capture for SMBs",
                        },
                        "goal": "1000 inscriptions",
                        "days": 30,
                        "signups": 1000,
                    },
                )
                desk = handle_marketing_action("status")
                self.assertTrue(desk["armed"])
                self.assertEqual(desk["product"]["name"], "InvoiceAI")
                self.assertGreaterEqual(desk["kpis"]["campaigns"], 1)
                self.assertGreaterEqual(desk["kpis"]["content"], 4)
                self.assertEqual(desk["launch"]["status"], "ready")
                campaign_id = desk["campaigns"][0]["id"]
                approved = handle_marketing_action("approve", {"id": campaign_id})
                self.assertEqual(approved["campaigns"][0]["status"], "approved")
                started = handle_marketing_action(
                    "start",
                    {
                        "schedule": {"kind": "daily", "hour": 9, "minute": 0, "tz": "UTC"},
                        "run_now": True,
                    },
                )
                self.assertTrue(started["loop"]["enabled"])
                self.assertIn("loop_tick", started)
                stopped = handle_marketing_action("stop")
                self.assertFalse(stopped["loop"]["enabled"])

    def test_bad_days_is_a_400_not_a_500(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = MarketingStore(Path(tmp))
            with patch("navin.webui.marketing_desk_api._store", return_value=store):
                with self.assertRaises(MarketingError) as ctx:
                    handle_marketing_action("plan", {"days": "abc"})
                self.assertEqual(ctx.exception.status, 400)
                self.assertIn("days", ctx.exception.message)

    def test_unknown_action_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = MarketingStore(Path(tmp))
            with patch("navin.webui.marketing_desk_api._store", return_value=store):
                with self.assertRaises(MarketingError) as ctx:
                    handle_marketing_action("explode")
                self.assertIn("unknown", ctx.exception.message)

    def test_brand_and_metrics_then_improve(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = MarketingStore(Path(tmp))
            with patch("navin.webui.marketing_desk_api._store", return_value=store):
                handle_marketing_action("brand", {"company": "InvoiceAI", "tone": "direct"})
                handle_marketing_action(
                    "understand",
                    {"product": {"name": "InvoiceAI", "one_liner": "Invoice capture"}},
                )
                handle_marketing_action("content", {"channels": ["linkedin", "x", "email"]})
                rows = store.load_content()
                handle_marketing_action(
                    "metrics",
                    {
                        "signups": 12,
                        "by_content": {
                            rows[0]["id"]: {"views": 200, "clicks": 80, "conversions": 20},
                            rows[1]["id"]: {"views": 180, "clicks": 8, "conversions": 1},
                            rows[2]["id"]: {"views": 160, "clicks": 6, "conversions": 1},
                        },
                    },
                )
                improved = handle_marketing_action("improve")
                self.assertGreaterEqual(len(improved.get("growth", {}).get("winners") or []), 1)
                self.assertGreaterEqual(improved["kpis"]["winners"], 1)

    def test_empty_brand_patch_does_not_wipe_company(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = MarketingStore(Path(tmp))
            with patch("navin.webui.marketing_desk_api._store", return_value=store):
                handle_marketing_action("brand", {"company": "InvoiceAI", "tone": "direct"})
                with self.assertRaises(MarketingError):
                    handle_marketing_action("brand", {"company": ""})
                desk = handle_marketing_action("status")
                self.assertEqual(desk["brand"]["company"], "InvoiceAI")
                with self.assertRaises(MarketingError):
                    handle_marketing_action("competitor", {"note": "no name"})
                handle_marketing_action("competitor", {"name": "Pennylane", "note": "pricing"})
                self.assertEqual(handle_marketing_action("status")["competitors"][0]["name"], "Pennylane")


class MarketingToolTest(unittest.TestCase):
    def test_status_is_read_only(self) -> None:
        tool = MarketingTool()
        self.assertEqual(tool.name, "marketing")
        self.assertTrue(tool.call_read_only({"action": "status"}))
        self.assertTrue(tool.call_read_only({"action": "watch"}))
        self.assertFalse(tool.call_read_only({"action": "pipeline"}))

    def test_execute_status_mentions_the_desk(self) -> None:
        import asyncio

        async def _run() -> object:
            return await MarketingTool().execute(action="status")

        with tempfile.TemporaryDirectory() as tmp:
            store = MarketingStore(Path(tmp))
            store.save_product({"name": "InvoiceAI"})
            with patch("navin.webui.marketing_desk_api._store", return_value=store):
                result = asyncio.run(_run())
        self.assertIn("Marketing desk", str(result))
        self.assertIn("InvoiceAI", str(result))

    def test_start_accepts_schedule_json(self) -> None:
        import asyncio

        async def _run() -> object:
            return await MarketingTool().execute(
                action="start",
                schedule='{"kind":"daily","hour":9,"minute":0,"tz":"UTC"}',
                run_now="false",
            )

        with tempfile.TemporaryDirectory() as tmp:
            store = MarketingStore(Path(tmp))
            store.save_product({"name": "InvoiceAI"})
            with patch("navin.webui.marketing_desk_api._store", return_value=store):
                result = asyncio.run(_run())
        self.assertFalse(getattr(result, "is_error", False), str(result))
        self.assertTrue(result["loop"]["enabled"])
        self.assertEqual(result["loop"]["schedule"]["hour"], 9)

    def test_metrics_without_signups_does_not_invent_1000(self) -> None:
        import asyncio

        async def _run() -> object:
            return await MarketingTool().execute(action="metrics", traffic="40")

        with tempfile.TemporaryDirectory() as tmp:
            store = MarketingStore(Path(tmp))
            store.save_product({"name": "InvoiceAI"})
            store.save_analytics({"signups": 12, "traffic": 10, "leads": 2})
            with patch("navin.webui.marketing_desk_api._store", return_value=store):
                result = asyncio.run(_run())
        self.assertFalse(getattr(result, "is_error", False), str(result))
        self.assertEqual(result["analytics"]["signups"], 12)
        self.assertEqual(result["analytics"]["traffic"], 40)


if __name__ == "__main__":
    unittest.main()
