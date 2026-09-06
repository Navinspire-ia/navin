"""End-to-end Marketing integration: HTTP, CLI, tool, loop, heartbeat, Vite, sandbox."""

from __future__ import annotations

import asyncio
import base64
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from navin.agent.loop import AgentLoop
from navin.agent.tools.context import RequestContext, request_context
from navin.agent.tools.loader import ToolLoader
from navin.agent.tools.marketing import MarketingTool
from navin.agent.tools.sandbox import writable_host_paths
from navin.command.modules import (
    default_preload_skills_for_module,
    extra_denied_tools_for_module,
)
from navin.config.paths import get_runtime_subdir
from navin.gateway.heartbeat_desks import tick_heartbeat_desks
from navin.marketing.desk_cli import main as marketing_cli
from navin.marketing.store import MarketingStore
from navin.webui.marketing_desk_api import handle_marketing_action
from navin.webui.ws_http import GatewayHTTPHandler

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT_KEYS = {
    "brand",
    "product",
    "positioning",
    "research",
    "campaigns",
    "content",
    "creatives",
    "experiments",
    "analytics",
    "scoreboard",
    "seo",
    "ads",
    "harvest",
    "social",
    "competitors",
    "launch",
    "settings",
    "loop",
    "journal",
    "kpis",
    "armed",
    "skills",
}


class _Request:
    def __init__(self, path: str, headers: dict[str, str] | None = None) -> None:
        self.path = path
        self.headers = headers or {}


class _Log:
    def exception(self, message, *args) -> None:
        pass

    def warning(self, message, *args) -> None:
        pass


def _file_body_headers(payload: dict) -> dict[str, str]:
    raw = json.dumps(payload).encode("utf-8")
    b64 = base64.b64encode(raw).decode("ascii")
    return {"x-navin-file-body-0": b64}


def _handler(*, authorized: bool = True) -> GatewayHTTPHandler:
    handler = object.__new__(GatewayHTTPHandler)
    handler.check_api_token = lambda request: authorized
    handler._log = _Log()
    return handler


def _http(action: str, body: dict | None = None, *, authorized: bool = True):
    path = f"/api/marketing?action={action}"
    headers = _file_body_headers(body or {})
    handler = _handler(authorized=authorized)
    return asyncio.run(handler._dispatch_session_routes(_Request(path, headers), "/api/marketing"))


def _json(response) -> dict:
    return json.loads(response.body.decode("utf-8"))


