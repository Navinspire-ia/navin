# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Career start/stop/heartbeat: one store for Tauri, terminal, and the agent."""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from navin.agent.tools.career import CareerTool
from navin.agent.tools.context import RequestContext, request_context
from navin.agent.tools.sandbox import writable_host_paths
from navin.career.desk_cli import HELP, main
from navin.career.store import CareerStore
from navin.config.paths import get_runtime_subdir
from navin.webui.career_api import handle_career_action

ROOT = Path(__file__).resolve().parents[1]


def _arm(store: CareerStore) -> None:
    store.save_profile({"titles": ["Data Engineer"], "wizard_complete": True})


class CareerSurfaceWiringTest(unittest.TestCase):
    def test_tauri_keeps_career_hash_in_the_webview(self) -> None:
        rust = (ROOT / "desktop/src-tauri/src/main.rs").read_text(encoding="utf-8")
        self.assertIn('#/career', rust)
        self.assertIn("is_desktop_app_url", rust)
        self.assertIn("http://tauri.localhost/#/career", rust)
        self.assertIn("https://tauri.localhost/#/career", rust)
        self.assertIn("https://tauri.localhost/#/career?job=abc", rust)
        self.assertIn("http://127.0.0.1:8766/#/career?job=abc", rust)
        self.assertIn("tauri://localhost/#/career", rust)
        ui = (ROOT / "webui/src/components/studio/career/career-ui.ts").read_text(
            encoding="utf-8"
        )
        self.assertIn('CAREER_HASH = "#/career"', ui)
        self.assertIn("openOfficialCareerUrl", ui)
        self.assertNotIn("window.open", ui)
        api = (ROOT / "webui/src/lib/career-api.ts").read_text(encoding="utf-8")
        self.assertIn("/api/career?action=", api)
        self.assertIn("function careerUrl", api)
        http = (ROOT / "navin/webui/ws_http.py").read_text(encoding="utf-8")
        self.assertIn('r"^/api/career$"', http)
        self.assertIn("handle_career_action", http)
        vite = (ROOT / "webui/vite.config.ts").read_text(encoding="utf-8")
        self.assertIn('"-m", "navin.career.desk_cli"', vite)

    def test_career_desk_is_identical_on_linux_windows_and_macos(self) -> None:
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
        self.assertIn("#/career", urls)
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
            self.assertNotIn("career", raw.lower())
            self.assertNotIn("heartbeat", raw.lower())

    def test_terminal_help_and_flag_start_stop_match_the_api(self) -> None:
        self.assertIn("navin career start", HELP)
        self.assertIn("start --kind", HELP)
        self.assertIn("stop", HELP)
        self.assertIn("watch", HELP)
        self.assertIn("#/career", HELP)
        self.assertIn("Never scrape LinkedIn", HELP)
        self.assertNotIn("\u2014", HELP)
        self.assertNotIn("\u2013", HELP)
        with tempfile.TemporaryDirectory() as tmp:
            store = CareerStore(Path(tmp))
            _arm(store)
            buf = io.StringIO()
            with (
                patch("navin.webui.career_api._store", return_value=store),
                patch("sys.stdout", buf),
            ):
                code = main(["start", "--kind", "daily", "--hour", "7", "--minute", "15"])
            self.assertEqual(code, 0)
            started = json.loads(buf.getvalue())
            self.assertTrue(started["loop"]["enabled"])
            self.assertEqual(started["loop"]["schedule"]["hour"], 7)
            self.assertEqual(started["loop_tick"]["phase"], "armed")
            self.assertFalse(started["loop_tick"]["did_work"])
            buf = io.StringIO()
            with (
                patch("navin.webui.career_api._store", return_value=store),
                patch("sys.stdout", buf),
            ):
                code = main(["pause"])
            self.assertEqual(code, 0)
            stopped = json.loads(buf.getvalue())
            self.assertFalse(stopped["loop"]["enabled"])
            with patch("navin.webui.career_api._store", return_value=store):
                snap = handle_career_action("snapshot")
            self.assertFalse(snap["loop"]["enabled"])
            self.assertEqual(snap["loop"]["schedule"]["hour"], 7)

    def test_tty_stop_does_not_read_stdin(self) -> None:
        stdin = Mock()
        stdin.isatty.return_value = True
        stdin.read.side_effect = AssertionError("TTY stop must not block on stdin")
        with tempfile.TemporaryDirectory() as tmp:
            store = CareerStore(Path(tmp))
            _arm(store)
            buf = io.StringIO()
            with (
                patch("navin.webui.career_api._store", return_value=store),
                patch("navin.career.desk_cli.sys.stdin", stdin),
                patch("sys.stdout", buf),
            ):
                code = main(["stop"])
        self.assertEqual(code, 0)
        stdin.read.assert_not_called()
        self.assertFalse(json.loads(buf.getvalue())["loop"]["enabled"])

    def test_navin_cli_career_is_the_same_desk(self) -> None:
        from typer.testing import CliRunner

        from navin.cli.commands import app

        with tempfile.TemporaryDirectory() as tmp:
            store = CareerStore(Path(tmp))
            _arm(store)
            runner = CliRunner()
            with patch("navin.webui.career_api._store", return_value=store):
                started = runner.invoke(
                    app,
                    ["career", "start", "--kind", "daily", "--hour", "7", "--minute", "15"],
                )
            self.assertEqual(started.exit_code, 0, started.output)
            payload = json.loads(started.stdout)
            self.assertTrue(payload["loop"]["enabled"])
            self.assertEqual(payload["loop"]["schedule"]["hour"], 7)
            self.assertFalse(payload["loop_tick"]["did_work"])
            with patch("navin.webui.career_api._store", return_value=store):
                stopped = runner.invoke(app, ["career", "stop"])
            self.assertEqual(stopped.exit_code, 0, stopped.output)
            self.assertFalse(json.loads(stopped.stdout)["loop"]["enabled"])

    def test_vite_stdin_json_still_starts_without_a_hunt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = CareerStore(Path(tmp))
            _arm(store)
            buf = io.StringIO()
            with (
                patch("navin.webui.career_api._store", return_value=store),
                patch("sys.argv", ["navin.career.desk_cli", "start"]),
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
            self.assertFalse(payload["loop_tick"]["did_work"])

    def test_agent_start_stop_use_the_same_handle(self) -> None:
        tool = CareerTool()
        self.assertIn("start", tool.parameters["properties"]["action"]["enum"])
        self.assertIn("stop", tool.parameters["properties"]["action"]["enum"])
        self.assertIn("desk_cli", tool.description)
        self.assertIn("#/career", tool.description)
        self.assertIn("heartbeat", tool.description.lower())
        with tempfile.TemporaryDirectory() as tmp:
            store = CareerStore(Path(tmp))
            _arm(store)
            ctx = RequestContext(
                channel="webui",
                chat_id="career-1",
                session_key="webui:career-1",
                metadata={"product_module": "career"},
            )
            import asyncio

            with patch("navin.webui.career_api._store", return_value=store):
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

    def test_agent_heartbeat_cannot_start_or_stop(self) -> None:
        import asyncio

        tool = CareerTool()
        ctx = RequestContext(
            channel="telegram",
            chat_id="1",
            session_key="heartbeat",
            metadata={"heartbeat": True},
        )
        with request_context(ctx):
            for action in ("start", "stop", "schedule", "tick"):
                result = asyncio.run(tool.execute(action=action))
                self.assertTrue(getattr(result, "is_error", False), action)
                self.assertIn("heartbeat", str(result).lower(), action)

    def test_sandbox_can_write_loop_lock_and_intent(self) -> None:
        career_dir = get_runtime_subdir("career")
        paths = writable_host_paths(str(tempfile.gettempdir()))
        self.assertIn(str(career_dir), paths)

    def test_gateway_supervisor_and_heartbeat_stay_split(self) -> None:
        cli = (ROOT / "navin/cli/commands.py").read_text(encoding="utf-8")
        self.assertIn("navin-career-loop", cli)
        self.assertIn("career_tick_inflight", cli)
        self.assertIn("tick_heartbeat_desks", cli)
        self.assertLess(cli.index("tick_heartbeat_desks"), cli.index("navin-career-loop"))
        hb = (ROOT / ".navin/HEARTBEAT.md").read_text(encoding="utf-8")
        self.assertIn("career action=watch", hb)
        self.assertIn("Studio Start loop", hb)
        self.assertIn("Never search", hb)

    def test_agent_and_chat_seed_do_not_spawn_a_search_cron(self) -> None:
        en = json.loads((ROOT / "webui/src/i18n/locales/en/common.json").read_text(encoding="utf-8"))
        fr = json.loads((ROOT / "webui/src/i18n/locales/fr/common.json").read_text(encoding="utf-8"))
        seed_en = en["thread"]["sessionInfo"]["createSeed"]["career"]
        seed_fr = fr["thread"]["sessionInfo"]["createSeed"]["career"]
        self.assertIn("Do not create a chat cron", seed_en)
        self.assertIn("Ne cree pas une cron de chat", seed_fr)
        for seed in (seed_en, seed_fr):
            self.assertIn("career action=start", seed)
            self.assertIn("career action=stop", seed)
            self.assertIn("career action=watch", seed)
            self.assertIn("desk_cli", seed)
            self.assertIn("navin career", seed)
            self.assertIn("#/career", seed)
            self.assertNotIn("every morning, call career action=search", seed)
            self.assertNotIn("chaque matin, appelle career action=search", seed)
            self.assertNotIn("\u2014", seed)
            self.assertNotIn("\u2013", seed)
        search = (ROOT / "navin/skills/job-search-agent/SKILL.md").read_text(encoding="utf-8")
        self.assertIn("career action=start", search)
        self.assertNotIn("Loop cron (reports every run)", search)
        self.assertIn("Never search, collect, start or tick from heartbeat", search)


if __name__ == "__main__":
    unittest.main()
