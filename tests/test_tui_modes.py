# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Composer mode routing stays internal; the transcript shows the prompt."""

from __future__ import annotations

import unittest

from navin.tui.modes import display_user_text, inbound_for_submit, route_text


class RouteTextTests(unittest.TestCase):
    def test_agent_prefixes_free_text(self) -> None:
        self.assertEqual(route_text("agent", "salut ca va ?"), "/forge salut ca va ?")

    def test_leaves_explicit_slash_alone(self) -> None:
        self.assertEqual(route_text("agent", "/help"), "/help")
        self.assertEqual(route_text("plan", "/forge already"), "/forge already")

    def test_chat_is_plain(self) -> None:
        self.assertEqual(route_text("chat", "hello"), "hello")

    def test_followup_in_agent_drops_forge(self) -> None:
        payload, followup = inbound_for_submit(
            "agent", "fais un petit test de grep", turn_active=True
        )
        self.assertTrue(followup)
        self.assertEqual(payload, "fais un petit test de grep")
        self.assertNotIn("/forge", payload)

    def test_fresh_agent_turn_keeps_forge(self) -> None:
        payload, followup = inbound_for_submit(
            "agent", "fais un petit test de grep", turn_active=False
        )
        self.assertFalse(followup)
        self.assertEqual(payload, "/forge fais un petit test de grep")


class AssistantPreviewTests(unittest.TestCase):
    def test_keeps_the_first_sentence(self) -> None:
        from navin.tui.widgets import assistant_preview

        text = "Le script n'ecrit plus dans COPY. " + ("suite " * 80)
        self.assertEqual(assistant_preview(text), "Le script n'ecrit plus dans COPY.")

    def test_truncates_a_long_line(self) -> None:
        from navin.tui.widgets import assistant_preview

        text = "mot " * 80
        out = assistant_preview(text, limit=40)
        self.assertLessEqual(len(out), 42)
        self.assertTrue(out.endswith("…"))

    def test_detects_a_question_to_the_user(self) -> None:
        from navin.tui.widgets import looks_like_client_prompt

        self.assertTrue(looks_like_client_prompt("Quelle option veux-tu ?"))
        self.assertTrue(
            looks_like_client_prompt("Choisis :\n1. continuer\n2. arreter")
        )
        self.assertFalse(
            looks_like_client_prompt(
                "Le script n'ecrit plus dans COPY. " + ("Verification ensuite. " * 20)
            )
        )


class ReadableAssistantMarkdownTests(unittest.TestCase):
    def test_breaks_a_wall_of_sentences(self) -> None:
        from navin.tui.widgets import readable_assistant_markdown

        wall = (
            "Le script n'ecrit plus dans COPY. "
            "Je verifie le CHECKPOINT ensuite. "
            "Le drop a ete recree. "
        ) * 4
        out = readable_assistant_markdown(wall)
        self.assertIn("\n\n", out)
        self.assertTrue(out.startswith("Le script"))
        self.assertIn("Le drop", out)

    def test_keeps_real_markdown_blocks(self) -> None:
        from navin.tui.widgets import readable_assistant_markdown

        md = "# Titre\n\nUn paragraphe."
        self.assertEqual(readable_assistant_markdown(md), md)


class DisplayUserTextTests(unittest.TestCase):
    def test_hides_forge_and_keeps_prompt(self) -> None:
        self.assertEqual(display_user_text("/forge salut ca va ?"), "salut ca va ?")
        self.assertEqual(display_user_text("  /ask  explain this  "), "explain this")
        self.assertEqual(
            display_user_text("/blueprint   plan the CRM"),
            "plan the CRM",
        )

    def test_keeps_bare_routing_command(self) -> None:
        self.assertEqual(display_user_text("/forge"), "/forge")
        self.assertEqual(display_user_text("/forge  "), "/forge")

    def test_leaves_plain_and_other_slashes(self) -> None:
        self.assertEqual(display_user_text("salut ca va ?"), "salut ca va ?")
        self.assertEqual(display_user_text("/help me"), "/help me")
        self.assertEqual(display_user_text("/settings"), "/settings")


