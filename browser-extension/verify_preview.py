"""Exercise packaged Chromium capture, criteria analysis and reviewed import on isolated data.

Run from the repository root: .venv/bin/python browser-extension/verify_preview.py
Requires Playwright Chromium. Does not use platform accounts or real Career records.
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from playwright.sync_api import sync_playwright  # noqa: E402
from websockets.datastructures import Headers  # noqa: E402
from websockets.http11 import Request  # noqa: E402

from navin.career.prospecting import _state, handle_prospecting  # noqa: E402
from navin.career.store import CareerStore  # noqa: E402
from navin.leads.store import LeadsStore  # noqa: E402
from navin.webui.browser_bridge import BrowserBridge  # noqa: E402
from navin.webui.ws_http import GatewayHTTPHandler  # noqa: E402


def main() -> None:
    root = Path(tempfile.mkdtemp(prefix="navin-analysis-preview-"))
    store = CareerStore(root / "career")
    handle_prospecting(store, "prospecting_config", {"criteria": {
        "mode": "profiles", "roles": ["Data Engineer"], "profile_roles": ["Data Engineer"],
        "skills": ["Python", "SQL"], "countries": ["FR"], "buy_rate_max": 600, "min_score": 70,
        "daily_search": False, "signal_only": False,
    }})
    leads = LeadsStore(root / "leads")
    bridge = BrowserBridge(root / "bridge", roots={"career": store.root, "leads": leads.root})
    handler = object.__new__(GatewayHTTPHandler)
    fixture = """<!doctype html><html><body><main><h1>Freelances</h1>
      <article><h2>Alice Martin</h2><p>Data Engineer</p><p>Python SQL</p><p>France</p><p>TJM 450 EUR/jour</p><a href="/profile/alice">Voir le profil</a></article>
      <article><h2>Bob Dupont</h2><p>Graphiste</p><p>Photoshop</p><p>France</p><p>TJM 800 EUR/jour</p><a href="/profile/bob">Voir le profil</a></article>
      <article><h2>Cara Simon</h2><p>Data Engineer</p><p>Python</p><p>France</p><p>TJM 500 EUR/jour</p><a href="/profile/cara">Voir le profil</a></article>
      <article hidden><h2>Hidden Profile</h2><a href="/profile/hidden">Hidden</a><p>private hidden text</p></article>
      <form><input value="private form value"><textarea>private draft</textarea></form>
      </main></body></html>"""
    page_reads = []

    class Server(BaseHTTPRequestHandler):
        def do_GET(self):
            if not self.path.startswith("/api/browser-bridge"):
                page_reads.append(self.path)
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(fixture.encode())
                return
            with patch("navin.webui.browser_bridge.BrowserBridge", return_value=bridge):
                response = asyncio.run(handler._handle_browser_bridge(Request(path=self.path, headers=Headers(self.headers.items()))))
            self.send_response(response.status_code)
            self.send_header("Access-Control-Allow-Origin", "*")
            for key, value in response.headers.raw_items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(response.body)

        def do_OPTIONS(self):
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Headers", self.headers.get("Access-Control-Request-Headers", "*"))
            self.end_headers()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Server)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    extension = root / "extension"
    with ZipFile(ROOT / "navin/browser_extension/chrome.zip") as archive:
        archive.extractall(extension)
    try:
        with sync_playwright() as playwright:
            context = playwright.chromium.launch_persistent_context(str(root / "chromium"), channel="chromium", headless=True,
                args=[f"--disable-extensions-except={extension}", f"--load-extension={extension}"], viewport={"width": 1280, "height": 1000})
            worker = context.service_workers[0] if context.service_workers else context.wait_for_event("serviceworker")
            extension_id = worker.url.split("/")[2]
            page = context.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(base + "/search")
            capture = page.evaluate((extension / "capture.js").read_text())
            assert capture["profile_list"] is True
            assert [row["name"] for row in capture["records"]] == ["Alice Martin", "Bob Dupont", "Cara Simon"]
            assert "private" not in json.dumps(capture)
            assert all(row["kind"] == "candidate" for row in capture["records"])
            assert not any(url.startswith("/profile/") for url in page_reads)
            page.set_content(fixture.replace('/profile/', '/freelance/'))
            assert len(page.evaluate((extension / "capture.js").read_text())["records"]) == 3
            page.set_content('<main><input type="password"><article><a href="/profile/test">Test</a><p>Data Engineer</p></article></main>')
            assert page.evaluate((extension / "capture.js").read_text())["records"] == []
            # Exercise the real floating launcher and background capture message.
            code = bridge.handle("create", {"label": "Preview"}, admin=True)["code"]
            page.goto(base + "/search")
            page.locator('[data-navin-overlay]').wait_for(state="attached")
            page.mouse.click(1220, 878)
            page.wait_for_function("document.querySelector('[data-navin-overlay]') !== null")
            for _ in range(100):
                frames = [f for f in page.frames if f.url.startswith(f"chrome-extension://{extension_id}/index.html")]
                if frames:
                    break
                page.wait_for_timeout(100)
            assert frames, "The floating launcher did not open the extension panel"
            host_page = page
            page = frames[0]
            page.get_by_label("Code d'appairage", exact=True).fill(json.dumps({
                "type": "navin-browser-pairing", "navin_url": base, "code": code,
            }))
            assert page.get_by_label("Adresse de Navin", exact=True).input_value() == base
            page.get_by_text("J'autorise l'analyse des fiches à ma demande par cette instance Navin et leur enregistrement après ma validation. Aucun mot de passe ou cookie des plateformes n'est transmis.", exact=True).click()
            page.get_by_role("button", name="Relier à Navin", exact=True).click()
            page.get_by_role("button", name="Analyser cette page avec mes critères Navin").click()
            page.get_by_text("Analyse terminée. Vérifiez les fiches retenues avant leur enregistrement.", exact=True).wait_for()
            assert page.get_by_role("checkbox", name="Alice Martin", exact=True).is_checked()
            assert page.get_by_role("checkbox", name="Bob Dupont", exact=True).is_disabled()
            assert not page.get_by_role("checkbox", name="Cara Simon", exact=True).is_checked()
            assert _state(store)["candidates"] == []
            host_page.screenshot(path=str(root / "analysis.png"), full_page=True)
            assert host_page.url == base + "/search"
            assert len(context.pages) == 2, "Panel must not open a new tab"
            page.get_by_text("J'ai vérifié les 1 fiche(s) sélectionnée(s) et j'autorise leur envoi à Navin.", exact=True).click()
            assert page.get_by_role("checkbox", name="J'ai vérifié").is_checked()
            page.get_by_role("button", name="Envoyer 1 fiche(s) vers Navin").click()
            page.get_by_text("1 fiche(s) enregistrée(s) dans Navin.", exact=True).wait_for()
            assert len(_state(store)["candidates"]) == 1
            assert _state(store)["candidates"][0]["name"] == "Alice Martin"
            page.evaluate("location.reload()")
            page.get_by_role("button", name="Analyser cette page avec mes critères Navin").click()
            page.get_by_text("Analyse terminée. Vérifiez les fiches retenues avant leur enregistrement.", exact=True).wait_for()
            assert not page.get_by_role("checkbox", name="Cara Simon", exact=True).is_checked()
            # A local fixture on LinkedIn's origin, never an authenticated account.
            context.route("https://www.linkedin.com/**", lambda route: route.fulfill(
                content_type="text/html", body='<main><div id="selected" style="white-space:pre-line">Test LinkedIn\nData Engineer\nPython SQL France</div><p>Unselected private context</p></main>'))
            host_page.goto("https://www.linkedin.com/in/navin-local-test")
            host_page.locator('[data-navin-overlay]').wait_for(state="attached")
            host_page.evaluate("""() => {
                const range = document.createRange(); range.selectNodeContents(document.querySelector('#selected'));
                const selection = getSelection(); selection.removeAllRanges(); selection.addRange(range);
            }""")
            host_page.mouse.click(1220, 878)
            for _ in range(100):
                frames = [f for f in host_page.frames if f.url.startswith(f"chrome-extension://{extension_id}/index.html")]
                if frames:
                    break
                host_page.wait_for_timeout(100)
            assert frames
            linkedin = frames[0]
            linkedin.get_by_label("Nom du profil / contact", exact=True).wait_for()
            assert linkedin.get_by_label("Nom du profil / contact", exact=True).input_value() == "Test LinkedIn"
            assert "Unselected" not in linkedin.get_by_label("Description et informations à conserver", exact=True).input_value()
            assert len(_state(store)["candidates"]) == 1
            host_page.screenshot(path=str(root / "linkedin-panel.png"))
            # Closing and reopening keeps the draft, without another tab.
            host_page.mouse.click(1218, 42)
            host_page.mouse.click(1220, 878)
            assert len(context.pages) == 2
            assert linkedin.get_by_label("Nom du profil / contact", exact=True).input_value() == "Test LinkedIn"
            linkedin.get_by_role("combobox", name="Utilisation de cette capture").click()
            linkedin.get_by_role("option", name="Prospection commerciale (Leads)", exact=True).click()
            assert not linkedin.get_by_role("button", name="Analyser cette page avec mes critères Navin").count()
            assert not leads.load_leads()
            linkedin.get_by_label("Entreprise / client", exact=False).fill("Entreprise de test")
            linkedin.get_by_text("Test LinkedIn", exact=True).first.click()
            linkedin.get_by_text("J'ai vérifié les 1 fiche(s) sélectionnée(s) et j'autorise leur envoi à Navin.", exact=True).click()
            linkedin.get_by_role("button", name="Envoyer 1 fiche(s) vers Navin").click()
            linkedin.get_by_text("1 fiche(s) enregistrée(s) dans Navin.", exact=True).wait_for()
            assert len(leads.load_leads()) == 1
            assert leads.load_leads()[0]["person"] == "Test LinkedIn"
            assert len(_state(store)["candidates"]) == 1
            assert worker.evaluate("async () => (await chrome.storage.local.get('destination')).destination") == "leads"
            assert not errors, errors
            context.close()
            print(json.dumps({"preview": "passed", "screenshots": str(root), "checks": ["visible profile cards", "freelance profile paths", "no profile navigation", "hidden content excluded", "login page guarded", "real packaged extension", "criteria matching", "missing evidence", "reviewed import", "floating panel without new tab", "LinkedIn fixture selection preserved", "close and reopen draft"]}))
    finally:
        server.shutdown()


if __name__ == "__main__":
    main()
