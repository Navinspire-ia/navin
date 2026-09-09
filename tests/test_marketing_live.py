# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Real harvest / produce / HTTP / CLI / tool tests against a local product site."""

from __future__ import annotations

import asyncio
import io
import json
import struct
import tempfile
import threading
import unittest
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from navin.agent.tools.context import RequestContext, request_context
from navin.agent.tools.marketing import MarketingTool
from navin.marketing.assets import ingest_bytes, resolve_asset
from navin.marketing.desk_cli import main as marketing_cli
from navin.marketing.errors import MarketingError
from navin.marketing.harvest import harvest_live_site
from navin.marketing.produce import produce_assets
from navin.marketing.store import MarketingStore
from navin.marketing.understand import understand_product
from navin.webui.marketing_desk_api import handle_marketing_action
from navin.webui.ws_http import GatewayHTTPHandler

ROOT = Path(__file__).resolve().parents[1]


def png_1x1(rgb: tuple[int, int, int] = (109, 40, 217)) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

    raw = b"\x00" + bytes(rgb)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


HOME_HTML = """
<html>
  <head>
    <title>Acme CRM | Sales OS</title>
    <meta name="description" content="Pipeline for SMB sales teams." />
    <meta property="og:site_name" content="Acme" />
    <meta property="og:image" content="/og.png" />
    <meta name="theme-color" content="#6D28D9" />
    <link rel="icon" href="/logo.png" />
    <link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400&display=swap" rel="stylesheet" />
  </head>
  <body>
    <h1>Close deals without the spreadsheet</h1>
    <a href="/pricing">Pricing</a>
    <a href="https://linkedin.com/company/acme">LinkedIn</a>
    <a href="/start">Start free</a>
    <img src="/product.png" alt="product" />
  </body>
</html>
"""

PRICING_HTML = """
<html>
  <head>
    <title>Pricing | Acme</title>
    <meta name="description" content="Start free, then 29 per seat." />
  </head>
  <body>
    <h1>Simple pricing</h1>
    <a href="/">Home</a>
  </body>
</html>
"""


class _LiveSite:
    def __init__(self) -> None:
        png = png_1x1()
        files = {
            "/": (HOME_HTML.encode("utf-8"), "text/html; charset=utf-8"),
            "/index.html": (HOME_HTML.encode("utf-8"), "text/html; charset=utf-8"),
            "/pricing": (PRICING_HTML.encode("utf-8"), "text/html; charset=utf-8"),
            "/logo.png": (png, "image/png"),
            "/og.png": (png, "image/png"),
            "/product.png": (png, "image/png"),
        }

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 - http.server API
                path = self.path.split("?", 1)[0]
                payload = files.get(path)
                if payload is None:
                    self.send_response(404)
                    self.end_headers()
                    return
                body, content_type = payload
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args: object) -> None:
                return

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/"

    def close(self) -> None:
        self.server.shutdown()
        self.thread.join(5)


class _Request:
    def __init__(self, path: str, headers: dict[str, str] | None = None) -> None:
        self.path = path
        self.headers = headers or {}


class _Log:
    def exception(self, message, *args) -> None:
        pass

    def warning(self, message, *args) -> None:
        pass


def _handler(*, authorized: bool = True) -> GatewayHTTPHandler:
    handler = object.__new__(GatewayHTTPHandler)
    handler.check_api_token = lambda request: authorized
    handler._log = _Log()
    return handler