class TranscriptStreamTests(unittest.IsolatedAsyncioTestCase):
    async def test_turns_show_names_without_cards(self) -> None:
        from textual.app import App, ComposeResult
        from textual.widgets import Static

        from navin.tui.widgets import AssistantMessage, UserMessage

        class Host(App):
            def compose(self) -> ComposeResult:
                yield UserMessage("salut bro")
                yield AssistantMessage()

        app = Host()
        async with app.run_test(size=(80, 20)) as _pilot:
            user = app.query_one(UserMessage)
            bot = app.query_one(AssistantMessage)
            self.assertEqual(user.query_one(".user-head", Static).content, "you")
            self.assertIn("salut bro", user.query_one(".user-body", Static).content)
            self.assertEqual(bot.query_one(".assistant-head", Static).content, "navin")

        labeled = AssistantMessage("glm-5.3-flash")
        class Host2(App):
            def compose(self) -> ComposeResult:
                yield labeled

        app2 = Host2()
        async with app2.run_test(size=(80, 10)) as _pilot:
            self.assertEqual(
                app2.query_one(AssistantMessage).query_one(".assistant-head", Static).content,
                "glm-5.3-flash",
            )

    async def test_long_user_paste_stays_a_chip(self) -> None:
        from textual.app import App, ComposeResult
        from textual.widgets import Static

        from navin.tui.widgets import UserMessage

        blob = "x" * 1148

        class Host(App):
            def compose(self) -> ComposeResult:
                yield UserMessage(f"regarde\n{blob}")

        app = Host()
        async with app.run_test(size=(80, 20)) as pilot:
            user = app.query_one(UserMessage)
            self.assertEqual(
                user.query_one(".user-paste-chip", Static).content,
                "[Pasted Content 1148 chars]",
            )
            self.assertIn("regarde", user.query_one(".user-body", Static).content)
            self.assertEqual(user.copy_text(), f"regarde\n{blob}")
            await pilot.click(user)
            self.assertTrue(user.has_class("-expanded"))

    async def test_thinking_stays_folded_until_done(self) -> None:
        from textual.app import App, ComposeResult
        from textual.widgets import Static

        from navin.tui.theme import NAVIN_DARK
        from navin.tui.widgets import AssistantMessage

        block = AssistantMessage()

        class Host(App):
            def compose(self) -> ComposeResult:
                yield block

        app = Host()
        app.register_theme(NAVIN_DARK)
        app.theme = "navin"
        long = "Le script n'ecrit plus dans COPY. " + ("Verification ensuite. " * 40)
        async with app.run_test(size=(80, 16)) as _pilot:
            await block.delta(long)
            preview = str(block.query_one(".assistant-preview", Static).content)
            self.assertIn("Le script", preview)
            self.assertIn("clic", preview)
            self.assertFalse(block._open)
            self.assertFalse(block.finished)

            await block.finish(latency_ms=12, model="glm", preset=None)
            self.assertTrue(block.finished)
            self.assertTrue(block._open)
            preview = block.query_one(".assistant-preview", Static)
            self.assertFalse(preview.display)
            self.assertEqual(str(preview.content).strip(), "")

            await block.set_text("Quelle option veux-tu ?")
            self.assertTrue(block._open)

    async def test_tool_head_markup_stays_valid(self) -> None:
        from textual.app import App, ComposeResult

        from navin.tui.theme import NAVIN_DARK
        from navin.tui.widgets import ToolCall

        class Host(App):
            def compose(self) -> ComposeResult:
                yield ToolCall("c1", "list_dir", {"path": "."})

        app = Host()
        app.register_theme(NAVIN_DARK)
        app.theme = "navin"
        async with app.run_test(size=(80, 12)) as _pilot:
            tool = app.query_one(ToolCall)
            tool.apply(phase="end", result="ok")
            await _pilot.pause()
            tool.apply(phase="error", error="boom")
            await _pilot.pause()
            self.assertIn("List", tool._head_text())
            self.assertIn("Failed: List .", tool._head_text())
            self.assertNotIn("args:", tool._head_text())
            self.assertNotIn("{", tool._head_text())
            tool.apply(phase="end", result="PASS - no lint errors\n2 passed")
            await _pilot.pause()
            self.assertTrue(tool._open)
            self.assertTrue(tool.query_one(".tool-body").display)
            self.assertIn("PASS", str(tool.query_one(".tool-body").content))


