"""Career, Leads, Marketing, Tenders and Trading ride the same Tauri sidecar.

Linux (127.0.0.1 / http://tauri.localhost), Windows (https://tauri.localhost)
and macOS (tauri://localhost) keep the five desk hashes in the WebView. The
PyInstaller spec freezes the Python packages; tsc + vite ship the UI.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

DESKS = (
    ("career", "navin.career.desk_cli", "#/career", "CareerWorkspace"),
    ("leads", "navin.leads.desk_cli", "#/leads", "LeadsWorkspace"),
    ("marketing", "navin.marketing.desk_cli", "#/marketing", "MarketingWorkspace"),
    ("tenders", "navin.tenders.desk_cli", "#/tenders", "TendersWorkspace"),
    ("trading", "navin.trading.desk_cli", "#/trading", "TradingWorkspace"),
)

SIDECARS = (
    "tauri.sidecar.linux.conf.json",
    "tauri.sidecar.windows.conf.json",
    "tauri.sidecar.macos.conf.json",
    "tauri.sidecar.macos-x64.conf.json",
)

WINDOWS_HASHES = (
    "#/career",
    "#/career?job=abc",
    "#/leads",
    "#/leads?lead=abc",
    "#/leads?pane=book",
    "#/marketing",
    "#/marketing?chat=websocket:1",
    "#/tenders",
    "#/tenders?notice=abc",
    "#/tenders?pane=tenders",
    "#/trading",
    "#/trading?chat=websocket:1",
)


class DeskTauriPackagingTest(unittest.TestCase):
    def test_tsc_is_the_webui_gate_every_tauri_os_runs(self) -> None:
        pkg = json.loads((ROOT / "webui/package.json").read_text(encoding="utf-8"))
        self.assertEqual(
            pkg["scripts"]["build"],
            "tsc -p tsconfig.build.json && vite build && node scripts/stamp-bundle.mjs",
        )
        linux = (ROOT / "packaging/linux/build-offline.sh").read_text(encoding="utf-8")
        macos = (ROOT / "packaging/macos/build-offline.sh").read_text(encoding="utf-8")
        windows = (ROOT / "packaging/windows/build-offline.ps1").read_text(encoding="utf-8")
        import_line = (
            "import navin.career.desk_cli, navin.leads.desk_cli, "
            "navin.marketing.desk_cli, navin.tenders.desk_cli, navin.trading.desk_cli"
        )
        for body, label in ((linux, "linux"), (macos, "macos")):
            self.assertIn("npm run build", body, label)
            self.assertIn(import_line, body, label)
        self.assertIn("npm.cmd run build", windows)
        self.assertIn(import_line, windows)

    def test_one_resolver_builds_the_bundle_on_every_os(self) -> None:
        """No release path may build the WebUI with a second package manager.

        bun.lock and package-lock.json resolve different versions (112 of 878
        packages when this was measured), so a script that accepted either made
        the shipped bundle depend on what the build machine had installed.
        """
        for relative in (
            "packaging/linux/build-offline.sh",
            "packaging/macos/build-offline.sh",
            "packaging/windows/build-offline.ps1",
        ):
            body = (ROOT / relative).read_text(encoding="utf-8")
            for line in body.splitlines():
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                self.assertNotIn("bun run build", stripped, relative)
                self.assertNotIn("bun install", stripped, relative)

    def test_pyinstaller_freezes_every_desk_package(self) -> None:
        spec = (ROOT / "packaging/pyinstaller/navin-onefile.spec").read_text(encoding="utf-8")
        contents = (ROOT / "packaging/pyinstaller/bundle_contents.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('collect_submodules("navin")', spec)
        self.assertIn("desk_hidden_imports", contents)
        self.assertIn("DESK_PACKAGES", contents)
        for name, module, _desk_hash, _ui in DESKS:
            pkg = module.rsplit(".", 1)[0]
            self.assertIn(f'collect_submodules("{pkg}")', spec, name)
            self.assertIn(f'"{pkg}"', contents, name)
            self.assertTrue((ROOT / pkg.replace(".", "/") / "desk_cli.py").is_file(), name)

    def test_macos_x64_sidecar_matches_arm(self) -> None:
        arm = json.loads(
            (ROOT / "desktop/src-tauri/tauri.sidecar.macos.conf.json").read_text(
                encoding="utf-8"
            )
        )
        intel = json.loads(
            (ROOT / "desktop/src-tauri/tauri.sidecar.macos-x64.conf.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(arm["bundle"]["resources"], intel["bundle"]["resources"])

    def test_desktop_and_web_share_one_dark_canvas(self) -> None:
        dark = "#141414"
        light = "#fcfcfc"
        rust = (ROOT / "desktop/src-tauri/src/main.rs").read_text(encoding="utf-8")
        conf = (ROOT / "desktop/src-tauri/tauri.conf.json").read_text(encoding="utf-8")
        splash = (ROOT / "desktop/ui/index.html").read_text(encoding="utf-8")
        html = (ROOT / "webui/index.html").read_text(encoding="utf-8")
        theme = (ROOT / "webui/src/hooks/useTheme.ts").read_text(encoding="utf-8")
        manifest = (ROOT / "webui/public/manifest.webmanifest").read_text(encoding="utf-8")
        css = (ROOT / "webui/src/globals.css").read_text(encoding="utf-8")
        self.assertIn(f'DESKTOP_CANVAS_DARK: &str = "{dark}"', rust)
        self.assertIn(f'"backgroundColor": "{dark}"', conf)
        self.assertIn(f"background: {dark}", splash)
        self.assertIn(f'dark: "{dark}"', theme)
        self.assertIn(f'light: "{light}"', theme)
        self.assertIn(dark, html)
        self.assertIn(light, html)
        self.assertNotIn("#0f1724", html)
        self.assertNotIn("#0b0b0e", splash)
        self.assertNotIn("#0b0b0e", conf)
        self.assertIn(f'"background_color": "{dark}"', manifest)
        self.assertIn(f'"theme_color": "{dark}"', manifest)
        self.assertIn("html.webkit-gtk.dark", css)
        self.assertIn("background-color: hsl(0, 0%, 8%)", css)
        self.assertIn("fn zoom_baseline() -> f64", rust)
        self.assertNotIn("return 1.25;", rust)
        readme = (ROOT / "desktop/README.md").read_text(encoding="utf-8")
        self.assertIn("1.0", readme)
        self.assertNotIn("zoom de base est **1.25**", readme)

    def test_sidecar_overlays_stay_desk_agnostic_on_every_os(self) -> None:
        for sidecar in SIDECARS:
            raw = (ROOT / "desktop/src-tauri" / sidecar).read_text(encoding="utf-8")
            self.assertIn("navin-dist", raw, sidecar)
            lowered = raw.lower()
            for name, _module, _desk_hash, _ui in DESKS:
                self.assertNotIn(name, lowered, sidecar)
            self.assertNotIn("heartbeat", lowered, sidecar)

    def test_capabilities_cover_linux_windows_and_macos(self) -> None:
        caps = ROOT / "desktop/src-tauri/capabilities"
        for name in (
            "gateway-opener.json",
            "gateway-dialog.json",
            "gateway-save.json",
            "gateway-zoom.json",
        ):
            payload = json.loads((caps / name).read_text(encoding="utf-8"))
            self.assertEqual(payload["platforms"], ["linux", "macOS", "windows"], name)
        opener = json.loads((caps / "gateway-opener.json").read_text(encoding="utf-8"))
        for host in (
            "http://127.0.0.1:*",
            "http://tauri.localhost",
            "https://tauri.localhost",
            "tauri://localhost",
        ):
            self.assertIn(host, opener["remote"]["urls"], host)

    def test_rust_and_webview_keep_every_desk_hash_on_every_os(self) -> None:
        rust = (ROOT / "desktop/src-tauri/src/main.rs").read_text(encoding="utf-8")
        ts = (ROOT / "webui/src/lib/desktop.test.ts").read_text(encoding="utf-8")
        urls = (ROOT / "webui/src/lib/external-url.ts").read_text(encoding="utf-8")
        self.assertIn("is_desktop_app_url", rust)
        self.assertIn("is_navin_shell_url", rust)
        self.assertIn(".on_navigation({", rust)
        self.assertIn(".on_new_window(move |url, _features|", rust)
        self.assertIn("apply_in_app_navigation", rust)
        self.assertIn("in_app_hash", rust)
        self.assertIn("ideNavigationHash", urls)
        for _name, _module, desk_hash, _ui in DESKS:
            self.assertIn(desk_hash, urls)
            for origin in (
                "http://tauri.localhost/",
                "https://tauri.localhost/",
                "tauri://localhost/",
                "http://127.0.0.1:8766/",
            ):
                needle = f"{origin}{desk_hash}"
                self.assertIn(needle, rust, needle)
                self.assertIn(needle, ts, needle)
        for desk_hash in WINDOWS_HASHES:
            windows = f"https://tauri.localhost/{desk_hash}"
            self.assertIn(windows, rust, windows)
            self.assertIn(windows, ts, windows)

    def test_webui_routes_vite_http_and_cli_name_the_same_desks(self) -> None:
        app = (ROOT / "webui/src/App.tsx").read_text(encoding="utf-8")
        vite = (ROOT / "webui/vite.config.ts").read_text(encoding="utf-8")
        http = (ROOT / "navin/webui/ws_http.py").read_text(encoding="utf-8")
        cli = (ROOT / "navin/cli/commands.py").read_text(encoding="utf-8")
        heartbeat = (ROOT / ".navin/HEARTBEAT.md").read_text(encoding="utf-8")
        en = json.loads(
            (ROOT / "webui/src/i18n/locales/en/common.json").read_text(encoding="utf-8")
        )
        seeds = en["thread"]["sessionInfo"]["createSeed"]
        for name, module, desk_hash, workspace in DESKS:
            self.assertIn(f'path === "/{name}"', app, name)
            self.assertIn(workspace, app, name)
            self.assertIn(f'"-m", "{module}"', vite, name)
            self.assertIn(f'r"^/api/{name}$"', http, name)
            self.assertIn(f'name="{name}"', cli, name)
            self.assertIn(f"navin-{name}-loop", cli, name)
            self.assertIn(f"from {module} import main as {name}_main", cli, name)
            watch = "follow" if name == "tenders" else "watch"
            self.assertIn(f"{name} action={watch}", heartbeat, name)
            self.assertIn(desk_hash, seeds[name], name)
            self.assertIn(f"{name} action=start", seeds[name], name)
        self.assertIn("LeadsScene.tsx", vite)
        self.assertIn("MarketingWorkspace.tsx", vite)

    def test_docs_cover_desktop_pages_for_every_desk(self) -> None:
        catalog = (ROOT / "site/front/src/lib/docs-catalog.ts").read_text(encoding="utf-8")
        back = (ROOT / "site/back/src/lib/docs-catalog.ts").read_text(encoding="utf-8")
        for name, _module, desk_hash, _ui in DESKS:
            en = ROOT / f"docs/navin_{name}/en/desktop.md"
            self.assertTrue(en.is_file(), name)
            text = en.read_text(encoding="utf-8")
            self.assertIn(desk_hash, text, name)
            self.assertIn("https://tauri.localhost", text, name)
            self.assertIn("tsc -p tsconfig.build.json", text, name)
            self.assertIn("Linux", text, name)
            self.assertIn("Windows", text, name)
            self.assertIn("macOS", text, name)
            self.assertNotIn("\u2014", text, name)
            self.assertNotIn("\u2013", text, name)
            self.assertIn(f"navin_{name}/en/desktop.md", catalog, name)
            self.assertIn(f"navin_{name}/en/desktop.md", back, name)

    def test_smoke_tests_import_the_five_desks(self) -> None:
        import_line = (
            "import navin.career.desk_cli, navin.leads.desk_cli, "
            "navin.marketing.desk_cli, navin.tenders.desk_cli, navin.trading.desk_cli"
        )
        sh = (ROOT / "packaging/smoke-test.sh").read_text(encoding="utf-8")
        ps1 = (ROOT / "packaging/windows/smoke-test.ps1").read_text(encoding="utf-8")
        self.assertIn(import_line, sh)
        self.assertIn(import_line, ps1)


if __name__ == "__main__":
    unittest.main()
