"""Best-of-N pipeline: candidate fan-out, judge independence, honest failure.

The promise under test: N candidates are generated with varied
temperatures, the judge sees them shuffled and anonymized, the verdict is
mapped back to original order, and every degraded path (judge garbage,
partial generation failure, total failure) is reported rather than papered
over.
"""

from __future__ import annotations

import unittest

from navin.agent.best_of import (
    BestOfError,
    _candidate_temperatures,
    _parse_judge_json,
    best_of_n,
)
from navin.providers.base import GenerationSettings, LLMResponse
from navin.utils.llm_runtime import LLMRuntime


class _ScriptedProvider:
    """Answers candidate calls from a queue, then the judge call last.

    Judge calls are recognized by temperature 0.0, which is part of the
    contract this suite pins (a reproducible verdict).
    """

    def __init__(self, candidates: list[str | None], judge: str | None) -> None:
        self._candidates = list(candidates)
        self._judge = judge
        self.judge_prompt: str | None = None
        self.candidate_temperatures: list[float] = []

    async def chat(self, messages, tools=None, model=None, max_tokens=4096,
                   temperature=0.7, reasoning_effort=None, tool_choice=None):
        if temperature == 0.0:
            self.judge_prompt = messages[-1]["content"]
            if self._judge is None:
                raise RuntimeError("judge unavailable")
            return LLMResponse(content=self._judge)
        self.candidate_temperatures.append(temperature)
        answer = self._candidates.pop(0)
        if answer is None:
            raise RuntimeError("candidate call failed")
        return LLMResponse(content=answer)


def _runtime(provider) -> LLMRuntime:
    return LLMRuntime(
        provider=provider,  # type: ignore[arg-type]
        model="test-model",
        generation=GenerationSettings(temperature=0.5, max_tokens=1024),
        context_window_tokens=100_000,
    )


def _judge_json(winner: str, scores: dict[str, float]) -> str:
    rows = ", ".join(
        f'{{"candidate": "{label}", "score": {score}, "reason": "r{label}"}}'
        for label, score in scores.items()
    )
    return f'{{"scores": [{rows}], "winner": "{winner}", "rationale": "because"}}'


class BestOfNTest(unittest.IsolatedAsyncioTestCase):
    async def test_winner_is_mapped_back_to_original_order(self) -> None:
        # With seed 1 and n=3 the shuffle is deterministic; whatever the
        # anonymized label of "plan-two" is, picking it must answer index 1.
        provider = _ScriptedProvider(
            ["plan-one", "plan-two", "plan-three"], judge=None
        )
        # First, discover the label mapping by running with a judge that
        # scores everything, then assert the mapping is consistent.
        provider._judge = _judge_json("A", {"A": 9, "B": 5, "C": 4})
        result = await best_of_n(
            _runtime(provider), "pick a plan", n=3, judge_seed=1
        )
        self.assertTrue(result.judge_ok)
        # Label A points at some original candidate; the winning text must
        # be exactly the candidate the judge saw as A.
        assert provider.judge_prompt is not None
        candidate_a = provider.judge_prompt.split("Candidate A:\n")[1].split("\n\n---")[0]
        self.assertEqual(candidate_a, result.candidates[result.winner])

    async def test_candidates_are_shuffled_for_the_judge(self) -> None:
        provider = _ScriptedProvider(
            ["plan-one", "plan-two", "plan-three"],
            judge=_judge_json("A", {"A": 8, "B": 7, "C": 3}),
        )
        await best_of_n(_runtime(provider), "task", n=3, judge_seed=7)
        assert provider.judge_prompt is not None
        # Seed 7 shuffles [0,1,2] away from identity for 3 items.
        first_shown = provider.judge_prompt.split("Candidate A:\n")[1].split("\n")[0]
        self.assertNotEqual("plan-one", first_shown)

    async def test_temperatures_vary_between_candidates(self) -> None:
        provider = _ScriptedProvider(
            ["a", "b", "c"], judge=_judge_json("A", {"A": 5, "B": 5, "C": 5})
        )
        await best_of_n(_runtime(provider), "task", n=3, judge_seed=0)
        self.assertEqual(3, len(set(provider.candidate_temperatures)))

    async def test_unparseable_judge_is_reported_not_hidden(self) -> None:
        provider = _ScriptedProvider(
            ["plan-one", "plan-two"], judge="I like the second one best."
        )
        result = await best_of_n(_runtime(provider), "task", n=2, judge_seed=0)
        self.assertFalse(result.judge_ok)
        self.assertEqual(0, result.winner)
        self.assertIn("could not be parsed", result.render())

    async def test_single_surviving_candidate_skips_the_contest(self) -> None:
        provider = _ScriptedProvider(["only-plan", None, None], judge=None)
        result = await best_of_n(_runtime(provider), "task", n=3, judge_seed=0)
        self.assertEqual(0, result.winner)
        self.assertFalse(result.judge_ok)
        self.assertIn("only one candidate", result.rationale)
        # The judge was never consulted.
        self.assertIsNone(provider.judge_prompt)

    async def test_total_generation_failure_raises(self) -> None:
        provider = _ScriptedProvider([None, None], judge=None)
        with self.assertRaises(BestOfError):
            await best_of_n(_runtime(provider), "task", n=2, judge_seed=0)

    async def test_judge_crash_falls_back_honestly(self) -> None:
        provider = _ScriptedProvider(["one", "two"], judge=None)
        result = await best_of_n(_runtime(provider), "task", n=2, judge_seed=0)
        self.assertFalse(result.judge_ok)
        self.assertEqual(0, result.winner)

    async def test_missing_winner_field_falls_back_to_best_score(self) -> None:
        judge = (
            '{"scores": [{"candidate": "A", "score": 3, "reason": "ra"}, '
            '{"candidate": "B", "score": 9, "reason": "rb"}], "rationale": "x"}'
        )
        provider = _ScriptedProvider(["one", "two"], judge=judge)
        result = await best_of_n(_runtime(provider), "task", n=2, judge_seed=0)
        self.assertTrue(result.judge_ok)
        best = max(result.verdicts, key=lambda v: v.score)
        self.assertEqual(best.index, result.winner)


class HelperTest(unittest.TestCase):
    def test_temperature_spread_is_capped(self) -> None:
        temps = _candidate_temperatures(0.9, 4)
        self.assertEqual(0.9, temps[0])
        self.assertTrue(all(t <= 1.0 for t in temps))

    def test_judge_json_survives_code_fences_and_prose(self) -> None:
        raw = 'Sure!\n```json\n{"winner": "B", "scores": [], "rationale": "r"}\n```'
        parsed = _parse_judge_json(raw)
        assert parsed is not None
        self.assertEqual("B", parsed["winner"])

    def test_garbage_is_none(self) -> None:
        self.assertIsNone(_parse_judge_json("no json here"))
        self.assertIsNone(_parse_judge_json('["array", "not", "object"]'))


class ToolSurfaceTest(unittest.TestCase):
    def test_best_of_is_available_in_plan_mode(self) -> None:
        """The whole point is advising risky *plans*; Plan mode must see it."""
        from navin.agent.tool_surface import denied_tools_for_composer_mode

        self.assertNotIn("best_of", denied_tools_for_composer_mode("plan"))

    def test_tool_is_read_only(self) -> None:
        from navin.agent.tools.best_of import BestOfTool

        self.assertTrue(BestOfTool().read_only)


if __name__ == "__main__":
    unittest.main()