class ToolColorTests(unittest.TestCase):
    def test_families_are_not_the_same_blue(self) -> None:
        from navin.tui.widgets import tool_color

        grep = tool_color("grep")
        exec_ = tool_color("exec")
        patch = tool_color("apply_patch")
        board = tool_color("board")
        self.assertNotEqual(grep, exec_)
        self.assertNotEqual(exec_, patch)
        self.assertNotEqual(patch, board)
        self.assertNotEqual(grep, board)

    def test_paths_in_args_are_bold_and_colored(self) -> None:
        from navin.tui.widgets import tool_args_markup

        markup = tool_args_markup({"path": "navin/webui/project_insights.py", "limit": 20})
        self.assertIn("[b #8FBC8F]navin/webui/project_insights.py[/]", markup)
        self.assertIn("[b #FF8F1C]20[/]", markup)
        self.assertNotIn("[/b]", markup)
        self.assertNotIn("#0369FF", markup)

    def test_commands_and_patterns_use_punchy_colors(self) -> None:
        from navin.tui.widgets import tool_args_markup, tool_color

        markup = tool_args_markup({"command": "ls -la", "pattern": "TODO"})
        self.assertIn("[b #FFB000]ls -la[/]", markup)
        self.assertIn("[b #FF2E93]TODO[/]", markup)
        command = tool_args_markup({"command": "cd /tmp/unified_deploy && python3 setup.py"})
        self.assertIn("[b #FFB000]", command)
        self.assertNotIn("#8FBC8F", command)
        washed = {"#54D4CD", "#9A9A9A", "#A3A3A3", "#B0B0B0", "#E6E6E6", "#F2F2F2", "#86EFAC", "#67E8F9"}
        for name in ("read_file", "grep", "exec", "apply_patch", "board", "git", "ask_user"):
            self.assertNotIn(tool_color(name), washed)

    def test_json_args_do_not_leak_markup(self) -> None:
        from navin.tui.widgets import ToolCall
        from navin.utils.tool_hints import format_tool_detail

        tool = ToolCall(
            "c1",
            "apply_patch",
            {"edits": [{"action": "add", "new_text": "hello"}]},
        )
        tool.phase = "end"
        head = tool._head_text()
        self.assertNotIn("[/dim]", head)
        self.assertIn("Edit", head)
        self.assertNotIn("apply_patch", head)
        self.assertNotIn("{", head)
        body = format_tool_detail(
            "apply_patch",
            {"edits": [{"action": "add", "new_text": "hello"}]},
            result={"ok": True},
        )
        self.assertNotIn("{", body)
        self.assertTrue(body)
        self.assertNotIn("args:", body)


