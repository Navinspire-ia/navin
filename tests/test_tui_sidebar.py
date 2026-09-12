# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Sidebar chrome: path wrap and mode-routing stay readable in a narrow column."""

from __future__ import annotations

import unittest

from navin.optional_live import live_modules_available
from navin.tui.hubs import provider_panel_label
from navin.tui.widgets import (
    account_side_text,
    context_line,
    context_meter,
    fit_path,
    wrap_path,
)


class WrapPathTests(unittest.TestCase):
    def test_keeps_a_short_path(self) -> None:
        self.assertEqual(wrap_path("/home/aymen", 40), "/home/aymen")

    def test_breaks_on_slash_not_mid_folder(self) -> None:
        path = "/home/aymen/projects/deploy7/navin-ai-v2"
        out = wrap_path(path, 24)
        self.assertIn("\n", out)
        self.assertNotIn("dep\nloy7", out)
        self.assertTrue(all(len(line) <= 24 for line in out.split("\n")))
        self.assertEqual("".join(out.split("\n")), path)

    def test_navin_projects_does_not_orphan_a_letter(self) -> None:
        path = "/home/aymen/NavinProjects"
        out = wrap_path(path, 20)
        self.assertNotEqual(out.split("\n")[-1], "s")
        self.assertEqual("".join(out.split("\n")), path)

    def test_normalizes_backslashes(self) -> None:
        self.assertEqual(wrap_path(r"C:\Users\aymen", 40), "C:/Users/aymen")


class ContextMeterTests(unittest.TestCase):
    def test_empty_without_window(self) -> None:
        self.assertEqual(context_meter(0, 0), ("", ""))

    def test_percent_and_width(self) -> None:
        bar, cap = context_meter(20_000, 200_000, cells=10)
        self.assertEqual(len(bar), 10)
        self.assertEqual(cap, "10% of 200k")
        self.assertEqual(bar.count("█"), 1)

    def test_one_line_fills_the_row(self) -> None:
        line = context_line(22_000, 200_000, 28)
        self.assertEqual(len(line), 28)
        self.assertIn("11% of 200k", line)
        self.assertNotIn("\n", line)

    def test_panel_width_is_used_in_full(self) -> None:
        line = context_line(22_000, 200_000, 32)
        self.assertEqual(len(line), 32)
        self.assertTrue(line.endswith("11% of 200k"))
        self.assertGreaterEqual(len(line) - len("11% of 200k") - 1, 16)


class HiddenNavinProviderTests(unittest.TestCase):
    def test_leftover_navin_slot_stays_off_the_providers_table(self) -> None:
        from navin.optional_live import live_modules_available
        from navin.tui.hubs import provider_rows

        if live_modules_available():
            self.skipTest("live account enabled")
        rows = provider_rows(
            {
                "providers": {
                    "navin": {
                        "apiKey": "sk-xxxxxxxxxxxxxxxx39dc",
                        "apiBase": "https://openrouter.ai/api/v1",
                    },
                    "openai": {"apiKey": "sk-xxxxxxxxxxxxxxxxabcd"},
                }
            }
        )
        keys = [str(row.key).lower() for row in rows]
        labels = [str(row.cells[1]).lower() for row in rows if len(row.cells) > 1]
        self.assertNotIn("navin", keys)
        self.assertNotIn("navin", labels)
        self.assertIn("openai", keys)


class ProviderPanelLabelTests(unittest.TestCase):
    def test_shows_openai_and_configured_count(self) -> None:
        data = {
            "providers": {
                "openai": {"apiKey": "sk-xxxxxxxxxxxxxxxx39dc"},
                "nvidia": {"apiKey": "nvapi-xxxxxxxxxxxxw0em"},
                "ollama": {"apiBase": "http://localhost:11434/v1"},
                "zai": {"apiKey": "zai-xxxxxxxxxxxxxxxxZdgf"},
            }
        }
        label = provider_panel_label(data, "openai")
        self.assertIn("OpenAI", label)
        self.assertIn("4", label)
        self.assertNotIn("z-ai", label)
        self.assertNotIn("Z.AI", label)