class MarketingHttpIntegrationTest(unittest.TestCase):
    def test_dispatch_hits_the_desk_and_refuses_without_token(self) -> None:
        denied = _http("snapshot", authorized=False)
        self.assertEqual(denied.status_code, 401)
        self.assertIn(b"Unauthorized", denied.body)

        with tempfile.TemporaryDirectory() as tmp:
            store = MarketingStore(Path(tmp))
            with patch("navin.webui.marketing_desk_api._store", return_value=store):
                ok = _http("snapshot")
        self.assertEqual(ok.status_code, 200)
        payload = _json(ok)
        self.assertTrue(SNAPSHOT_KEYS.issubset(payload))
        self.assertFalse(payload["armed"])

    def test_http_pipeline_from_a_real_workspace_then_approve_and_loop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "app"
            workspace.mkdir()
            (workspace / "README.md").write_text(
                "# InvoiceAI\n\nAutomates invoice capture for SMBs.\n",
                encoding="utf-8",
            )
            (workspace / "package.json").write_text('{"name":"invoice-ai"}\n', encoding="utf-8")
            store = MarketingStore(Path(tmp) / "desk")
            with patch("navin.webui.marketing_desk_api._store", return_value=store):
                created = _http(
                    "pipeline",
                    {
                        "workspace": str(workspace),
                        "goal": "1000 inscriptions",
                        "days": 30,
                        "signups": 1000,
                    },
                )
                self.assertEqual(created.status_code, 200, created.body)
                desk = _json(created)
                self.assertTrue(desk["armed"])
                self.assertEqual(desk["product"]["name"], "InvoiceAI")
                self.assertIn("invoice", desk["product"]["category"])
                self.assertGreaterEqual(desk["kpis"]["campaigns"], 1)
                self.assertGreaterEqual(len(desk["content"]), 4)
                self.assertEqual(desk["launch"]["status"], "ready")
                campaign_id = desk["campaigns"][0]["id"]
                approved = _json(_http("approve", {"id": campaign_id}))
                self.assertIn("campaigns", approved)
                self.assertEqual(approved["campaigns"][0]["status"], "approved")
                started = _json(
                    _http(
                        "start",
                        {
                            "schedule": {"kind": "daily", "hour": 9, "minute": 0, "tz": "UTC"},
                            "run_now": True,
                        },
                    )
                )
                self.assertTrue(started["loop"]["enabled"])
                self.assertIn("loop_tick", started)
                stopped = _json(_http("stop"))
                self.assertFalse(stopped["loop"]["enabled"])

    def test_http_unknown_action_is_json_400(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = MarketingStore(Path(tmp))
            with patch("navin.webui.marketing_desk_api._store", return_value=store):
                response = _http("explode")
        self.assertEqual(response.status_code, 400)
        self.assertIn(b"unknown", response.body)


class MarketingCliAndToolShareStoreTest(unittest.TestCase):
    def test_cli_stdin_writes_the_book_the_tool_reads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = MarketingStore(Path(tmp))
            buf = io.StringIO()
            with patch("navin.webui.marketing_desk_api._store", return_value=store):
                with (
                    patch("sys.argv", ["navin.marketing.desk_cli", "pipeline"]),
                    patch(
                        "sys.stdin",
                        io.StringIO(
                            json.dumps(
                                {
                                    "product": {
                                        "name": "InvoiceAI",
                                        "one_liner": "Invoice capture",
                                    },
                                    "goal": "1000 inscriptions",
                                }
                            )
                        ),
                    ),
                    patch("sys.stdout", buf),
                ):
                    code = marketing_cli()
                self.assertEqual(code, 0)
                cli = json.loads(buf.getvalue())
                self.assertTrue(cli["armed"])
                self.assertEqual(cli["product"]["name"], "InvoiceAI")

                async def _status():
                    return await MarketingTool().execute(action="status")

                text = str(asyncio.run(_status()))
            self.assertIn("Marketing desk", text)
            self.assertIn("InvoiceAI", text)
            self.assertIn("armed=True", text)

    def test_vite_style_subprocess_cli_returns_json(self) -> None:
        env = {**os.environ, "PYTHONPATH": str(ROOT)}
        proc = subprocess.run(
            [sys.executable, "-m", "navin.marketing.desk_cli", "snapshot"],
            cwd=str(ROOT),
            input="{}",
            text=True,
            capture_output=True,
            env=env,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertTrue(SNAPSHOT_KEYS.issubset(payload))

    def test_cli_flags_start_requires_an_armed_desk(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = MarketingStore(Path(tmp))
            buf = io.StringIO()
            with patch("navin.webui.marketing_desk_api._store", return_value=store):
                with (
                    patch(
                        "sys.argv",
                        ["navin.marketing.desk_cli", "start", "--kind", "daily", "--hour", "9"],
                    ),
                    patch("sys.stdout", buf),
                ):
                    code = marketing_cli()
            self.assertEqual(code, 2)
            payload = json.loads(buf.getvalue())
            self.assertIn("brand", payload["error"])


class MarketingHeartbeatAndLoopIntegrationTest(unittest.TestCase):
    def test_gateway_watch_then_tool_is_refused_on_heartbeat(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = MarketingStore(Path(tmp))
            with patch("navin.webui.marketing_desk_api._store", return_value=store):
                handle_marketing_action(
                    "pipeline",
                    {"product": {"name": "InvoiceAI", "one_liner": "Invoice capture"}},
                )
                rows = store.load_content()
                handle_marketing_action(
                    "metrics",
                    {
                        "by_content": {
                            rows[0]["id"]: {"views": 400, "clicks": 80, "conversions": 20},
                            rows[1]["id"]: {"views": 200, "clicks": 6, "conversions": 1},
                        }
                    },
                )
            note = tick_heartbeat_desks(marketing_store=store)
            self.assertIn("watch.count=", note)
            self.assertIn("Never publish", note)

            ctx = RequestContext(
                channel="webui",
                chat_id="1",
                session_key="heartbeat",
                metadata={"heartbeat": True, "product_module": "marketing"},
            )
            with request_context(ctx):
                with patch("navin.webui.marketing_desk_api._store", return_value=store):
                    result = asyncio.run(MarketingTool().execute(action="pipeline"))
            self.assertTrue(getattr(result, "is_error", False))
            self.assertIn("heartbeat", str(result).lower())

    def test_workspace_heartbeat_and_vite_and_cli_are_wired(self) -> None:
        heartbeat = (ROOT / ".navin/HEARTBEAT.md").read_text(encoding="utf-8")
        template = (ROOT / "navin/templates/HEARTBEAT.md").read_text(encoding="utf-8")
        for body in (heartbeat, template):
            self.assertIn("marketing action=watch", body)
            self.assertIn("Never publish", body)
        cli = (ROOT / "navin/cli/commands.py").read_text(encoding="utf-8")
        self.assertIn("navin-marketing-loop", cli)
        self.assertIn("tick_heartbeat_desks", cli)
        self.assertIn("from navin.marketing.desk_cli import main as marketing_main", cli)
        vite = (ROOT / "webui/vite.config.ts").read_text(encoding="utf-8")
        self.assertIn("marketingDeskApi", vite)
        self.assertIn("navin.marketing.desk_cli", vite)
        self.assertIn('pathname !== "/api/marketing"', vite)
        api = (ROOT / "webui/src/lib/marketing-api.ts").read_text(encoding="utf-8")
        self.assertIn("`/api/marketing?action=", api)
        for key in ("campaigns", "content", "creatives", "loop", "kpis", "armed", "harvest", "social"):
            self.assertIn(key, api)
        sandbox = (ROOT / "navin/agent/tools/sandbox.py").read_text(encoding="utf-8")
        self.assertIn('"marketing"', sandbox)

    def test_tauri_keeps_marketing_inside_the_webview_on_every_os(self) -> None:
        """Windows (tauri.localhost), macOS (tauri://), Linux (127.0.0.1:8766)."""
        rust = (ROOT / "desktop/src-tauri/src/main.rs").read_text(encoding="utf-8")
        self.assertIn("http://tauri.localhost/#/marketing", rust)
        self.assertIn("https://tauri.localhost/#/marketing", rust)
        self.assertIn("https://tauri.localhost/#/marketing?chat=websocket:1", rust)
        self.assertIn("tauri://localhost/#/marketing", rust)
        self.assertIn("http://127.0.0.1:8766/#/marketing", rust)
        self.assertIn("#/marketing?chat=websocket:1", rust)
        self.assertIn(".on_navigation({", rust)
        self.assertIn("apply_in_app_navigation", rust)
        self.assertIn("in_app_hash", rust)
        opener = (ROOT / "desktop/src-tauri/capabilities/gateway-opener.json").read_text(
            encoding="utf-8"
        )
        self.assertIn('"linux"', opener)
        self.assertIn('"macOS"', opener)
        self.assertIn('"windows"', opener)
        self.assertIn("http://tauri.localhost", opener)
        self.assertIn("tauri://localhost", opener)
        desktop = (ROOT / "webui/src/lib/desktop.ts").read_text(encoding="utf-8")
        self.assertIn("isDesktopAppUrl", desktop)
        self.assertIn("isTauriCustomScheme", desktop)
        urls = (ROOT / "webui/src/lib/external-url.ts").read_text(encoding="utf-8")
        self.assertIn("#/marketing", urls)
        self.assertIn("ideNavigationHash", urls)
        spec = (ROOT / "packaging/pyinstaller/navin-onefile.spec").read_text(encoding="utf-8")
        self.assertIn('collect_submodules("navin")', spec)
        self.assertTrue((ROOT / "navin/marketing/desk_cli.py").is_file())
        for sidecar in (
            "tauri.sidecar.linux.conf.json",
            "tauri.sidecar.windows.conf.json",
            "tauri.sidecar.macos.conf.json",
            "tauri.sidecar.macos-x64.conf.json",
        ):
            raw = (ROOT / "desktop/src-tauri" / sidecar).read_text(encoding="utf-8")
            self.assertNotIn("marketing", raw.lower())
        for path in (ROOT / "navin/marketing").rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("sys.platform", text, path)
            self.assertNotIn("os.name", text, path)


class MarketingAgentWiringTest(unittest.TestCase):
    def test_tool_is_discoverable_and_scoped(self) -> None:
        self.assertIn(MarketingTool, ToolLoader().discover())
        self.assertEqual(extra_denied_tools_for_module("marketing"), frozenset({"tenders", "career", "trading", "leads"}))
        locked = AgentLoop._locked_denied_tools(None, {"product_module": "marketing"})
        self.assertIn("trading", locked)
        self.assertNotIn("marketing", locked)
        self.assertIn("marketing-strategist", default_preload_skills_for_module("marketing"))
        skill = (ROOT / "navin/skills/marketing-strategist/SKILL.md").read_text(encoding="utf-8")
        self.assertIn("marketing action=watch", skill)
        self.assertIn("navin.marketing.desk_cli", skill)
        self.assertIn("Never publish", skill)
        brief_fr = (ROOT / "webui/src/i18n/locales/fr/common.json").read_text(encoding="utf-8")
        self.assertIn("navin.marketing.desk_cli", brief_fr)
        self.assertIn("Ne cree pas une cron de chat", brief_fr)

    def test_sandbox_can_write_marketing_runtime_dir(self) -> None:
        marketing_dir = get_runtime_subdir("marketing").resolve()
        paths = writable_host_paths(str(tempfile.gettempdir()))
        resolved = []
        for item in paths:
            try:
                resolved.append(Path(item).resolve())
            except (OSError, RuntimeError, ValueError):
                continue
        self.assertIn(marketing_dir, resolved)
        self.assertTrue(marketing_dir.is_dir())


if __name__ == "__main__":
    unittest.main()