class ToolClusterTests(unittest.IsolatedAsyncioTestCase):
    async def test_reads_and_searches_fold_under_explored(self) -> None:
        from textual.app import App, ComposeResult

        from navin.tui.theme import NAVIN_DARK
        from navin.tui.widgets import AssistantMessage, ToolCall, ToolCluster

        block = AssistantMessage("navin")

        class Host(App):
            def compose(self) -> ComposeResult:
                yield block

        app = Host()
        app.register_theme(NAVIN_DARK)
        app.theme = "navin"
        async with app.run_test(size=(80, 20)) as _pilot:
            await block.tool_event(
                "r1", "read_file", "end", {"path": "useAccount.ts"}, "ok", None, None
            )
            await block.tool_event(
                "g1",
                "grep",
                "end",
                {"pattern": "NavinClient", "path": "navin-client.ts"},
                "ok",
                None,
                None,
            )
            await block.tool_event(
                "e1", "exec", "end", {"command": "git status"}, "ok", None, None
            )
            clusters = list(block.query(ToolCluster))
            self.assertEqual(len(clusters), 1)
            self.assertEqual(clusters[0].kind, "explore")
            self.assertIn("Explored", clusters[0]._head_text())
            heads = [tool._head_text() for tool in clusters[0].tools]
            self.assertTrue(any("Read useAccount.ts" in head for head in heads))
            self.assertTrue(any("Search NavinClient in navin-client.ts" in head for head in heads))
            self.assertTrue(heads[0].startswith("├ ") or heads[-1].startswith("└ "))
            runs = [
                tool
                for tool in block.query(ToolCall)
                if tool.cluster_kind == ""
            ]
            self.assertEqual(len(runs), 1)
            self.assertIn("git status", runs[0]._head_text())

    async def test_edit_diff_stays_open_without_a_click(self) -> None:
        from textual.app import App, ComposeResult

        from navin.tui.theme import NAVIN_DARK
        from navin.tui.widgets import AssistantMessage, ToolCluster

        block = AssistantMessage("navin")

        class Host(App):
            def compose(self) -> ComposeResult:
                yield block

        diff = (
            "@@ -1,2 +1,3 @@\n"
            " import asyncio\n"
            "+import queue\n"
            " from pathlib import Path\n"
        )
        app = Host()
        app.register_theme(NAVIN_DARK)
        app.theme = "navin"
        async with app.run_test(size=(80, 24)) as _pilot:
            await block.tool_event(
                "e1",
                "edit_file",
                "end",
                {"path": "navin/tui/app.py"},
                "ok",
                None,
                None,
            )
            await block.note_file_edit("navin/tui/app.py", 1, 0, diff=diff, call_id="e1", kind="edit")
            await block.finish(latency_ms=12, model="glm", preset=None)
            cluster = block.query_one(ToolCluster)
            self.assertEqual(cluster.kind, "edit")
            self.assertIn("Edited", cluster._head_text())
            self.assertIn("1 file", cluster._head_text())
            self.assertIn("+1", cluster._head_text())
            tool = cluster.tools[0]
            self.assertTrue(tool._open)
            self.assertTrue(tool.query_one(".tool-body").display)
            body = str(tool.query_one(".tool-body").content)
            self.assertIn("import queue", body)
            self.assertTrue(any("on #0f6b38" in str(span.style).lower() for span in tool.query_one(".tool-body").content.spans))


class UpdateOfferTests(unittest.IsolatedAsyncioTestCase):
    def test_slash_and_palette_expose_update(self) -> None:
        from navin.tui.app import _TUI_SLASH

        self.assertTrue(any(row["command"] == "/update" for row in _TUI_SLASH))

    async def test_offer_names_the_version(self) -> None:
        from textual.app import App, ComposeResult
        from textual.widgets import Static

        from navin.tui.theme import NAVIN_DARK
        from navin.tui.widgets import UpdateOffer

        class Host(App):
            def compose(self) -> ComposeResult:
                yield UpdateOffer("2.0.3", "navin 2.0.3 is available")

        app = Host()
        app.register_theme(NAVIN_DARK)
        app.theme = "navin"
        async with app.run_test(size=(80, 8)) as _pilot:
            card = app.query_one(UpdateOffer)
            shown = str(card.content) if isinstance(card, Static) else str(card)
            self.assertIn("2.0.3", shown)
            self.assertIn("/update", shown)


