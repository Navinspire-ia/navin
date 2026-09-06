"""The mid-turn snip must leave a usable brief, not a vague notice.

When the context window fills mid-Build the runner cannot call the LLM
handoff (that would stall the request). The dropped turns still named
files, a user ask and tool failures: those facts have to travel with the
snip so the next model call does not invent them.
"""

from __future__ import annotations

import unittest

from navin.agent.context_governance import deterministic_snip_brief


class DeterministicSnipBriefTest(unittest.TestCase):
    def test_names_the_user_ask_files_and_failures(self) -> None:
        brief = deterministic_snip_brief([
            {"role": "user", "content": "Fix the login crash in webui/src/App.tsx"},
            {
                "role": "tool",
                "name": "read_file",
                "content": "Opened webui/src/App.tsx\nOpened navin/agent/loop.py",
            },
            {
                "role": "tool",
                "name": "verify",
                "status": "error",
                "content": "Error: VERDICT_TEST_FAILURES: 2 red in tests/test_login.py",
            },
        ])
        self.assertIn("snipped mid-run", brief)
        self.assertIn("Fix the login crash", brief)
        self.assertIn("webui/src/App.tsx", brief)
        self.assertIn("navin/agent/loop.py", brief)
        self.assertIn("VERDICT_TEST_FAILURES", brief)

    def test_an_empty_drop_still_tells_the_model_to_re_read(self) -> None:
        brief = deterministic_snip_brief([])
        self.assertIn("Re-read the files", brief)
        self.assertNotIn("Last user ask", brief)

    def test_the_brief_stays_bounded(self) -> None:
        dropped = [
            {
                "role": "tool",
                "name": "read_file",
                "content": f"pkg/mod{i}/file{i}.py " * 40,
            }
            for i in range(20)
        ]
        brief = deterministic_snip_brief(dropped)
        self.assertLessEqual(len(brief), 920)


if __name__ == "__main__":
    unittest.main()
