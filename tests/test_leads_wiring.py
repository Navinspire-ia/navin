# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Leads start/stop/heartbeat: one store for Tauri, terminal, and the agent."""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from navin.agent.tools.context import RequestContext, request_context
from navin.agent.tools.leads import LeadsTool, LeadsToolConfig
from navin.agent.tools.sandbox import writable_host_paths
from navin.config.paths import get_runtime_subdir
from navin.leads.desk_cli import HELP, main
from navin.leads.store import LeadsStore
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


class LeadsSurfaceWiringTest(unittest.TestCase):
    def test_tauri_keeps_leads_hash_in_the_webview(self) -> None:
        rust = (ROOT / "desktop/src-tauri/src/main.rs").read_text(encoding="utf-8")
        self.assertIn("#/leads", rust)
        self.assertIn("is_desktop_app_url", rust)
        self.assertIn("http://tauri.localhost/#/leads", rust)
        self.assertIn("http://127.0.0.1:8766/#/leads?lead=abc", rust)
        self.assertIn("https://tauri.localhost/#/leads", rust)
        self.assertIn("tauri://localhost/#/leads", rust)
        ui = (ROOT / "webui/src/components/studio/leads/leads-ui.ts").read_text(
            encoding="utf-8"
        )
        self.assertIn("openOfficialLeadUrl", ui)
        self.assertNotIn("window.open", ui)
        api = (ROOT / "webui/src/lib/leads-api.ts").read_text(encoding="utf-8")
        self.assertIn("/api/leads?action=", api)
        self.assertIn("function leadsUrl", api)
        http = (ROOT / "navin/webui/ws_http.py").read_text(encoding="utf-8")
        self.assertIn(r"^/api/leads$", http)
        self.assertIn("handle_leads_action", http)
        vite = (ROOT / "webui/vite.config.ts").read_text(encoding="utf-8")
        self.assertIn('"-m", "navin.leads.desk_cli"', vite)

    def test_leads_desk_is_identical_on_linux_windows_and_macos(self) -> None:
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
        self.assertIn("#/leads", urls)
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
            self.assertNotIn("leads", raw.lower())
            self.assertNotIn("heartbeat", raw.lower())

    def test_docs_cover_loop_heartbeat_and_three_tauri_os(self) -> None:
        pages = (
            "docs/navin_leads/README.md",
            "docs/navin_leads/fr/desk.md",
            "docs/navin_leads/en/desk.md",
            "docs/navin_leads/fr/loop.md",
            "docs/navin_leads/en/loop.md",
            "docs/navin_leads/fr/heartbeat.md",
            "docs/navin_leads/en/heartbeat.md",
            "docs/navin_leads/fr/desktop.md",
            "docs/navin_leads/en/desktop.md",
            "docs/navin_leads/fr/cli-api.md",
            "docs/navin_leads/en/cli-api.md",
            "site/front/content/docs/navin_leads/en/desktop.md",
            "site/front/content/docs/navin_leads/en/loop.md",
        )
        for rel in pages:
            text = (ROOT / rel).read_text(encoding="utf-8")
            self.assertNotIn("\u2014", text, rel)
            self.assertNotIn("\u2013", text, rel)
        loop_fr = (ROOT / "docs/navin_leads/fr/loop.md").read_text(encoding="utf-8")
        self.assertIn("Sans `run_now`, ne chasse pas", loop_fr)
        self.assertIn("navin-leads-loop", loop_fr)
        hb = (ROOT / "docs/navin_leads/en/heartbeat.md").read_text(encoding="utf-8")
        self.assertIn("That is not this heartbeat", hb)
        self.assertIn("HEARTBEAT_WATCH_S", hb)
        desktop = (ROOT / "docs/navin_leads/en/desktop.md").read_text(encoding="utf-8")
        self.assertIn("Linux", desktop)
        self.assertIn("Windows", desktop)
        self.assertIn("macOS", desktop)
        self.assertIn("https://tauri.localhost", desktop)
        self.assertIn("tauri://localhost", desktop)
        self.assertIn("tsc -p tsconfig.build.json", desktop)
        catalog = (ROOT / "site/front/src/lib/docs-catalog.ts").read_text(encoding="utf-8")
        self.assertIn("navin_leads/en/desktop.md", catalog)
        self.assertIn("navin_leads/en/loop.md", catalog)

    def test_terminal_help_and_flag_start_stop_match_the_api(self) -> None:
        self.assertIn("navin leads start", HELP)
        self.assertIn("start --kind", HELP)
        self.assertIn("stop", HELP)
        self.assertIn("watch", HELP)
        self.assertIn("#/leads", HELP)
        self.assertIn("Never scrape LinkedIn", HELP)
        self.assertNotIn("\u2014", HELP)
        self.assertNotIn("\u2013", HELP)
        with tempfile.TemporaryDirectory() as tmp:
            store = LeadsStore(Path(tmp))
            _arm(store)
            buf = io.StringIO()
            with (
                patch("navin.webui.leads_api._store", return_value=store),
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
                patch("navin.webui.leads_api._store", return_value=store),
                patch("sys.stdout", buf),
            ):
                code = main(["pause"])
            self.assertEqual(code, 0)
            stopped = json.loads(buf.getvalue())
            self.assertFalse(stopped["loop"]["enabled"])
            with patch("navin.webui.leads_api._store", return_value=store):
                snap = handle_leads_action("snapshot")
            self.assertFalse(snap["loop"]["enabled"])
            self.assertEqual(snap["loop"]["schedule"]["hour"], 7)

    def test_tty_stop_does_not_read_stdin(self) -> None:
        stdin = Mock()
        stdin.isatty.return_value = True
        stdin.read.side_effect = AssertionError("TTY stop must not block on stdin")
        with tempfile.TemporaryDirectory() as tmp:
            store = LeadsStore(Path(tmp))
            _arm(store)
            buf = io.StringIO()
            with (
                patch("navin.webui.leads_api._store", return_value=store),
                patch("navin.leads.desk_cli.sys.stdin", stdin),
                patch("sys.stdout", buf),
            ):
                code = main(["stop"])
        self.assertEqual(code, 0)
        stdin.read.assert_not_called()
        self.assertFalse(json.loads(buf.getvalue())["loop"]["enabled"])

    def test_navin_cli_leads_is_the_same_desk(self) -> None:
        from typer.testing import CliRunner

        from navin.cli.commands import app

        with tempfile.TemporaryDirectory() as tmp:
            store = LeadsStore(Path(tmp))
            _arm(store)
            runner = CliRunner()
            with patch("navin.webui.leads_api._store", return_value=store):
                started = runner.invoke(
                    app,
                    ["leads", "start", "--kind", "daily", "--hour", "7", "--minute", "15"],
                )
            self.assertEqual(started.exit_code, 0, started.output)
            payload = json.loads(started.stdout)
            self.assertTrue(payload["loop"]["enabled"])
            self.assertEqual(payload["loop"]["schedule"]["hour"], 7)
            self.assertFalse(payload["loop_tick"]["did_work"])
            with patch("navin.webui.leads_api._store", return_value=store):
                stopped = runner.invoke(app, ["leads", "stop"])
            self.assertEqual(stopped.exit_code, 0, stopped.output)
            self.assertFalse(json.loads(stopped.stdout)["loop"]["enabled"])

    def test_vite_stdin_json_still_starts_without_a_hunt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = LeadsStore(Path(tmp))
            _arm(store)
            buf = io.StringIO()
            with (
                patch("navin.webui.leads_api._store", return_value=store),
                patch("sys.argv", ["navin.leads.desk_cli", "start"]),
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
        tool = LeadsTool(workspace=tempfile.gettempdir(), config=LeadsToolConfig())
        self.assertIn("start", tool.parameters["properties"]["action"]["enum"])
        self.assertIn("stop", tool.parameters["properties"]["action"]["enum"])
        self.assertIn("delete", tool.parameters["properties"]["action"]["enum"])
        self.assertIn("desk_cli", tool.description)
        self.assertIn("#/leads", tool.description)
        self.assertIn("navin leads", tool.description)
        self.assertIn("heartbeat", tool.description.lower())
        with tempfile.TemporaryDirectory() as tmp:
            store = LeadsStore(Path(tmp))
            _arm(store)
            ctx = RequestContext(
                channel="webui",
                chat_id="leads-1",
                session_key="webui:leads-1",
                metadata={"product_module": "leads"},
            )
            import asyncio

            with patch("navin.webui.leads_api._store", return_value=store):
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

        tool = LeadsTool(workspace=tempfile.gettempdir(), config=LeadsToolConfig())
        ctx = RequestContext(
            channel="telegram",
            chat_id="1",
            session_key="heartbeat",
            metadata={"heartbeat": True},
        )
        with request_context(ctx):
            for action in ("start", "stop", "schedule", "tick", "hunt"):
                result = asyncio.run(tool.execute(action=action))
                self.assertTrue(getattr(result, "is_error", False), action)
                self.assertIn("heartbeat", str(result).lower(), action)

    def test_sandbox_can_write_loop_lock_and_intent(self) -> None:
        leads_dir = get_runtime_subdir("leads")
        paths = writable_host_paths(str(tempfile.gettempdir()))
        self.assertIn(str(leads_dir), paths)

    def test_gateway_supervisor_and_heartbeat_stay_split(self) -> None:
        cli = (ROOT / "navin/cli/commands.py").read_text(encoding="utf-8")
        self.assertIn("navin-leads-loop", cli)
        self.assertIn("leads_tick_inflight", cli)
        self.assertIn("tick_heartbeat_desks", cli)
        self.assertIn("from navin.leads.desk_cli import main as leads_main", cli)
        self.assertLess(cli.index("tick_heartbeat_desks"), cli.index("navin-leads-loop"))
        hb = (ROOT / ".navin/HEARTBEAT.md").read_text(encoding="utf-8")
        self.assertIn("leads action=watch", hb)
        self.assertIn("The Leads desk loop", hb)
        self.assertIn("Never hunt", hb)

    def test_skills_and_status_name_the_desk_loop(self) -> None:
        prospector = (ROOT / "navin/skills/lead-prospector/SKILL.md").read_text(encoding="utf-8")
        self.assertIn("leads action=start", prospector)
        self.assertIn("Do not create a chat cron", prospector)
        self.assertIn("Never hunt, start, schedule, tick or send from heartbeat", prospector)
        for rel in (
            "navin/skills/buying-signals/SKILL.md",
            "navin/skills/lead-generation/SKILL.md",
            "navin/skills/lead-qualification/SKILL.md",
            "navin/skills/outreach-sequencer/SKILL.md",
        ):
            body = (ROOT / rel).read_text(encoding="utf-8")
            self.assertTrue(
                "leads action=start" in body or "leads action=watch" in body or "leads action=sequence" in body,
                rel,
            )
            self.assertNotIn("\u2014", body)
            self.assertNotIn("\u2013", body)

    def test_snapshot_loop_brief_matches_status(self) -> None:
        from navin.leads.desk import format_agent_status, snapshot
        from navin.leads.loop import start_loop

        with tempfile.TemporaryDirectory() as tmp:
            store = LeadsStore(Path(tmp))
            _arm(store)
            start_loop(
                store,
                schedule={"kind": "daily", "hour": 7, "minute": 15},
                run_now=False,
                now=1_700_000_000.0,
            )
            snap = snapshot(store)
            self.assertIn("Loop: ON", snap["loop_brief"])
            self.assertIn("every day at 07:15", snap["loop_brief"])
            self.assertIn("next ", snap["loop_brief"])
            text = format_agent_status(snap)
            self.assertIn(snap["loop_brief"], text)
            self.assertIn("If the loop is ON, do not hunt again", text)

    def test_agent_and_chat_seed_do_not_spawn_a_hunt_cron(self) -> None:
        en = json.loads((ROOT / "webui/src/i18n/locales/en/common.json").read_text(encoding="utf-8"))
        fr = json.loads((ROOT / "webui/src/i18n/locales/fr/common.json").read_text(encoding="utf-8"))
        seed_en = en["thread"]["sessionInfo"]["createSeed"]["leads"]
        seed_fr = fr["thread"]["sessionInfo"]["createSeed"]["leads"]
        self.assertIn("Do not create a chat cron", seed_en)
        self.assertIn("Ne cree pas une cron de chat", seed_fr)
        brief = (ROOT / "navin/command/builtin.py").read_text(encoding="utf-8")
        self.assertIn("Do not create a chat cron that hunts or ticks", brief)
        self.assertNotIn("Loop cron may hunt", brief)
        for seed in (seed_en, seed_fr):
            self.assertIn("leads action=start", seed)
            self.assertIn("leads action=stop", seed)
            self.assertIn("leads action=watch", seed)
            self.assertIn("desk_cli", seed)
            self.assertIn("navin leads", seed)
            self.assertIn("#/leads", seed)
            self.assertNotIn("Create a loop for this leads chat", seed)
            self.assertNotIn("\u2014", seed)
            self.assertNotIn("\u2013", seed)


if __name__ == "__main__":
    unittest.main()