class AccountTextTests(unittest.TestCase):
    def test_keeps_plan_price_without_spend(self) -> None:
        payload = {
            "connected": True,
            "plan": "pro",
            "plan_label": "Pro",
            "plan_price_usd": 69,
            "usage": {
                "used_percent": 1,
                "spent_micro_usd": 710_000,
                "budget_micro_usd": 64_000_000,
            },
        }
        side = account_side_text(payload)
        self.assertEqual(side, "• Pro  1%  $69/month")
        self.assertNotIn("$0.71", side)
        self.assertNotIn("$64.00", side)
        self.assertNotIn(" / $", side)

    def test_disconnected_is_empty_in_the_panel(self) -> None:
        self.assertEqual(account_side_text({"connected": False}), "")


class SidebarRenderTests(unittest.IsolatedAsyncioTestCase):
    async def test_model_shows_context_without_account_card(self) -> None:
        from textual.app import App, ComposeResult
        from textual.widgets import Button, Static

        from navin.tui.widgets import Sidebar

        class Host(App):
            CSS = "Sidebar { display: block; width: 36; height: 1fr; }"

            def compose(self) -> ComposeResult:
                yield Sidebar()

        app = Host()
        payload = {
            "connected": True,
            "plan": "pro",
            "plan_label": "Pro",
            "plan_price_usd": 69,
            "usage": {
                "used_percent": 1,
                "spent_micro_usd": 710_000,
                "budget_micro_usd": 64_000_000,
            },
        }
        async with app.run_test(size=(120, 40)) as pilot:
            side = app.query_one(Sidebar)
            side.set_model(
                slug="z-ai/glm-5.3-flash",
                tools=34,
                tools_loaded=65,
                billed=47_808,
                used=22_000,
                window=200_000,
            )
            side.set_account(account_side_text(payload))
            side.set_workspace("/home/aymen/NavinProjects")
            await pilot.pause()
            model = side.query_one("#side-model").query_one(".card-body", Static)
            meter = side.query_one("#side-model").query_one(".card-meter", Static)
            workspace = side.query_one("#side-workspace").query_one(".card-body", Static)
            head = side.query_one("#side-workspace").query_one(".card-head", Static)
            self.assertIn("z-ai/glm-5.3-flash", model.content)
            self.assertIn("billed 47,808", model.content)
            self.assertNotIn("tools", model.content)
            self.assertIn("% of", meter.content)
            self.assertTrue(meter.content.endswith("11% of 200k"))
            self.assertEqual("".join(workspace.content.split("\n")), "/home/aymen/NavinProjects")
            self.assertNotIn("...", workspace.content)
            self.assertFalse(side.query_one("#side-workspace").has_class("-boxed"))
            self.assertNotIn("Workspace", head.content)
            self.assertNotIn("ctrl+w", head.content)
            if live_modules_available():
                account = side.query_one("#side-account")
                self.assertTrue(account.has_class("-accent"))
                account_head = account.query_one(".card-head", Static)
                account_body = account.query_one(".card-body", Static)
                self.assertIn("Account", account_head.content)
                self.assertIn("$primary", account_head.content)
                self.assertIn("ctrl+d", account_head.content)
                self.assertIn("• Pro", account_body.content)
                self.assertIn("$primary", account_body.content)
            else:
                self.assertEqual(list(side.query("#side-account")), [])
            self.assertGreaterEqual(side.query_one("#side-mode").size.height, 1)
            side.set_version("2.0.1")
            await pilot.pause()
            hide = side.query_one("#side-panel", Button)
            self.assertEqual(str(hide.label), "Hide panel  ctrl+b")
            version = side.query_one("#side-version", Static)
            self.assertGreaterEqual(version.size.height, 1)
            self.assertGreaterEqual(side.query_one("#side-foot").size.height, 1)
            self.assertIn("navin", version.content)
            self.assertIn("v2.0.1", version.content)

    async def test_footer_stays_visible_on_a_short_terminal(self) -> None:
        from textual.app import App, ComposeResult
        from textual.widgets import Static

        from navin.tui.widgets import Sidebar

        class Host(App):
            CSS = "Sidebar { display: block; width: 36; height: 1fr; }"

            def compose(self) -> ComposeResult:
                yield Sidebar()

        app = Host()
        async with app.run_test(size=(80, 24)) as pilot:
            side = app.query_one(Sidebar)
            side.set_account("• Pro  1%  $69/month")
            side.set_version("2.0.1")
            side.set_workspace("/home/aymen/projects/deploy7/navin-ai-v2")
            await pilot.pause()
            foot = side.query_one("#side-foot")
            version = side.query_one("#side-version", Static)
            self.assertGreaterEqual(foot.size.height, 4)
            self.assertGreaterEqual(version.size.height, 1)
            if live_modules_available():
                self.assertEqual(len(list(side.query("#side-account"))), 1)
            else:
                self.assertEqual(list(side.query("#side-account")), [])
            self.assertIn("navin", version.content)
            self.assertIn("v2.0.1", version.content)


