"""The live agent eval executes the real loop and really discriminates.

The keyword eval scored canned strings, so a broken runner could still ship.
These tests pin the new backend on both sides:

* the shipped corpus passes through the real AgentRunner with real tools,
  including the Plan and Ask guards observed as runtime refusals;
* the checker fails loudly when the loop does NOT do what a case expects
  (wrong file content, missing tool run, mutation in a read-only mode), so
  a green gate means the product behaved, not that the harness is lenient.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from navin.evals.agent_loop import (
    AgentLoopCase,
    load_agent_dataset,
    run_agent_case,
    run_agent_dataset,
)
from navin.evals.runner import release_gate, scoreboard

_DATASET = Path("navin/evals/datasets/code_agent_live_v1.jsonl")


class ShippedCorpusTest(unittest.TestCase):
    def test_the_live_corpus_clears_a_full_bar(self) -> None:
        results = run_agent_dataset(_DATASET)
        board = scoreboard(results)
        gate = release_gate(board, min_overall=1.0, min_per_category=1.0)
        failures = [(r.case_id, r.missing) for r in results if not r.passed]
        self.assertTrue(gate.ok, f"gate={gate.reasons} failures={failures}")

    def test_the_corpus_exercises_the_mode_guards(self) -> None:
        cases = load_agent_dataset(_DATASET)
        modes = {case.composer_mode for case in cases}
        self.assertIn("plan", modes)
        self.assertIn("ask", modes)
        guarded = [c for c in cases if c.expect.get("tools_blocked")]
        self.assertTrue(guarded, "the corpus must assert real policy refusals")


class HarnessDiscriminatesTest(unittest.TestCase):
    """A green gate must be impossible when the loop misbehaves."""

    def test_wrong_file_content_fails_the_case(self) -> None:
        case = AgentLoopCase(
            id="bad-edit",
            prompt="fix it",
            workspace_files={"a.py": "x = 1\n"},
            script=(
                {"tool": "edit_file", "args": {"path": "a.py", "old_text": "x = 1", "new_text": "x = 2"}},
                {"final": "done"},
            ),
            expect={"files_contain": {"a.py": ["x = 3"]}},
        )
        result = run_agent_case(case)
        self.assertFalse(result.passed)
        self.assertTrue(any("x = 3" in item for item in result.missing))

    def test_a_tool_that_never_ran_fails_the_case(self) -> None:
        case = AgentLoopCase(
            id="no-grep",
            prompt="look around",
            workspace_files={"a.py": "pass\n"},
            script=({"final": "done without looking"},),
            expect={"tools_ok": ["grep"]},
        )
        result = run_agent_case(case)
        self.assertFalse(result.passed)
        self.assertIn("tool_ok:grep", result.missing)

    def test_a_mutation_in_ask_mode_is_refused_and_visible(self) -> None:
        case = AgentLoopCase(
            id="ask-mutation",
            prompt="just read",
            composer_mode="ask",
            workspace_files={"a.py": "x = 1\n"},
            script=(
                {"tool": "write_file", "args": {"path": "b.py", "content": "y = 2\n"}},
                {"final": "done"},
            ),
            expect={"tools_blocked": ["write_file"], "no_writes": True, "files_absent": ["b.py"]},
        )
        result = run_agent_case(case)
        self.assertTrue(result.passed, result.missing)

    def test_an_unexpected_mutation_breaks_no_writes(self) -> None:
        case = AgentLoopCase(
            id="agent-mutation",
            prompt="do work",
            workspace_files={},
            script=(
                {"tool": "write_file", "args": {"path": "b.py", "content": "y = 2\n"}},
                {"final": "done"},
            ),
            expect={"no_writes": True},
        )
        result = run_agent_case(case)
        self.assertFalse(result.passed)
        self.assertTrue(any("no_writes" in item for item in result.missing))

    def test_stop_reason_mismatch_fails_the_case(self) -> None:
        case = AgentLoopCase(
            id="stop-reason",
            prompt="finish",
            workspace_files={},
            script=({"final": "done"},),
            expect={"stop_reason": "max_iterations"},
        )
        result = run_agent_case(case)
        self.assertFalse(result.passed)
        self.assertTrue(any("stop_reason" in item for item in result.missing))


class DatasetShapeTest(unittest.TestCase):
    def test_every_case_has_an_expectation_and_a_script(self) -> None:
        for case in load_agent_dataset(_DATASET):
            self.assertTrue(case.script, case.id)
            self.assertTrue(case.expect, case.id)
            self.assertTrue(
                any("final" in step for step in case.script),
                f"{case.id}: script must end with a final answer",
            )


if __name__ == "__main__":
    unittest.main()
