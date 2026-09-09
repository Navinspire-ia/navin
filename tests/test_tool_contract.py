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


class HouseStyleTest(unittest.TestCase):
    def test_the_batching_section_carries_no_unicode_dash(self):
        """AGENTS.md bans em/en dashes; line 19 documents them, so scope this."""
        contract = render_template("agent/tool_contract.md")
        section = contract.split("## Token efficiency", 1)[1].split("\n## ", 1)[0]
        self.assertNotIn("\u2014", section)
        self.assertNotIn("\u2013", section)


if __name__ == "__main__":
    unittest.main()