class MarketingLiveHarvestTest(unittest.TestCase):
    def setUp(self) -> None:
        self.site = _LiveSite()
        self.addCleanup(self.site.close)

    def test_harvest_fetches_pages_and_downloads_images(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = MarketingStore(Path(tmp))
            report = harvest_live_site(self.site.url)
            self.assertEqual(report["name"], "Acme")
            self.assertIn("Pipeline", report["one_liner"])
            self.assertTrue(any("/pricing" in str(page.get("url")) for page in report["pages"]))
            self.assertGreaterEqual(len(report["images"]), 3)
            from navin.marketing.harvest import apply_harvest

            saved = apply_harvest(store, report, download=True)
            assets = list((store.root / "assets").glob("*.png"))
            self.assertGreaterEqual(len(assets), 3)
            self.assertTrue(all(item.get("preview", "").startswith("/api/marketing?action=file") for item in saved["images"]))
            brand = store.load_brand()
            self.assertEqual(brand["company"], "Acme")
            self.assertIn("#6D28D9", brand["colors"])
            self.assertTrue(str(brand["logo"]).startswith("/api/marketing?action=file"))
            self.assertTrue(any(row.get("placement") == "site logo" for row in store.load_creatives()))
            product = store.load_product()
            self.assertGreaterEqual(len(product["screenshots"]), 3)
            self.assertTrue(any("pricing" in doc for doc in product["docs"]))

    def test_understand_url_uses_the_live_site_not_a_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = MarketingStore(Path(tmp))
            decoy = Path(tmp) / "decoy"
            decoy.mkdir()
            (decoy / "README.md").write_text("# DecoyRepo\n\nWrong product.\n", encoding="utf-8")
            product = understand_product(
                store,
                workspace=decoy,
                extras={"source_kind": "url", "site": self.site.url},
            )
            self.assertEqual(product["name"], "Acme")
            self.assertEqual(product["source_kind"], "url")
            self.assertFalse(product.get("workspace"))
            self.assertNotIn("DecoyRepo", product["name"])
            self.assertGreaterEqual(len(store.load_harvest()["images"]), 1)

    def test_desk_harvest_fills_brand_seo_social_and_ads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = MarketingStore(Path(tmp))
            with patch("navin.webui.marketing_desk_api._store", return_value=store):
                desk = handle_marketing_action("harvest", {"site": self.site.url})
            self.assertTrue(desk["armed"])
            self.assertEqual(desk["brand"]["company"], "Acme")
            self.assertEqual(desk["product"]["source_kind"], "url")
            self.assertGreaterEqual(len(desk["harvest"]["images"]), 3)
            self.assertGreaterEqual(len(desk["seo"]["pages"]), 2)
            self.assertTrue(any("pricing" in str(page.get("url") or "") for page in desk["seo"]["pages"]))
            self.assertGreaterEqual(len(desk["content"]), 4)
            self.assertTrue(
                any("Close deals" in (row.get("hook") or row.get("body") or "") for row in desk["content"])
            )
            self.assertGreaterEqual(len(desk["social"]["posts"]), 4)
            self.assertTrue(all(post.get("status") == "draft" for post in desk["social"]["posts"]))
            self.assertTrue(any(post.get("preview") for post in desk["social"]["posts"]))
            self.assertEqual(desk["ads"]["spend"], 0)
            self.assertEqual(len(desk["ads"]["campaigns"]), 2)
            self.assertTrue(all(row.get("spend") == 0 for row in desk["ads"]["campaigns"]))

    def test_existing_product_with_site_harvests(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = MarketingStore(Path(tmp))
            product = understand_product(
                store,
                extras={
                    "source_kind": "product",
                    "name": "Acme Desk",
                    "one_liner": "Manual CRM",
                    "site": self.site.url,
                },
            )
            self.assertEqual(product["source_kind"], "product")
            self.assertEqual(product["name"], "Acme Desk")
            self.assertGreaterEqual(len(store.load_harvest()["images"]), 1)
            self.assertTrue(str(store.load_brand()["site"]).startswith("http://127.0.0.1:"))

    def test_pipeline_from_live_url_builds_the_full_book(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = MarketingStore(Path(tmp))
            with patch("navin.webui.marketing_desk_api._store", return_value=store):
                desk = handle_marketing_action(
                    "pipeline",
                    {
                        "source_kind": "url",
                        "site": self.site.url,
                        "goal": "1000 inscriptions",
                        "days": 30,
                        "signups": 1000,
                    },
                )
            self.assertTrue(desk["armed"])
            self.assertEqual(desk["product"]["name"], "Acme")
            self.assertEqual(desk["product"]["source_kind"], "url")
            self.assertGreaterEqual(len(desk["harvest"]["images"]), 3)
            self.assertGreaterEqual(desk["kpis"]["campaigns"], 1)
            self.assertGreaterEqual(len(desk["content"]), 4)
            self.assertGreaterEqual(len(desk["social"]["posts"]), 4)
            self.assertEqual(desk["ads"]["spend"], 0)
            self.assertEqual(desk["launch"]["status"], "ready")

    def test_produce_targets_one_creative_and_skips_video_audio_without_provider(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = MarketingStore(Path(tmp))
            store.save_product({"name": "Acme", "one_liner": "CRM", "site": self.site.url})
            from navin.marketing.produce import produce_assets

            briefs = produce_assets(store, kinds=["image"], pack="brand", generate=False)
            brand_ids = [
                row["id"]
                for row in briefs["creatives"]
                if row.get("placement") == "brand lockup"
            ]
            self.assertTrue(brand_ids)
            target = brand_ids[0]

            def fake_image(desk, row):
                name = ingest_bytes(desk, png_1x1((16, 185, 129)), hint="one", suffix=".png")
                path = desk.root / "assets" / name
                return {
                    "preview": f"/api/marketing?action=file&name={name}",
                    "asset": name,
                    "path": str(path),
                    "status": "produced",
                }

            with patch("navin.marketing.produce._try_generate_image", side_effect=fake_image):
                one = produce_assets(store, creative_id=target, generate=True)
            self.assertEqual(one["produced"], 1)
            produced = [row for row in store.load_creatives() if row.get("id") == target]
            self.assertEqual(produced[0]["status"], "produced")
            others = [
                row
                for row in store.load_creatives()
                if row.get("kind") == "image" and row.get("id") != target
            ]
            self.assertTrue(all(row.get("status") != "produced" for row in others))

            video = produce_assets(store, kinds=["video"], generate=True)
            audio = produce_assets(store, kinds=["audio"], generate=True)
            self.assertEqual(video["produced"], 0)
            self.assertEqual(audio["produced"], 0)
            self.assertTrue(video["skipped"] or audio["skipped"])
            self.assertTrue(any(row.get("kind") == "video" for row in store.load_creatives()))
            self.assertTrue(any(row.get("kind") == "audio" for row in store.load_creatives()))

    def test_produce_writes_a_real_asset_and_refreshes_social(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = MarketingStore(Path(tmp))
            with patch("navin.webui.marketing_desk_api._store", return_value=store):
                handle_marketing_action("harvest", {"site": self.site.url})

                def fake_image(desk, row):
                    name = ingest_bytes(desk, png_1x1((16, 185, 129)), hint="gen", suffix=".png")
                    path = desk.root / "assets" / name
                    return {
                        "preview": f"/api/marketing?action=file&name={name}",
                        "asset": name,
                        "path": str(path),
                        "status": "produced",
                    }

                with patch("navin.marketing.produce._try_generate_image", side_effect=fake_image):
                    result = produce_assets(store, kinds=["image"], generate=True, max_images=1)
            self.assertEqual(result["produced"], 1)
            produced = [row for row in store.load_creatives() if row.get("status") == "produced"]
            self.assertEqual(len(produced), 1)
            asset = produced[0]["asset"]
            self.assertTrue((store.root / "assets" / asset).is_file())
            self.assertGreater((store.root / "assets" / asset).stat().st_size, 20)
            self.assertTrue(
                any(post.get("preview") == produced[0]["preview"] for post in store.load_social()["posts"])
            )

    def test_http_harvest_then_serves_the_downloaded_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = MarketingStore(Path(tmp))
            with patch("navin.webui.marketing_desk_api._store", return_value=store):
                with patch("navin.marketing.store.get_runtime_subdir", return_value=Path(tmp)):
                    handler = _handler()
                    raw = json.dumps({"site": self.site.url}).encode("utf-8")
                    import base64

                    headers = {"x-navin-file-body-0": base64.b64encode(raw).decode("ascii")}
                    harvested = asyncio.run(
                        handler._dispatch_session_routes(
                            _Request("/api/marketing?action=harvest", headers),
                            "/api/marketing",
                        )
                    )
                    self.assertEqual(harvested.status_code, 200, harvested.body)
                    desk = json.loads(harvested.body.decode("utf-8"))
                    self.assertEqual(desk["brand"]["company"], "Acme")
                    name = Path(str(desk["harvest"]["images"][0]["preview"]).split("name=")[-1]).name
                    served = asyncio.run(
                        handler._handle_marketing_desk(_Request(f"/api/marketing?action=file&name={name}"))
                    )
                    self.assertEqual(served.status_code, 200)
                    from navin.webui.http_utils import case_insensitive_header

                    self.assertIn("image/png", case_insensitive_header(served.headers, "Content-Type"))
                    self.assertTrue(served.body.startswith(b"\x89PNG"))
                    denied = asyncio.run(
                        handler._handle_marketing_desk(_Request("/api/marketing?action=file&name=../brand.json"))
                    )
                    self.assertEqual(denied.status_code, 404)

    def test_cli_and_tool_harvest_the_same_book(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = MarketingStore(Path(tmp))
            buf = io.StringIO()
            with patch("navin.webui.marketing_desk_api._store", return_value=store):
                with (
                    patch("sys.argv", ["navin.marketing.desk_cli", "harvest", "--site", self.site.url]),
                    patch("sys.stdout", buf),
                ):
                    code = marketing_cli()
                self.assertEqual(code, 0, buf.getvalue())
                cli = json.loads(buf.getvalue())
                self.assertEqual(cli["brand"]["company"], "Acme")
                self.assertGreaterEqual(len(cli["social"]["posts"]), 1)

                async def _tool():
                    return await MarketingTool().execute(action="status")

                text = str(asyncio.run(_tool()))
            self.assertIn("Acme", text)
            self.assertIn("armed=True", text)


class MarketingLiveGuardTest(unittest.TestCase):
    def test_invalid_url_is_400(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = MarketingStore(Path(tmp))
            with patch("navin.webui.marketing_desk_api._store", return_value=store):
                with self.assertRaises(MarketingError) as ctx:
                    handle_marketing_action("harvest", {"site": "not-a-url"})
                self.assertEqual(ctx.exception.status, 400)

    def test_unreachable_site_is_400(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = MarketingStore(Path(tmp))
            with patch("navin.webui.marketing_desk_api._store", return_value=store):
                with self.assertRaises(MarketingError) as ctx:
                    handle_marketing_action("harvest", {"site": "http://127.0.0.1:1/"})
                self.assertEqual(ctx.exception.status, 400)

    def test_file_path_cannot_escape_assets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = MarketingStore(Path(tmp))
            (store.root / "secret.txt").write_text("nope", encoding="utf-8")
            self.assertIsNone(resolve_asset(store, "../secret.txt"))
            self.assertIsNone(resolve_asset(store, "foo/../../../secret.txt"))
            name = ingest_bytes(store, png_1x1(), hint="ok", suffix=".png")
            path = resolve_asset(store, name)
            self.assertIsNotNone(path)
            self.assertTrue(path.is_file())

    def test_heartbeat_refuses_harvest_and_produce(self) -> None:
        ctx = RequestContext(
            channel="webui",
            chat_id="1",
            session_key="heartbeat",
            metadata={"heartbeat": True, "product_module": "marketing"},
        )
        with request_context(ctx):
            with tempfile.TemporaryDirectory() as tmp:
                store = MarketingStore(Path(tmp))
                with patch("navin.webui.marketing_desk_api._store", return_value=store):
                    harvest = asyncio.run(MarketingTool().execute(action="harvest", site="https://acme.test"))
                    produce = asyncio.run(MarketingTool().execute(action="produce", kinds="image"))
        self.assertTrue(getattr(harvest, "is_error", False))
        self.assertIn("heartbeat", str(harvest).lower())
        self.assertTrue(getattr(produce, "is_error", False))

    def test_ui_and_cli_keep_the_live_actions(self) -> None:
        desk = (ROOT / "webui/src/components/studio/marketing/MarketingWorkspace.tsx").read_text(encoding="utf-8")
        self.assertIn('data-testid="marketing-harvest-site"', desk)
        self.assertIn('run("produce"', desk)
        self.assertIn('run("social"', desk)
        self.assertIn('run("seo"', desk)
        self.assertIn('run("ads"', desk)
        help_text = (ROOT / "navin/marketing/desk_cli.py").read_text(encoding="utf-8")
        self.assertIn("harvest --site", help_text)
        self.assertIn("produce --kinds", help_text)


if __name__ == "__main__":
    unittest.main()
