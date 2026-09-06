"""Wiring for the Trading studio module."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from navin.command.builtin import (
    _DELIVERY_WORKFLOWS,
    _HTML_REPORT_WORKFLOWS,
    _TRACKED_WORKFLOWS,
    _WORKFLOW_BRIEFS,
    BUILTIN_COMMAND_SPECS,
    builtin_command_palette,
)
from navin.command.modules import (
    CODE_HIDDEN_COMMANDS,
    VALID_PRODUCT_MODULES,
    exclusive_studio_skills,
    is_command_allowed_for_module,
)
from navin.trading.errors import TradingError
from navin.trading.store import TradingStore
from navin.webui.trading_api import handle_trading_action

ROOT = Path(__file__).resolve().parents[1]


class TradingProductModuleTest(unittest.TestCase):
    def test_module_registered(self) -> None:
        self.assertIn("trading", VALID_PRODUCT_MODULES)
        self.assertIn("/trading", CODE_HIDDEN_COMMANDS)
        self.assertTrue(is_command_allowed_for_module("/trading", "trading"))
        self.assertFalse(is_command_allowed_for_module("/trading", "code"))
        self.assertIn("/trading", _HTML_REPORT_WORKFLOWS)
        self.assertIn("/trading", _DELIVERY_WORKFLOWS)
        self.assertIn("/trading", _TRACKED_WORKFLOWS)

    def test_palette_and_brief(self) -> None:
        specs = {spec.command: spec for spec in BUILTIN_COMMAND_SPECS}
        self.assertIn("/trading", specs)
        title, skills, brief = _WORKFLOW_BRIEFS["/trading"]
        self.assertIn("Trading", title)
        self.assertIn("trading-agent", skills)
        self.assertIn("trading", brief)
        self.assertIn("trading action=start", brief)
        self.assertIn("Do not create a chat cron", brief)
        self.assertIn("trading action=watch", brief)
        trading = {row["command"] for row in builtin_command_palette("trading")}
        self.assertIn("/trading", trading)
        self.assertNotIn("/campaign", trading)
        exclusive = exclusive_studio_skills()
        self.assertIn("trading-agent", exclusive.get("trading", set()))
        self.assertIn("nft-collector", exclusive.get("trading", set()))
        self.assertIn("real-estate-investor", exclusive.get("trading", set()))
        self.assertIn("global-equities", exclusive.get("trading", set()))
        self.assertIn("nft-collector", skills)
        self.assertTrue((ROOT / "navin/skills/nft-collector/SKILL.md").is_file())
        self.assertTrue((ROOT / "navin/skills/real-estate-investor/SKILL.md").is_file())
        self.assertTrue((ROOT / "navin/skills/global-equities/SKILL.md").is_file())

    def test_shell_opens_trading_first(self) -> None:
        app = (ROOT / "webui/src/App.tsx").read_text(encoding="utf-8")
        sidebar = (ROOT / "webui/src/components/Sidebar.tsx").read_text(encoding="utf-8")
        self.assertIn('path === "/trading"', app)
        self.assertIn("TradingWorkspace", app)
        self.assertIn('"trading"', sidebar)
        self.assertLess(sidebar.find('"trading"'), sidebar.find('"leads"'))
        self.assertIn("onOpenTradingStudio", sidebar)
        http = (ROOT / "navin/webui/ws_http.py").read_text(encoding="utf-8")
        self.assertIn("async def _handle_trading", http)
        self.assertIn('if exc.message != "missing file content"', http)
        api = (ROOT / "webui/src/lib/trading-api.ts").read_text(encoding="utf-8")
        self.assertIn('apiBodyHeaders("{}")', api)
        desk = (ROOT / "webui/src/components/studio/trading/TradingWorkspace.tsx").read_text(encoding="utf-8")
        self.assertIn('id: "home"', desk)
        self.assertIn('useState<Pane>("home")', desk)
        self.assertLess(desk.find('aria-label={tx("panesAria"'), desk.find('pane === "home"'))
        self.assertIn("function MandatePane", desk)
        self.assertIn("TradingKpiGrid", desk)
        self.assertIn("buildTradingKpis", desk)
        kpis = (ROOT / "webui/src/components/studio/trading/TradingKpis.tsx").read_text(encoding="utf-8")
        self.assertIn("data-testid=\"trading-kpi-grid\"", kpis)
        self.assertNotIn("motion.", kpis)
        self.assertIn("activateStrategy", desk)
        self.assertIn('run("activate"', desk)
        self.assertIn("TradingMandate", api)


class TradingApiTest(unittest.TestCase):
    def test_snapshot_and_compile_stay_on_disk(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = TradingStore(Path(tmp))
            with patch("navin.webui.trading_api._store", return_value=store):
                snap = handle_trading_action("snapshot")
                self.assertIn("portfolio", snap)
                self.assertEqual(snap["loop"]["enabled"], False)
                compiled = handle_trading_action(
                    "strategy",
                    {"brief": "Nasdaq 100 growth > 15%. Stop 5%. Max 3%."},
                )
                self.assertEqual(compiled["strategy"]["universe"], "NASDAQ100")
                self.assertEqual(compiled["strategy"]["stop_loss"], 5.0)
                stopped = handle_trading_action("stop")
                self.assertFalse(stopped["loop"]["enabled"])
                status = handle_trading_action("status")
                self.assertIn("mandate", status)
                self.assertIn("portfolio", status)
                journal = handle_trading_action("journal")
                self.assertIn("journal", journal)
                self.assertIn("loop", journal)

    def test_unknown_action_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = TradingStore(Path(tmp))
            with patch("navin.webui.trading_api._store", return_value=store):
                with self.assertRaises(TradingError) as ctx:
                    handle_trading_action("explode")
                self.assertEqual(ctx.exception.status, 400)

    def test_watchlist_and_risk_leverage_stay_typed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = TradingStore(Path(tmp))
            with patch("navin.webui.trading_api._store", return_value=store):
                saved = handle_trading_action("watchlist", {"symbols": ["NVDA", "aapl"]})
                self.assertEqual(saved["watchlist"], ["NVDA", "AAPL"])
                settings = handle_trading_action("settings", {"risk": {"leverage": 1}})
                self.assertIs(settings["settings"]["risk"]["leverage"], True)
                settings = handle_trading_action("settings", {"risk": {"leverage": 0}})
                self.assertIs(settings["settings"]["risk"]["leverage"], False)
                with self.assertRaises(TradingError) as ctx:
                    handle_trading_action("approve", {"id": ""})
                self.assertEqual(ctx.exception.status, 404)
                with self.assertRaises(TradingError):
                    handle_trading_action("research", {})
                first = store.active_strategy()
                extra = store.upsert_strategy(
                    {"name": "alt", "brief": "Hold cash.", "skill": "trading-agent"},
                    make_active=False,
                )
                self.assertFalse(extra.get("active"))
                activated = handle_trading_action("activate", {"id": extra["id"]})
                self.assertEqual(activated["strategy"]["id"], extra["id"])
                self.assertTrue(activated["strategy"]["active"])
                self.assertNotEqual(store.active_strategy()["id"], first["id"])
                with self.assertRaises(TradingError):
                    handle_trading_action("activate", {"id": ""})


if __name__ == "__main__":
    unittest.main()