class FitPathTests(unittest.TestCase):
    def test_keeps_a_short_path(self) -> None:
        self.assertEqual(fit_path("/home/aymen", 40), "/home/aymen")

    def test_keeps_leaf_and_head(self) -> None:
        path = "/home/aymen/projects/deploy7/navin-ai-v2"
        out = fit_path(path, 28)
        self.assertLessEqual(len(out), 28)
        self.assertTrue(out.endswith("navin-ai-v2"))
        self.assertIn("...", out)
        self.assertNotIn("dep...loy", out)


class CompactShortcutTests(unittest.TestCase):
    def test_ctrl_becomes_caret(self) -> None:
        from navin.tui.widgets import compact_shortcut

        self.assertEqual(compact_shortcut("ctrl+p"), "^p")
        self.assertEqual(compact_shortcut("ctrl+b"), "^b")
        self.assertEqual(compact_shortcut("ctrl+shift+g"), "^G")
        self.assertEqual(compact_shortcut("F2"), "F2")


class DockBarTests(unittest.IsolatedAsyncioTestCase):
    async def test_path_and_shortcuts_share_one_row(self) -> None:
        from textual.app import App, ComposeResult
        from textual.widgets import Static

        from navin.tui.widgets import DockBar, DockHint

        class Host(App):
            CSS = "DockBar { width: 1fr; height: 1; }"

            def compose(self) -> ComposeResult:
                yield DockBar(id="dock")

        app = Host()
        async with app.run_test(size=(120, 8)) as _pilot:
            dock = app.query_one("#dock", DockBar)
            dock.set_path("/home/aymen/projects/deploy7/navin-ai-v2")
            await _pilot.pause()
            path = dock.query_one("#dock-path", Static)
            self.assertEqual(dock.size.height, 1)
            self.assertIn("navin-ai-v2", path.content)
            self.assertNotIn("ctrl+p", path.content)
            cmds = dock.query_one("#dock-commands", DockHint)
            self.assertEqual(cmds.key, "ctrl+p")
            self.assertEqual(cmds.label, "commands")
            self.assertIn("^p", str(cmds.content))
            self.assertNotIn("ctrl+p", str(cmds.content))
            self.assertTrue(any(h.key == "ctrl+b" for h in dock.query(DockHint)))
            dock.set_panel(True)
            self.assertTrue(dock.has_class("-hidden"))
            dock.set_panel(False)
            self.assertFalse(dock.has_class("-hidden"))


if __name__ == "__main__":
    unittest.main()
