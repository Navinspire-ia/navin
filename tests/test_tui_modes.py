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
            self.assertIn("list_dir", tool._head_text())


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

        tool = ToolCall(
            "c1",
            "apply_patch",
            {"edits": [{"action": "add", "new_text": "hello"}]},
        )
        tool.phase = "end"
        head = tool._head_text()
        self.assertNotIn("[/dim]", head)
        self.assertIn("apply_patch", head)


if __name__ == "__main__":
    unittest.main()
