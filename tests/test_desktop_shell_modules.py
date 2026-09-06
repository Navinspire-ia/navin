"""Lock New chat, Code and Studio desks on every Tauri host.

Linux AppImage / deb / rpm / pacman, Windows WebView2 and macOS x64 / arm share the
same hash routes. A missing string here is a missing desk in the WebView.
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

from navin.webui.sidebar_state import normalize_webui_sidebar_state

ROOT = Path(__file__).resolve().parents[1]

SHELL_HASHES = (
    "#/new",
    "#/chat/",
    "#/code",
    "#/tenders",
    "#/career",
    "#/trading",
    "#/marketing",
    "#/leads",
    "#/crm",
)

STUDIO_MODULES = (
    "tenders",
    "career",
    "trading",
    "marketing",
    "leads",
    "crm",
)


class DesktopShellModulesTest(unittest.TestCase):
    def test_sidebar_state_keeps_every_requested_module(self) -> None:
        keys = {f"websocket:{name}": name for name in ("chat", "dev", *STUDIO_MODULES)}
        state = normalize_webui_sidebar_state({"module_by_key": keys})
        for name in ("chat", "dev", *STUDIO_MODULES):
            self.assertEqual(state["module_by_key"][f"websocket:{name}"], name, name)

    def test_webui_routes_and_sidebar_handlers_exist(self) -> None:
        app = (ROOT / "webui/src/App.tsx").read_text(encoding="utf-8")
        sidebar = (ROOT / "webui/src/components/Sidebar.tsx").read_text(encoding="utf-8")
        self.assertIn('if (path === "/new")', app)
        self.assertIn('if (path === "/code" || path === "/dev")', app)
        self.assertIn('if (path === "/crm" || path.startsWith("/crm/"))', app)
        for name in STUDIO_MODULES:
            if name == "crm":
                self.assertIn("onOpenCrmStudio", app)
                self.assertIn("onOpenCrmStudio", sidebar)
                continue
            handler = f"onOpen{name.capitalize()}Studio"
            if name == "leads":
                handler = "onOpenLeadsStudio"
            self.assertIn(handler, app, handler)
            self.assertIn(handler, sidebar, handler)
        self.assertIn("onNewChat", sidebar)
        self.assertIn("onOpenDev", sidebar)

    def test_tauri_keeps_every_hash_on_linux_windows_and_macos(self) -> None:
        rust = (ROOT / "desktop/src-tauri/src/main.rs").read_text(encoding="utf-8")
        self.assertIn("requested_shell_hashes_stay_in_the_webview_on_every_os", rust)
        for host in (
            "http://127.0.0.1:8766",
            "http://tauri.localhost",
            "https://tauri.localhost",
            "tauri://localhost",
        ):
            self.assertIn(host, rust, host)
        for hash_route in SHELL_HASHES:
            self.assertIn(hash_route, rust, hash_route)
        self.assertIn("#/crm/contacts", rust)
        urls = (ROOT / "webui/src/lib/external-url.ts").read_text(encoding="utf-8")
        for hash_route in SHELL_HASHES:
            self.assertIn(hash_route.rstrip("/"), urls, hash_route)
        desktop = (ROOT / "webui/src/lib/desktop.ts").read_text(encoding="utf-8")
        self.assertIn("isDesktopAppUrl", desktop)
        self.assertIn("tauri.localhost", desktop)

    def test_opener_and_sidecars_are_identical_across_os(self) -> None:
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
        bundle = json.loads(
            (ROOT / "desktop/src-tauri/tauri.conf.json").read_text()
        )["bundle"]
        self.assertIn("appimage", bundle["linux"])
        self.assertIn("deb", bundle["linux"])
        self.assertIn("rpm", bundle["linux"])
        linux = (ROOT / "packaging/linux/build-appimage.sh").read_text(encoding="utf-8")
        self.assertIn("AppImage", linux)
        self.assertIn(".deb", linux)
        self.assertIn(".rpm", linux)
        self.assertIn(".pkg.tar.zst", linux)
        self.assertIn("pack_pacman.py", linux)
        self.assertIn("x86_64", linux)
        self.assertIn("aarch64", linux)
        macos = (ROOT / "packaging/macos/build-desktop.sh").read_text(encoding="utf-8")
        self.assertTrue(
            "arm64" in macos or "aarch64" in macos,
            "macOS arm build missing",
        )
        self.assertTrue("x64" in macos or "x86_64" in macos, "macOS x64 build missing")
        windows = (ROOT / "packaging/windows/build-desktop.ps1").read_text(
            encoding="utf-8"
        )
        self.assertIn("nsis", windows.lower())
        for sidecar in (
            "tauri.sidecar.linux.conf.json",
            "tauri.sidecar.windows.conf.json",
            "tauri.sidecar.macos.conf.json",
            "tauri.sidecar.macos-x64.conf.json",
        ):
            raw = (ROOT / "desktop/src-tauri" / sidecar).read_text(encoding="utf-8")
            for name in STUDIO_MODULES:
                self.assertNotIn(name, raw.lower(), f"{sidecar} must not special-case {name}")

    def test_client_module_list_matches_server_allowlist(self) -> None:
        client = (ROOT / "webui/src/lib/chat-module.ts").read_text(encoding="utf-8")
        server = (ROOT / "navin/webui/sidebar_state.py").read_text(encoding="utf-8")
        names = re.findall(r'"([a-z]+)"', client.split("] as const")[0])
        self.assertIn("tenders", names)
        self.assertIn("career", names)
        self.assertIn("crm", names)
        for name in names:
            if name == "code":
                continue
            self.assertIn(f'"{name}"', server, name)

    def test_host_chrome_preview_is_wired_for_browser_parity(self) -> None:
        """Browser can paint the Tauri chrome without a desktop rebuild."""
        host = (ROOT / "webui/src/lib/host-chrome.ts").read_text(encoding="utf-8")
        app = (ROOT / "webui/src/App.tsx").read_text(encoding="utf-8")
        main = (ROOT / "webui/src/main.tsx").read_text(encoding="utf-8")
        bootstrap = (ROOT / "webui/src/lib/bootstrap.ts").read_text(encoding="utf-8")
        self.assertIn("shouldShowHostChrome", host)
        self.assertIn("hostChrome", bootstrap)
        self.assertIn("shouldShowHostChrome", app)
        self.assertIn("initHostChromePreview", main)
        parity = (ROOT / "packaging/desktop-parity.sh").read_text(encoding="utf-8")
        self.assertIn("hostChrome=1", parity)
        self.assertIn("desktop-parity", (ROOT / "Makefile").read_text(encoding="utf-8"))

    def test_editor_gutter_metrics_do_not_inflate_the_hidden_spacer(self) -> None:
        """Forced height on .cm-gutterElement makes cm-activeLineGutter
        paint on N+1 while the caret stays on N. Same editor in ?hostChrome=1.
        """
        metrics = (ROOT / "webui/src/components/dev/editorLineMetrics.ts").read_text(
            encoding="utf-8"
        )
        self.assertIn(".cm-gutterElement", metrics)
        self.assertIn('minHeight: "0"', metrics)
        self.assertIn("collapseGutterSpacers", metrics)
        self.assertIn("snapClientYToPaintedLines", metrics)
        gutter_block = metrics.split('".cm-gutterElement"', 1)[1].split("}", 1)[0]
        self.assertNotIn("height: LINE", gutter_block)
        self.assertNotIn("minHeight: LINE", gutter_block)

    def test_code_explorer_starts_hidden(self) -> None:
        """The file tree is closed until the user opens it (toolbar Explorer)."""
        workbench = (ROOT / "webui/src/components/dev/DevWorkbench.tsx").read_text(
            encoding="utf-8"
        )
        self.assertIn("const [explorerOpen, setExplorerOpen] = useState(false);", workbench)
        self.assertIn('tx("dev.showExplorer"', workbench)
        rail = workbench.split('key: "project"', 1)[1]
        ext = rail.find('key: "extensions"')
        templates = rail.find('key: "templates"')
        self.assertNotEqual(ext, -1, "rail must list Extensions")
        self.assertNotEqual(templates, -1)
        self.assertLess(ext, templates)

    def test_terminal_maximize_keeps_workbench_toolbar(self) -> None:
        """Terminal full mode fills the editor body; the toolbar menu stays."""
        workbench = (ROOT / "webui/src/components/dev/DevWorkbench.tsx").read_text(
            encoding="utf-8"
        )
        toolbar = workbench.find('data-testid="dev-workbench-toolbar"')
        hidden = workbench.find('terminalOpen && terminalMaximized && "hidden"')
        self.assertNotEqual(toolbar, -1)
        self.assertNotEqual(hidden, -1)
        self.assertLess(toolbar, hidden)

    def test_choice_card_is_not_gated_on_host_chrome(self) -> None:
        """?hostChrome=1 only paints the Tauri chrome. The Questions card,
        including Other, is the same ThreadShell path as the browser UI."""
        shell = (ROOT / "webui/src/components/thread/ThreadShell.tsx").read_text(
            encoding="utf-8"
        )
        prompt = (ROOT / "webui/src/components/thread/ChoicePrompt.tsx").read_text(
            encoding="utf-8"
        )
        css = (ROOT / "webui/src/globals.css").read_text(encoding="utf-8")
        self.assertIn("ChoicePrompt", shell)
        self.assertNotIn("showHostChrome && pendingChoices", shell)
        self.assertNotIn("hostChrome && pendingChoices", shell)
        self.assertIn("OTHER_CHOICE_ID", prompt)
        self.assertIn("data-choice-prompt-scroll", prompt)
        self.assertIn("[data-choice-prompt-scroll]", css)

    def test_reasoning_stays_visible_like_opencode(self) -> None:
        """Thinking is one summary card with click-to-expand, not a row dump."""
        cluster = (
            ROOT / "webui/src/components/thread/AgentActivityCluster.tsx"
        ).read_text(encoding="utf-8")
        card = (
            ROOT / "webui/src/components/thread/activity/ReasoningRow.tsx"
        ).read_text(encoding="utf-8")
        plan = (ROOT / "webui/src/components/thread/ThreadPlanPanel.tsx").read_text(
            encoding="utf-8"
        )
        self.assertIn("ReasoningCard", cluster)
        self.assertNotIn("<ReasoningRow", cluster)
        self.assertIn('data-testid="activity-reasoning-card"', card)
        self.assertNotIn("rounded-xl border", card)
        self.assertIn("line-clamp-6", card)
        self.assertIn("text-muted-foreground", card)
        self.assertIn('data-testid="activity-reasoning-copy"', card)
        prose = (ROOT / "webui/src/components/thread/activity/reasoningProse.ts").read_text(
            encoding="utf-8"
        )
        css = (ROOT / "webui/src/globals.css").read_text(encoding="utf-8")
        self.assertIn("reasoning-prose", prose)
        self.assertIn(".markdown-content.reasoning-prose", css)
        self.assertIn(".markdown-content.reasoning-prose strong", css)
        self.assertIn("reasoningExpand", card)
        self.assertIn("planItemTone", plan)
        journal = (
            ROOT / "webui/src/components/thread/activity/ActivityJournal.tsx"
        ).read_text(encoding="utf-8")
        thread = (ROOT / "webui/src/components/thread/ThreadMessages.tsx").read_text(
            encoding="utf-8"
        )
        self.assertIn("ActivityJournal", cluster)
        self.assertIn("buildJournalTimeline", cluster)
        self.assertIn("JournalNowLine", cluster)
        self.assertIn("ActivityDigest", cluster)
        self.assertIn("isLatestTurn", cluster)
        self.assertIn("isLatestTurn={index === latestActivityIndex}", thread)
        self.assertIn('data-testid="activity-task-log"', journal)
        self.assertIn('data-testid="activity-journal-open"', journal)
        self.assertIn('data-testid="activity-journal-row"', journal)
        self.assertIn('data-testid="activity-journal-aside"', journal)
        self.assertIn('data-testid="activity-digest"', journal)
        self.assertIn('data-testid="activity-now-line"', journal)
        self.assertIn("JOURNAL_TARGET_CLASS", journal)
        self.assertIn("text-foreground/92", journal)
        self.assertIn("--tone-explore", journal)
        self.assertIn("--tone-read", journal)
        self.assertIn("--tone-explore:", css)
        self.assertIn("--tone-read:", css)
        # Activity detail preference (auto / expanded / compact / digest),
        # time by phase under the header, task titles that open the board.
        self.assertIn("useActivityMode", cluster)
        self.assertIn("ActivityPhaseBar", cluster)
        self.assertIn('data-testid="activity-phase-split"', journal)
        self.assertIn('data-testid="activity-journal-task"', journal)
        self.assertIn("requestOpenBoardTask", journal)
        events = (ROOT / "webui/src/lib/workbench-events.ts").read_text(
            encoding="utf-8"
        )
        board = (ROOT / "webui/src/components/dev/DevBoardPanel.tsx").read_text(
            encoding="utf-8"
        )
        self.assertIn("OPEN_BOARD_TASK_EVENT", events)
        self.assertIn("consumePendingBoardTaskFocus", board)
        self.assertIn("data-board-task-id", board)
        prefs = (ROOT / "webui/src/lib/local-preferences.ts").read_text(
            encoding="utf-8"
        )
        self.assertIn('"auto" | "expanded" | "compact" | "digest"', prefs)


    def test_electron_and_tauri_mark_the_sidecar_as_desktop(self) -> None:
        electron = (ROOT / "desktop-electron/main.js").read_text(encoding="utf-8")
        self.assertIn("NAVIN_DESKTOP_PID", electron)
        self.assertIn("NAVIN_DESKTOP_APP", electron)
        self.assertIn("show: true", electron)
        require_at = electron.index('require("electron")')
        hint_at = electron.index('process.env.ELECTRON_OZONE_PLATFORM_HINT = "x11"')
        self.assertLess(hint_at, require_at, "NVIDIA Wayland ozone must be set before requiring electron")
        rust = (ROOT / "desktop/src-tauri/src/main.rs").read_text(encoding="utf-8")
        self.assertIn("NAVIN_DESKTOP_PID", rust)
        self.assertIn("NAVIN_DESKTOP_APP", rust)


if __name__ == "__main__":
    unittest.main()
