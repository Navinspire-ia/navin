# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Secure, high quality demo capture through the browser tool.

Drives a real headless Chromium against a local login app:

- credentials come from the vault through ``secret_ref`` / ``login`` and the
  clear value never shows in a tool result, the history, the live action feed
  or the logs;
- password fields and fields filled from a secret are masked in the page;
- ``record_start`` keeps the logged-in session (storage_state carried over)
  and ``record_stop`` writes a CFR MP4 plus the ``.actions.json`` sidecar.

Skipped when Playwright or a Chromium build is not available.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from unittest import mock
from urllib.parse import parse_qs

from loguru import logger

from navin.agent.tools import browser as browser_mod
from navin.agent.tools.browser import _SESSIONS, BrowserTool, BrowserToolConfig
from navin.browser_credentials import BrowserCredentialStore, handle_browser_credentials_action

PASSWORD = "Sup3r-Secret-Pa55!"
USERNAME = "demo.owner@example.test"
PLAIN_PASSWORD = "Typed-In-Clear-77"

_LOGIN_PAGE = """<!doctype html><html><head><title>Sign in</title></head><body>
<form method="post" action="/login">
  <input id="user" name="username" type="email" placeholder="Email">
  <input id="pass" name="password" type="password" placeholder="Password">
  <button type="submit">Sign in</button>
</form>
<input id="search" type="text" placeholder="Search">
</body></html>"""

_APP_PAGE = """<!doctype html><html><head><title>Dashboard</title>
<style>body{margin:0;font:16px sans-serif} .tall{height:2400px}</style></head><body>
<h1 id="welcome">Welcome back</h1>
<button id="action" onclick="document.getElementById('out').textContent='clicked'">Create report</button>
<div id="out"></div>
<input id="note" type="text" placeholder="Note">
<input id="token" type="text" placeholder="Token">
<!--settings-->
<div id="private" data-sensitive>ACCOUNT 4242</div>
<div class="tall"></div>
</body></html>"""


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args: Any) -> None:  # keep test output quiet
        return

    def _send(self, status: int, body: str = "", headers: dict[str, str] | None = None) -> None:
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)

    def _logged_in(self) -> bool:
        return "session=ok" in (self.headers.get("Cookie") or "")

    def do_GET(self) -> None:  # noqa: N802
        if self.path.startswith("/app"):
            if self._logged_in():
                page = _APP_PAGE
                if self.path.startswith("/app/settings"):
                    # A dashboard page that has its own password field.
                    page = page.replace("<!--settings-->", '<input id="pw2" type="password" placeholder="Confirm">')
                self._send(200, page)
            else:
                self._send(302, headers={"Location": "/login"})
            return
        self._send(200, _LOGIN_PAGE)

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        form = parse_qs(self.rfile.read(length).decode("utf-8"))
        if form.get("username") == [USERNAME] and form.get("password") == [PASSWORD]:
            self._send(302, headers={"Location": "/app", "Set-Cookie": "session=ok; Path=/; HttpOnly"})
        else:
            self._send(200, _LOGIN_PAGE)


class _NoVault:
    """No OS keyring: forces the encrypted-file fallback."""

    def ready(self) -> bool:
        return False


class _Bus:
    def __init__(self) -> None:
        self.outbound = self
        self.items: list[Any] = []

    def put_nowait(self, item: Any) -> None:
        self.items.append(item)


def _chromium_available() -> bool:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            browser.close()
        return True
    except Exception:
        return False


_HAS_CHROMIUM = _chromium_available()


def _text_of(result: Any) -> str:
    if isinstance(result, list):
        return "\n".join(str(b.get("text") or "") for b in result if isinstance(b, dict))
    return str(result)


