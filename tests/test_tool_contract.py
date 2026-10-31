# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""The tool contract must keep asking the model to group its calls.

Measured on 2026-08-15: 96 requests produced 12,247 output tokens, about 128
per request, which is one tool call each. The engine already runs independent
read-only calls concurrently (``concurrent_tools``); nothing ever asked the
model to emit them together. Losing this instruction silently restores that
one-call-per-round-trip behaviour, and the only symptom is a bill.
"""

import unittest

from navin.utils.prompt_templates import render_template


class BatchingGuidanceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.contract = render_template("agent/tool_contract.md")

    def test_the_contract_asks_for_calls_in_the_same_reply(self):
        self.assertIn("same reply", self.contract)

    def test_it_says_why_splitting_costs(self):
        """Models follow a rule they understand better than a bare order."""
        self.assertIn("re-sends the entire prompt", self.contract)

    def test_it_names_concrete_tools_to_group(self):
        section = self.contract.split("## Token efficiency", 1)[1].split("\n## ", 1)[0]
        for tool in ("read_file", "grep", "find_files", "code_index"):
            self.assertIn(tool, section)

    def test_it_still_allows_a_real_dependency(self):
        """Batching must not push the model into guessing its next call."""
        self.assertIn("genuinely decides the next call", self.contract)


class SlimContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.contract = render_template("agent/tool_contract_slim.md")

    def test_the_slim_contract_still_asks_for_calls_in_the_same_reply(self):
        self.assertIn("same reply", self.contract)
        self.assertIn("re-sends the entire prompt", self.contract)
        self.assertIn("genuinely decides the next call", self.contract)

    def test_the_slim_contract_drops_off_mission_essays(self):
        self.assertNotIn("## Scrape", self.contract)
        self.assertNotIn("## Browser and UI Verification", self.contract)
        self.assertNotIn("## Semantic Code Navigation", self.contract)


class BuildBeforeDeliveryTest(unittest.TestCase):
    """User rule: always build before delivering, small changes included."""

    def test_both_contracts_require_a_build_before_delivery(self):
        for name in ("agent/tool_contract.md", "agent/tool_contract_slim.md"):
            contract = render_template(name)
            self.assertIn("Always build before delivering", contract, name)
            self.assertNotIn("No production build", contract, name)
            self.assertNotIn("Do not run a production build", contract, name)

    def test_fullstack_skill_gates_delivery_on_a_green_build(self):
        from pathlib import Path

        import navin

        skill = (Path(navin.__file__).parent / "skills/fullstack-dev/SKILL.md").read_text(encoding="utf-8")
        gate = skill.split("## Delivery gate", 1)[1].split("\n## ", 1)[0]
        self.assertIn("Always build before delivering", gate)


class HouseStyleTest(unittest.TestCase):
    def test_delivery_summary_is_shared_by_cli_and_desktop(self):
        for channel in ("cli", "websocket"):
            identity = render_template("agent/identity.md", channel=channel)
            self.assertIn("Files to execute", identity)
            self.assertIn("prepared but not executed", identity)
            self.assertIn("execution order, destination environment", identity)

    def test_the_batching_section_carries_no_unicode_dash(self):
        """AGENTS.md bans em/en dashes; line 19 documents them, so scope this."""
        contract = render_template("agent/tool_contract.md")
        section = contract.split("## Token efficiency", 1)[1].split("\n## ", 1)[0]
        self.assertNotIn("\u2014", section)
        self.assertNotIn("\u2013", section)


class CorePromptStaysLeanTest(unittest.TestCase):
    """Strong models lose quality under a pile of overlapping orders.

    Measured on 2026-10-23 before the trim: identity 11.5k chars, full
    contract 37.6k, slim contract 10.1k, /forge brief 10.9k. The caps leave
    room for a few lines, not for the essays to grow back.
    """

    def test_core_templates_stay_under_their_budget(self):
        budgets = {
            "agent/identity.md": 7_000,
            "agent/tool_contract.md": 13_000,
            "agent/tool_contract_slim.md": 5_000,
        }
        for name, cap in budgets.items():
            size = len(render_template(name, channel="cli", workspace_path="/w", runtime="x", platform_policy=""))
            self.assertLess(size, cap, name)

    def test_forge_brief_is_lean_and_does_not_contradict_auto_branch(self):
        from navin.command.builtin import _WORKFLOW_BRIEFS

        brief = _WORKFLOW_BRIEFS["/forge"][2]
        self.assertLess(len(brief), 4_000)
        # Runtime Context owns the branch policy; the briefs used to promise
        # a navin/task-<id> branch even when auto-branch is off.
        for command in ("/forge", "/cruise", "/mission"):
            text = _WORKFLOW_BRIEFS[command][2]
            self.assertNotIn("navin/task-<id>", text, command)
            self.assertIn("auto-branch", text, command)
            # A close-out lists remarks, not work left open.
            self.assertNotIn("open items", text, command)
            self.assertNotIn("remaining risks", text, command)
        # The plan must name every layer, the "front never wired" complaint.
        self.assertIn("frontend screens, navigation, wiring", brief)


if __name__ == "__main__":
    unittest.main()


class EssentialTestingGuidanceTest(unittest.TestCase):
    """Default guidance: one or two tests per change; thorough keeps the old breadth.

    Reported: tasks took forever because every edit asked for happy path,
    failure cases and every related test file.
    """

    def test_both_contracts_ask_for_one_or_two_tests_by_default(self):
        for name in ("agent/tool_contract.md", "agent/tool_contract_slim.md"):
            essential = render_template(name).lower()
            self.assertIn("one or two test", essential, name)
            self.assertIn("never the whole suite", essential, name)
            self.assertNotIn("get real tests", essential, name)
            thorough = render_template(name, testing_depth="thorough").lower()
            self.assertIn("get real tests", thorough, name)
            self.assertNotIn("one or two test", thorough, name)

    def test_the_validation_reminder_follows_the_setting(self):
        from unittest import mock

        from navin.utils.runtime import (
            VERIFY_BEFORE_DONE_THOROUGH_PROMPT,
            build_verify_before_done_message,
            is_harness_nudge,
        )

        essential = build_verify_before_done_message(depth="essential")["content"]
        self.assertIn("one or two tests, no more", essential)
        self.assertIn("Never the whole suite", essential)
        self.assertNotIn("relevant failure cases", essential)
        self.assertEqual(
            build_verify_before_done_message(depth="thorough")["content"], VERIFY_BEFORE_DONE_THOROUGH_PROMPT,
        )
        # Both stay recognised as Navin's own reminders, never as the user's words.
        self.assertTrue(is_harness_nudge(essential))
        self.assertTrue(is_harness_nudge(VERIFY_BEFORE_DONE_THOROUGH_PROMPT))
        with mock.patch("navin.quality.check_budget.testing_depth", return_value="thorough"):
            self.assertIn("relevant failure cases", build_verify_before_done_message()["content"])
        self.assertEqual(build_verify_before_done_message("custom")["content"], "custom")