class PickerScreenTests(unittest.IsolatedAsyncioTestCase):
    def test_clips_long_session_titles(self) -> None:
        from navin.tui.screens import PickerScreen

        long = "Scope reminder: the request names these targets: ~/projects/deploy7/db"
        self.assertLessEqual(len(PickerScreen._clip(long, 44)), 44)
        self.assertTrue(PickerScreen._clip(long, 44).endswith("…"))

    async def test_long_list_can_scroll(self) -> None:
        from textual.app import App
        from textual.widgets import OptionList

        from navin.tui.screens import PickerScreen, PickItem
        from navin.tui.theme import NAVIN_DARK

        items = [
            PickItem(f"cli:{i}", f"Session {i} with a longer title", f"cli:{i}", "2026-09-11")
            for i in range(24)
        ]

        class Host(App):
            async def on_mount(self) -> None:
                await self.push_screen(PickerScreen("Sessions", items, hint="Enter opens."))

        app = Host()
        app.register_theme(NAVIN_DARK)
        app.theme = "navin"
        async with app.run_test(size=(80, 20)) as _pilot:
            options = app.screen.query_one("#options", OptionList)
            self.assertGreaterEqual(len(options.options), 24)
            self.assertGreater(options.max_scroll_y, 0)
            before = options.scroll_y
            options.scroll_relative(y=4, animate=False)
            self.assertGreater(options.scroll_y, before)

    async def test_filter_keeps_matching_titles(self) -> None:
        from textual.app import App
        from textual.widgets import OptionList

        from navin.tui.screens import PickerScreen, PickItem
        from navin.tui.theme import NAVIN_DARK

        items = [
            PickItem("cli:1", "Migration v2", "This window", "9 Sep 15:00"),
            PickItem("sdk:e2e", "Untitled chat", "SDK", "4 Sep 17:11"),
        ]

        class Host(App):
            async def on_mount(self) -> None:
                await self.push_screen(PickerScreen("Sessions", items, hint="Type to search."))

        app = Host()
        app.register_theme(NAVIN_DARK)
        app.theme = "navin"
        async with app.run_test(size=(80, 20)) as _pilot:
            screen = app.screen
            assert isinstance(screen, PickerScreen)
            screen._apply_filter("migr")
            options = screen.query_one("#options", OptionList)
            self.assertEqual(len(options.options), 1)

    async def test_f2_asks_to_rename_the_highlighted_chat(self) -> None:
        from textual.app import App

        from navin.tui.screens import RENAME_PREFIX, PickerScreen, PickItem
        from navin.tui.theme import NAVIN_DARK

        items = [
            PickItem("__new__", "New session"),
            PickItem("cli:1", "Migration v2", "This window", "9 Sep"),
        ]
        chosen: list[str | None] = []

        class Host(App):
            async def on_mount(self) -> None:
                await self.push_screen(
                    PickerScreen("Sessions", items, current="cli:1", renamable=True),
                    chosen.append,
                )

        app = Host()
        app.register_theme(NAVIN_DARK)
        app.theme = "navin"
        async with app.run_test(size=(80, 20)) as pilot:
            await pilot.press("f2")
            await pilot.press("enter")
        self.assertEqual(chosen, [f"{RENAME_PREFIX}cli:1\nMigration v2"])

    async def test_typed_search_is_visible(self) -> None:
        from textual.app import App
        from textual.widgets import OptionList

        from navin.tui.screens import PickerScreen, PickItem
        from navin.tui.theme import NAVIN_DARK

        items = [
            PickItem("cli:1", "Migration v2"),
            PickItem("sdk:e2e", "Untitled chat"),
        ]

        class Host(App):
            async def on_mount(self) -> None:
                await self.push_screen(PickerScreen("Sessions", items))

        app = Host()
        app.register_theme(NAVIN_DARK)
        app.theme = "navin"
        async with app.run_test(size=(80, 20)) as _pilot:
            screen = app.screen
            assert isinstance(screen, PickerScreen)
            screen._apply_filter("migr")
            self.assertEqual(screen._query, "migr")
            options = screen.query_one("#options", OptionList)
            self.assertEqual(len(options.options), 1)


if __name__ == "__main__":
    unittest.main()
