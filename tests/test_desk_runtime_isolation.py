"""Desk loops / heartbeats / Vite must not take down the WebUI."""

from __future__ import annotations

import asyncio
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

DESKS = ("career", "leads", "marketing", "tenders", "trading")
HEAVY = (
    "harvest",
    "produce",
    "pipeline",
    "understand",
    "tick",
    "collect",
    "write",
    "send",
    "hunt",
)


class DeskRuntimeIsolationTest(unittest.TestCase):
    def test_one_failed_loop_does_not_cancel_siblings(self) -> None:
        cancelled = False

        async def boom() -> None:
            raise RuntimeError("marketing cycle exploded")

        async def stay() -> str:
            nonlocal cancelled
            try:
                await asyncio.sleep(0.05)
                return "ok"
            except asyncio.CancelledError:
                cancelled = True
                raise

        async def run() -> None:
            results = await asyncio.gather(boom(), stay(), return_exceptions=True)
            self.assertIsInstance(results[0], RuntimeError)
            self.assertEqual(results[1], "ok")

        asyncio.run(run())
        self.assertFalse(cancelled)

    def test_default_gather_would_kill_the_webui(self) -> None:
        """Document why desk loops must use return_exceptions=True."""
        cancelled = False

        async def boom() -> None:
            raise RuntimeError("marketing cycle exploded")

        async def stay() -> str:
            nonlocal cancelled
            try:
                await asyncio.sleep(0.2)
                return "ok"
            except asyncio.CancelledError:
                cancelled = True
                raise

        async def run() -> None:
            with self.assertRaises(RuntimeError):
                await asyncio.gather(boom(), stay())

        asyncio.run(run())
        self.assertTrue(cancelled)

    def test_gateway_isolates_all_five_desk_loops(self) -> None:
        commands = (ROOT / "navin/cli/commands.py").read_text(encoding="utf-8")
        self.assertIn("_run_isolated_desk_loops", commands)
        self.assertIn('name="navin-desk-loops"', commands)
        self.assertIn("await asyncio.gather(*desk_loop_tasks, return_exceptions=True)", commands)
        self.assertIn("Cron trading-loop tick failed", commands)
        self.assertIn("Cron marketing-loop tick failed", commands)
        for name in DESKS:
            self.assertIn(f"navin-{name}-loop", commands, name)
            self.assertIn(f"{name}_tick_inflight", commands, name)
            titled = name[:1].upper() + name[1:]
            self.assertIn(f"{titled} loop supervisor crashed - restarting in 5s", commands, name)

    def test_vite_never_spawns_heavy_desk_work(self) -> None:
        vite = (ROOT / "webui/vite.config.ts").read_text(encoding="utf-8")
        self.assertIn("deskActionGoesToGateway", vite)
        self.assertIn("DESK_VITE_HEAVY_ACTIONS", vite)
        self.assertIn("NAVIN_VITE_DESK_FALLBACK", vite)
        for action in HEAVY:
            self.assertIn(f'"{action}"', vite, action)
        self.assertGreaterEqual(vite.count("deskActionGoesToGateway(action)"), 5)
        self.assertIn("lookupTimer", vite)

    def test_heartbeat_desks_stay_watch_only_and_isolated(self) -> None:
        pulse = (ROOT / "navin/gateway/heartbeat_desks.py").read_text(encoding="utf-8")
        self.assertIn("one failure never skips the others", pulse)
        self.assertEqual(pulse.count("except Exception:"), 5)
        for banned in ("produce_assets", "harvest_live_site", "run_pipeline", "run_collect", "hunt_companies"):
            self.assertNotIn(banned, pulse, banned)
        marketing = (ROOT / "navin/marketing/heartbeat.py").read_text(encoding="utf-8")
        self.assertIn("HEARTBEAT_WATCH_S", marketing)
        self.assertNotIn("produce_assets", marketing)
        self.assertNotIn("harvest_live_site", marketing)
        self.assertNotIn("run_pipeline", marketing)
        loop = (ROOT / "navin/marketing/loop.py").read_text(encoding="utf-8")
        self.assertIn("run_growth_cycle", loop)
        self.assertNotIn("produce_assets", loop)
        self.assertNotIn("harvest_live_site", loop)

    def test_http_marketing_errors_do_not_escape(self) -> None:
        handler = (ROOT / "navin/webui/ws_http.py").read_text(encoding="utf-8")
        self.assertIn("marketing desk failed action={}", handler)
        self.assertIn("marketing asset failed", handler)
        self.assertIn("except MarketingError as exc:", handler)
        api = (ROOT / "navin/webui/marketing_desk_api.py").read_text(encoding="utf-8")
        self.assertIn("is_heartbeat_turn()", api)
        self.assertIn("HEARTBEAT_MARKETING_ACTIONS", api)


if __name__ == "__main__":
    unittest.main()
