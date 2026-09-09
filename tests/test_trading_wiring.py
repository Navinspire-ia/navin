# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Trading start/stop/heartbeat: one store for Tauri, terminal, and the agent."""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from navin.agent.tools.context import RequestContext, request_context
from navin.agent.tools.trading import TradingTool
from navin.trading.desk_cli import HELP, main
from navin.trading.store import TradingStore
from navin.webui.trading_api import handle_trading_action

ROOT = Path(__file__).resolve().parents[1]


class TradingSurfaceWiringTest(unittest.TestCase):
    def test_tauri_keeps_trading_hash_in_the_webview(self) -> None:
        rust = (ROOT / "desktop/src-tauri/src/main.rs").read_text(encoding="utf-8")
        self.assertIn("is_desktop_app_url", rust)
        self.assertIn("http://tauri.localhost/#/trading", rust)
        self.assertIn("https://tauri.localhost/#/trading", rust)
        self.assertIn("https://tauri.localhost/#/trading?chat=websocket:1", rust)
        self.assertIn("http://127.0.0.1:8766/#/trading?chat=websocket:1", rust)
        self.assertIn("tauri://localhost/#/trading", rust)
        self.assertIn("tauri://localhost/#/trading?chat=websocket:1", rust)
        app = (ROOT / "webui/src/App.tsx").read_text(encoding="utf-8")
        self.assertIn('path === "/trading"', app)
        api = (ROOT / "webui/src/lib/trading-api.ts").read_text(encoding="utf-8")
        self.assertIn("/api/trading?action=", api)
        self.assertIn('TRADING_DESK_HASH = "#/trading"', api)
        http = (ROOT / "navin/webui/ws_http.py").read_text(encoding="utf-8")
        self.assertIn("handle_trading_action", http)
        vite = (ROOT / "webui/vite.config.ts").read_text(encoding="utf-8")
        self.assertIn('"-m", "navin.trading.desk_cli"', vite)

    def test_trading_desk_is_identical_on_linux_windows_and_macos(self) -> None:
        opener = json.loads(
            (ROOT / "desktop/src-tauri/capabilities/gateway-opener.json").read_text()
        )
        self.assertEqual(opener["platforms"], ["linux", "macOS", "windows"])
        for host in (
            "http://127.0.0.1:*",
            "http://tauri.localhost",
            "https://tauri.localhost",
            "tauri://localhost",
        ):
            self.assertIn(host, opener["remote"]["urls"], host)
        desktop = (ROOT / "webui/src/lib/desktop.ts").read_text(encoding="utf-8")
        self.assertIn("isDesktopAppUrl", desktop)
        self.assertIn("isTauriCustomScheme", desktop)
        self.assertIn("tauri.localhost", desktop)
        urls = (ROOT / "webui/src/lib/external-url.ts").read_text(encoding="utf-8")
        self.assertIn("#/trading", urls)
        rust = (ROOT / "desktop/src-tauri/src/main.rs").read_text(encoding="utf-8")
        self.assertIn(".on_navigation({", rust)
        self.assertIn(".on_new_window(move |url, _features|", rust)
        self.assertIn("apply_in_app_navigation", rust)
        for sidecar in (
            "tauri.sidecar.linux.conf.json",
            "tauri.sidecar.windows.conf.json",
            "tauri.sidecar.macos.conf.json",
            "tauri.sidecar.macos-x64.conf.json",
        ):
            raw = (ROOT / "desktop/src-tauri" / sidecar).read_text(encoding="utf-8")
            self.assertNotIn("trading", raw.lower())
            self.assertNotIn("heartbeat", raw.lower())

    def test_terminal_help_and_flag_start_stop_match_the_api(self) -> None:
        self.assertIn("navin trading start", HELP)
        self.assertIn("stop", HELP)
        self.assertIn("watch", HELP)
        self.assertIn("#/trading", HELP)
        self.assertNotIn("\u2014", HELP)
        self.assertNotIn("\u2013", HELP)
        with tempfile.TemporaryDirectory() as tmp:
            store = TradingStore(Path(tmp))
            buf = io.StringIO()
            with (
                patch("navin.webui.trading_api._store", return_value=store),
                patch("sys.stdout", buf),
            ):
                code = main(["start", "--kind", "daily", "--hour", "7", "--minute", "15"])
            self.assertEqual(code, 0)
            started = json.loads(buf.getvalue())
            self.assertTrue(started["loop"]["enabled"])
            self.assertEqual(started["loop"]["schedule"]["hour"], 7)
            self.assertFalse(started["did_work"])
            buf = io.StringIO()
            with (
                patch("navin.webui.trading_api._store", return_value=store),
                patch("sys.stdout", buf),
            ):
                code = main(["pause"])
            self.assertEqual(code, 0)
            stopped = json.loads(buf.getvalue())
            self.assertFalse(stopped["loop"]["enabled"])
            with patch("navin.webui.trading_api._store", return_value=store):
                snap = handle_trading_action("snapshot")
            self.assertFalse(snap["loop"]["enabled"])
            self.assertEqual(snap["loop"]["schedule"]["hour"], 7)

    def test_tty_stop_does_not_read_stdin(self) -> None:
        stdin = Mock()
        stdin.isatty.return_value = True
        stdin.read.side_effect = AssertionError("TTY stop must not block on stdin")
        with tempfile.TemporaryDirectory() as tmp:
            store = TradingStore(Path(tmp))
            buf = io.StringIO()
            with (
                patch("navin.webui.trading_api._store", return_value=store),
                patch("navin.trading.desk_cli.sys.stdin", stdin),
                patch("sys.stdout", buf),
            ):
                code = main(["stop"])
        self.assertEqual(code, 0)
        stdin.read.assert_not_called()
        self.assertFalse(json.loads(buf.getvalue())["loop"]["enabled"])

    def test_vite_stdin_json_still_starts_without_a_cycle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = TradingStore(Path(tmp))
            buf = io.StringIO()
            with (
                patch("navin.webui.trading_api._store", return_value=store),
                patch("sys.argv", ["navin.trading.desk_cli", "start"]),
                patch(
                    "sys.stdin",
                    io.StringIO('{"schedule":{"kind":"weekdays","hour":8},"run_now":false}'),
                ),
                patch("sys.stdout", buf),
            ):
                code = main()
            self.assertEqual(code, 0)
            payload = json.loads(buf.getvalue())
            self.assertTrue(payload["loop"]["enabled"])
            self.assertEqual(payload["loop"]["schedule"]["kind"], "weekdays")
            self.assertFalse(payload["did_work"])

    def test_navin_cli_trading_is_the_same_desk(self) -> None:
        from typer.testing import CliRunner

        from navin.cli.commands import app

        with tempfile.TemporaryDirectory() as tmp:
            store = TradingStore(Path(tmp))
            runner = CliRunner()
            with patch("navin.webui.trading_api._store", return_value=store):
                started = runner.invoke(
                    app,
                    ["trading", "start", "--kind", "daily", "--hour", "7", "--minute", "15"],
                )
            self.assertEqual(started.exit_code, 0, started.output)
            payload = json.loads(started.stdout)
            self.assertTrue(payload["loop"]["enabled"])
            self.assertEqual(payload["loop"]["schedule"]["hour"], 7)
            with patch("navin.webui.trading_api._store", return_value=store):
                stopped = runner.invoke(app, ["trading", "stop"])
            self.assertEqual(stopped.exit_code, 0, stopped.output)
            self.assertFalse(json.loads(stopped.stdout)["loop"]["enabled"])

    def test_agent_start_stop_use_the_same_handle(self) -> None:
        import asyncio

        tool = TradingTool()
        self.assertIn("start", tool.parameters["properties"]["action"]["enum"])
        self.assertIn("stop", tool.parameters["properties"]["action"]["enum"])
        self.assertIn("schedule", tool.parameters["properties"]["action"]["enum"])
        self.assertIn("desk_cli", tool.description)
        with tempfile.TemporaryDirectory() as tmp:
            store = TradingStore(Path(tmp))
            ctx = RequestContext(
                channel="webui",
                chat_id="trading-1",
                session_key="webui:trading-1",
                metadata={"product_module": "trading"},
            )
            with patch("navin.webui.trading_api._store", return_value=store):
                with request_context(ctx):
                    started = asyncio.run(
                        tool.execute(
                            action="start",
                            schedule='{"kind":"daily","hour":6}',
                            run_now="false",
                        )
                    )
                    stopped = asyncio.run(tool.execute(action="stop"))
            self.assertFalse(getattr(started, "is_error", False), started)
            self.assertTrue(started["loop"]["enabled"])
            self.assertEqual(started["loop"]["schedule"]["hour"], 6)
            self.assertFalse(stopped["loop"]["enabled"])

    def test_gateway_supervisor_and_heartbeat_stay_split(self) -> None:
        cli = (ROOT / "navin/cli/commands.py").read_text(encoding="utf-8")
        self.assertIn("navin-trading-loop", cli)
        self.assertIn("trading_tick_inflight", cli)
        self.assertIn("tick_heartbeat_desks", cli)
        hb = (ROOT / ".navin/HEARTBEAT.md").read_text(encoding="utf-8")
        self.assertIn("trading action=watch", hb)
        self.assertIn("Studio Start loop", hb)
        self.assertIn("Never tick", hb)

    def test_agent_and_chat_seed_do_not_spawn_a_tick_cron(self) -> None:
        en = json.loads((ROOT / "webui/src/i18n/locales/en/common.json").read_text(encoding="utf-8"))
        fr = json.loads((ROOT / "webui/src/i18n/locales/fr/common.json").read_text(encoding="utf-8"))
        seed_en = en["thread"]["sessionInfo"]["createSeed"]["trading"]
        seed_fr = fr["thread"]["sessionInfo"]["createSeed"]["trading"]
        self.assertIn("Do not create a chat cron", seed_en)
        self.assertIn("Ne cree pas une cron de chat", seed_fr)
        for seed in (seed_en, seed_fr):
            self.assertIn("trading action=start", seed)
            self.assertIn("trading action=stop", seed)
            self.assertIn("trading action=watch", seed)
            self.assertIn("desk_cli", seed)
            self.assertIn("#/trading", seed)
            self.assertNotIn("every 15 minutes, tick", seed)
            self.assertNotIn("\u2014", seed)
            self.assertNotIn("\u2013", seed)
        skill = (ROOT / "navin/skills/trading-agent/SKILL.md").read_text(encoding="utf-8")
        self.assertIn("trading action=start", skill)
        self.assertNotIn("every 15 minutes", skill)
        self.assertIn("Never start, stop, schedule or tick from heartbeat", skill)


if __name__ == "__main__":
    unittest.main()