@unittest.skipUnless(_HAS_CHROMIUM, "playwright chromium not available")
class SecureCaptureTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="navin-capture-"))
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.site = f"127.0.0.1:{self.server.server_address[1]}"
        self.store = BrowserCredentialStore(
            self.tmp / "creds", vault=_NoVault(), config_path=self.tmp / "config.json"
        )
        handle_browser_credentials_action(
            "set",
            {"site": self.base, "values": {"username": USERNAME, "password": PASSWORD, "token": "tok-ABCDEF-123"}},
            store=self.store,
        )
        self.patches = [
            mock.patch.object(browser_mod, "_credential_store", lambda: self.store),
            mock.patch.object(browser_mod, "artifact_directory", lambda sub: self.tmp / "media" / sub),
        ]
        for patch in self.patches:
            patch.start()
        self.logs: list[str] = []
        self.log_id = logger.add(lambda msg: self.logs.append(str(msg)), level="DEBUG")
        _SESSIONS.clear()
        self.tool = BrowserTool(
            config=BrowserToolConfig(
                live_view=True,
                record_viewport_width=1280,
                record_viewport_height=720,
                record_device_scale_factor=1.0,
            )
        )
        self.bus = _Bus()

    async def asyncTearDown(self) -> None:
        logger.remove(self.log_id)
        for session in list(_SESSIONS.values()):
            await session.close()
        _SESSIONS.clear()
        for patch in self.patches:
            patch.stop()
        self.server.shutdown()
        self.server.server_close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    async def _run(self, **kwargs: Any) -> Any:
        session = await browser_mod._get_session(self.tool.config)
        session._live_bus = self.bus
        session._live_chat_id = "chat-1"
        return await self.tool.execute(**kwargs)

    def _live_text(self) -> str:
        return "\n".join(
            json.dumps(getattr(item, "metadata", None) or {}, default=str) + str(getattr(item, "content", ""))
            for item in self.bus.items
        )

    async def test_login_mask_record_and_no_leak(self) -> None:
        session = await browser_mod._get_session(self.tool.config)
        outputs: list[str] = []

        result = await self._run(action="login", url=f"{self.base}/login")
        outputs.append(_text_of(result))
        self.assertIn("Signed in", outputs[-1])
        self.assertIn("Welcome back", outputs[-1])

        # The session was saved encrypted: no cookie value in clear on disk.
        self.assertIsNotNone(self.store.load_storage_state(self.site))
        for path in (self.tmp / "creds").rglob("*"):
            if path.is_file():
                raw = path.read_bytes()
                self.assertNotIn(b"session=ok", raw)
                self.assertNotIn(PASSWORD.encode(), raw)
                self.assertNotIn(USERNAME.encode(), raw)

        outputs.append(_text_of(await self._run(action="navigate", url=f"{self.base}/app/settings")))
        # secret_ref into a plain text field: masked in the page, hidden everywhere.
        result = await self._run(action="type", selector="#token", secret_ref=f"site:{self.site}/token")
        outputs.append(_text_of(result))
        self.assertIn("value hidden", outputs[-1])
        # A password typed in clear into a password field is scrubbed too.
        result = await self._run(action="type", selector="#pw2", text=PLAIN_PASSWORD)
        outputs.append(_text_of(result))
        self.assertIn("secret_ref", outputs[-1])

        styles = await session.page.evaluate(
            """() => ['#token', '#pw2', '#private', '#note'].map((s) => {
                const cs = getComputedStyle(document.querySelector(s));
                return [cs.filter, cs.webkitTextSecurity];
            })"""
        )
        for masked in styles[:3]:
            self.assertIn("blur", masked[0])
            self.assertEqual(masked[1], "disc")
        self.assertEqual(styles[3][0], "none")

        # Reading the values back through the tool never returns them.
        result = await self._run(
            action="evaluate",
            javascript="() => [document.querySelector('#token').value, document.querySelector('#pw2').value]",
        )
        outputs.append(_text_of(result))
        self.assertIn("[secret]", outputs[-1])
        outputs.append(_text_of(await self._run(action="snapshot")))

        # Pixel check: the masked field does not render like the same value in clear.
        masked_png = await session.page.locator("#token").screenshot()
        await session.page.evaluate("() => { document.querySelector('#note').value = 'tok-ABCDEF-123'; }")
        clear_png = await session.page.locator("#note").screenshot()
        await session.page.evaluate("() => { document.querySelector('#note').value = ''; }")
        self.assertNotEqual(masked_png, clear_png)
        try:
            import PIL.Image
        except ImportError:
            pass
        else:
            import io

            def darkest(png: bytes) -> int:
                image = PIL.Image.open(io.BytesIO(png)).convert("L")
                width, height = image.size
                # Inner area only: the input border is drawn in clear on both.
                return min(image.crop((4, 4, width - 4, height - 4)).getdata())

            # Crisp glyphs reach near-black; blurred dots never do.
            self.assertGreater(darkest(masked_png), darkest(clear_png) + 40)

        # Screenshot with DPR scale and element bbox metadata.
        result = await self._run(action="screenshot", selector="#action", scale=2)
        label = _text_of(result)
        outputs.append(label)
        meta = json.loads(label.split("metadata: ", 1)[1].splitlines()[0])
        self.assertEqual(meta["scale"], 2.0)
        bbox = meta["bbox"]
        self.assertEqual(len(bbox), 4)
        self.assertAlmostEqual(meta["image"][0], round(bbox[2] * 2), delta=2)

        # Recording keeps the logged-in session.
        result = await self._run(action="record_start")
        outputs.append(_text_of(result))
        self.assertIn("Recording started", outputs[-1])
        outputs.append(_text_of(await self._run(action="navigate", url=f"{self.base}/app")))
        self.assertIn("Welcome back", outputs[-1])
        self.assertNotIn("[login-wall]", outputs[-1])
        outputs.append(_text_of(await self._run(action="hover", selector="#action")))
        outputs.append(_text_of(await self._run(action="click", selector="#action")))
        outputs.append(_text_of(await self._run(action="type", selector="#note", text="hello demo")))
        outputs.append(_text_of(await self._run(action="type", selector="#token", secret_ref=f"site:{self.site}/token")))
        outputs.append(_text_of(await self._run(action="scroll", direction="down")))
        outputs.append(_text_of(await self._run(action="press_key", key="Tab")))
        outputs.append(_text_of(await self._run(action="wait", ms=300)))
        result = await self._run(action="record_stop")
        outputs.append(_text_of(result))
        self.assertIn("mp4:", outputs[-1], outputs[-1])
        self.assertIn("actions:", outputs[-1])

        mp4 = Path(outputs[-1].split("mp4: ", 1)[1].splitlines()[0].strip())
        actions_path = Path(outputs[-1].split("actions: ", 1)[1].splitlines()[0].strip())
        self.assertTrue(mp4.is_file())
        self.assertEqual(actions_path, mp4.with_suffix(".actions.json"))
        log = json.loads(actions_path.read_text(encoding="utf-8"))
        self.assertEqual(log["version"], 1)
        self.assertEqual(log["viewport"], {"width": 1280, "height": 720})
        self.assertEqual(log["device_scale_factor"], 1.0)
        self.assertEqual(log["fps"], 30)
        self.assertGreater(log["duration"], 0)
        types = [event["type"] for event in log["events"]]
        for expected in ("navigate", "hover", "click", "type", "scroll", "key", "wait"):
            self.assertIn(expected, types)
        for event in log["events"]:
            self.assertEqual(set(event) - {"path"}, {"t", "type", "x", "y", "bbox", "label"})
            self.assertGreaterEqual(event["t"], 0)
            self.assertLessEqual(event["t"], log["duration"] + 0.01)
            self.assertNotIn("tok-ABCDEF-123", event["label"])
            if event["bbox"] is not None:
                self.assertEqual(len(event["bbox"]), 4)
                x, y, w, h = event["bbox"]
                self.assertAlmostEqual(event["x"], x + w / 2, delta=0.2)
                self.assertAlmostEqual(event["y"], y + h / 2, delta=0.2)
        click = next(e for e in log["events"] if e["type"] == "click")
        self.assertTrue(click["path"])
        self.assertEqual(len(click["path"][0]), 3)
        self.assertEqual(click["label"], "Create report")

        if shutil.which("ffprobe"):
            probe = subprocess.run(
                ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                 "stream=codec_name,r_frame_rate,avg_frame_rate,width,height", "-of", "json", str(mp4)],
                capture_output=True, text=True, check=True,
            )
            stream = json.loads(probe.stdout)["streams"][0]
            self.assertEqual(stream["codec_name"], "h264")
            self.assertEqual(stream["r_frame_rate"], "30/1")
            self.assertEqual(stream["avg_frame_rate"], "30/1")
            self.assertEqual((stream["width"], stream["height"]), (1280, 720))

        # Back from recording: still logged in on the normal viewport.
        outputs.append(_text_of(await self._run(action="navigate", url=f"{self.base}/app")))
        self.assertIn("Welcome back", outputs[-1])
        outputs.append(_text_of(await self._run(action="history")))

        everything = "\n".join(outputs) + self._live_text() + "\n".join(self.logs) + json.dumps(session.history)
        for secret in (PASSWORD, USERNAME, PLAIN_PASSWORD, "tok-ABCDEF-123"):
            self.assertNotIn(secret, everything)

    async def test_saved_session_is_reused_without_typing(self) -> None:
        result = await self._run(action="login", url=f"{self.base}/login")
        self.assertIn("Signed in", _text_of(result))
        for session in list(_SESSIONS.values()):
            await session.close()
        _SESSIONS.clear()

        result = await self._run(action="login", url=f"{self.base}/app")
        text = _text_of(result)
        self.assertIn("Already signed in", text)
        self.assertIn("Welcome back", text)

    async def test_wrong_credentials_report_a_failed_login_without_the_value(self) -> None:
        handle_browser_credentials_action(
            "set", {"site": self.base, "name": "password", "value": "wrong-pass-999"}, store=self.store
        )
        result = await self._run(action="login", url=f"{self.base}/login")
        text = _text_of(result)
        self.assertTrue(getattr(result, "is_error", False))
        self.assertIn("did not complete", text)
        self.assertNotIn("wrong-pass-999", text + "\n".join(self.logs))

    async def test_missing_secret_points_to_settings(self) -> None:
        await self._run(action="navigate", url=f"{self.base}/login")
        result = await self._run(action="type", selector="#pass", secret_ref=f"site:{self.site}/nope")
        self.assertTrue(getattr(result, "is_error", False))
        self.assertIn("Settings", str(result))


if __name__ == "__main__":
    asyncio.run(unittest.main())
