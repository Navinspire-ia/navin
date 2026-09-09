# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Chat titles must reflect the prompt, not the composer's routing slash."""

from __future__ import annotations

import unittest

from navin.session.webui_turns import (
    provisional_title_from_user_text,
    strip_leading_slash_command,
    title_source_from_user_text,
)


class StripLeadingSlashCommandTest(unittest.TestCase):
    def test_a_routing_prefix_is_dropped(self):
        self.assertEqual(
            strip_leading_slash_command("/forge build a landing page"),
            "build a landing page",
        )
        self.assertEqual(strip_leading_slash_command("/blueprint plan auth"), "plan auth")

    def test_a_bare_command_is_left_alone(self):
        self.assertEqual(strip_leading_slash_command("/board"), "/board")

    def test_free_text_is_untouched(self):
        self.assertEqual(strip_leading_slash_command("just chat"), "just chat")


class TitleSourceTest(unittest.TestCase):
    def test_workflow_brief_uses_focus_line(self):
        brief = (
            "[Build mode] (/forge)\n"
            "Skills for this mission (preloaded into Active Skills): forge.\n"
            "Focus / target given by the user: Build a landing page for Acme"
        )
        self.assertEqual(
            title_source_from_user_text(brief),
            "Build a landing page for Acme",
        )

    def test_workflow_brief_without_focus_is_empty(self):
        brief = (
            "[Build mode] (/forge)\n"
            "Skills for this mission (preloaded into Active Skills): forge.\n"
            "INTENT GATE: the user has not given a clear build target "
            "(empty message, greeting, or a short vague phrase)."
        )
        self.assertEqual(title_source_from_user_text(brief), "")


class ProvisionalTitleTest(unittest.TestCase):
    def test_agent_mode_free_text_still_gets_a_title(self):
        """Agent mode routes free text through /forge; the chat must still be
        titled immediately from the prompt, not stay 'New chat'."""
        self.assertEqual(
            provisional_title_from_user_text("/forge Build a landing page for Acme"),
            "Build a landing page for Acme",
        )

    def test_a_bare_command_gives_no_title(self):
        self.assertEqual(provisional_title_from_user_text("/board"), "")

    def test_greetings_are_usable_titles(self):
        # Better "Salut" than an LLM meta title like "The user said salut...".
        self.assertEqual(provisional_title_from_user_text("/forge salut"), "salut")
        self.assertEqual(provisional_title_from_user_text("bonjour"), "bonjour")

    def test_expanded_workflow_brief_is_not_used_as_title(self):
        brief = (
            "[Build mode] (/forge)\n"
            "Skills for this mission (preloaded into Active Skills): forge.\n"
            "INTENT GATE: the user has not given a clear build target "
            "(empty message, greeting, or a short vague phrase)."
        )
        self.assertEqual(provisional_title_from_user_text(brief), "")


class UsableGeneratedTitleTest(unittest.TestCase):
    def test_rejects_meta_descriptions(self):
        from navin.session.webui_turns import is_usable_generated_title

        self.assertFalse(
            is_usable_generated_title(
                'The user said "salut" (French for hello) and the assistant…'
            )
        )
        self.assertFalse(is_usable_generated_title("User said hello"))
        self.assertTrue(is_usable_generated_title("Salut"))
        self.assertTrue(is_usable_generated_title("Landing page Acme"))


if __name__ == "__main__":
    unittest.main()
