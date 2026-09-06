"""Plan limits from license validate must clamp agent runtime settings."""

from __future__ import annotations

import unittest
from unittest import mock

from navin.config.schema import Config
from navin.plan_limits import (
    DEFAULT_TURN_ITERATION_CAP,
    FREE_CONCURRENT_AGENTS,
    FREE_STEPS_PER_TASK,
    apply_limits_from_response,
    apply_to_agent_loop,
    clamp_concurrent_agents,
    clamp_steps_per_task,
    effective_concurrent_agents,
    effective_steps_per_task,
    effective_turn_iterations,
    parse_limits,
)


class ParseLimitsTest(unittest.TestCase):
    def test_camel_and_snake_case(self):
        self.assertEqual(
            parse_limits(
                {
                    "limits": {
                        "stepsPerTask": 30,
                        "concurrentAgents": 1,
                        "outputTokensPerCall": 8000,
                    }
                }
            ),
            {
                "steps_per_task": 30,
                "concurrent_agents": 1,
                "output_tokens_per_call": 8000,
            },
        )
        self.assertEqual(
            parse_limits({"limits": {"steps_per_task": 120, "concurrent_agents": 5}}),
            {"steps_per_task": 120, "concurrent_agents": 5},
        )

    def test_ignores_invalid(self):
        self.assertEqual(parse_limits(None), {})
        self.assertEqual(parse_limits({"limits": {"stepsPerTask": 0}}), {})
        self.assertEqual(parse_limits({"limits": {"stepsPerTask": "x"}}), {})


class ClampTest(unittest.TestCase):
    def test_steps_never_exceed_plan(self):
        self.assertEqual(clamp_steps_per_task(200, 30), 30)
        self.assertEqual(clamp_steps_per_task(20, 30), 20)
        self.assertEqual(clamp_steps_per_task(200, None), 200)
        self.assertEqual(clamp_steps_per_task(200, 0), 200)

    def test_concurrent_agents_never_exceed_plan(self):
        self.assertEqual(clamp_concurrent_agents(8, 1), 1)
        self.assertEqual(clamp_concurrent_agents(3, 8), 3)
        self.assertEqual(clamp_concurrent_agents(8, None), 8)


class ConfigStoreTest(unittest.TestCase):
    def test_validate_body_stores_limits(self):
        config = Config()
        changed = apply_limits_from_response(
            config,
            {"limits": {"stepsPerTask": 80, "concurrentAgents": 3}},
        )
        self.assertTrue(changed)
        self.assertEqual(config.license.steps_per_task, 80)
        self.assertEqual(config.license.concurrent_agents, 3)

    def test_effective_helpers_respect_plan(self):
        config = Config()
        config.agents.defaults.max_tool_iterations = 200
        config.agents.defaults.max_concurrent_subagents = 8
        config.license.steps_per_task = 30
        config.license.concurrent_agents = 1
        self.assertEqual(effective_steps_per_task(config), 30)
        self.assertEqual(effective_concurrent_agents(config), 1)

    def test_team_limits(self):
        config = Config()
        config.agents.defaults.max_tool_iterations = 200
        config.agents.defaults.max_concurrent_subagents = 8
        apply_limits_from_response(
            config,
            {"limits": {"stepsPerTask": 120, "concurrentAgents": 5}},
        )
        self.assertEqual(effective_steps_per_task(config), 120)
        self.assertEqual(effective_concurrent_agents(config), 5)


class ApplyToLoopTest(unittest.TestCase):
    def test_apply_to_agent_loop_clamps(self):
        config = Config()
        config.license.steps_per_task = 30
        config.license.concurrent_agents = 1

        loop = mock.Mock()
        loop.max_iterations = 200
        loop.subagents = mock.Mock()
        loop.subagents.max_iterations = 200
        loop.subagents.max_concurrent_subagents = 8
        loop._concurrency_gate = None

        with mock.patch.dict("os.environ", {}, clear=False):
            # Ensure env override is absent for this assertion.
            import os

            os.environ.pop("NAVIN_MAX_CONCURRENT_REQUESTS", None)
            apply_to_agent_loop(loop, config)

        self.assertEqual(loop.max_iterations, 30)
        self.assertEqual(loop.subagents.max_iterations, 30)
        self.assertEqual(loop.subagents.max_concurrent_subagents, 1)
        self.assertIsNotNone(loop._concurrency_gate)


class TeamBudgetMathTest(unittest.TestCase):
    """Mirror site/src/lib/plans.ts Team seat math (source of truth on site)."""

    TEAM_SEAT_PRICE_USD = 40
    TEAM_SEAT_AI_BUDGET_USD = 38
    TEAM_SEAT_MIN = 2
    TEAM_SEAT_MAX = 50
    MICRO = 1_000_000

    def _clamp(self, seats: int) -> int:
        return min(self.TEAM_SEAT_MAX, max(self.TEAM_SEAT_MIN, int(seats)))

    def _budget_micro(self, seats: int) -> int:
        return self._clamp(seats) * self.TEAM_SEAT_AI_BUDGET_USD * self.MICRO

    def _cap_usd(self, seats: int) -> int:
        # Same single OpenRouter limit as site openRouterCap / teamManagedKeyCapUsd.
        return self._clamp(seats) * self.TEAM_SEAT_AI_BUDGET_USD

    def test_team_5_and_10(self):
        self.assertEqual(5 * self.TEAM_SEAT_PRICE_USD, 200)
        self.assertEqual(10 * self.TEAM_SEAT_PRICE_USD, 400)
        self.assertEqual(self._budget_micro(5), 190 * self.MICRO)
        self.assertEqual(self._budget_micro(10), 380 * self.MICRO)

    def test_limits_scale_with_seats(self):
        # Agents never drop below the Pro entitlement: a pooled Team seat must
        # not work less in parallel than a single Pro desk.
        for seats, agents, devices in ((2, 15, 4), (5, 15, 10), (10, 15, 20), (50, 20, 100)):
            with self.subTest(seats=seats):
                self.assertEqual(min(20, max(15, seats)), agents)
                self.assertEqual(seats * 2, devices)

    def test_clamp_bounds(self):
        self.assertEqual(self._clamp(1), 2)
        self.assertEqual(self._clamp(100), 50)

    def test_managed_key_cap_matches_budget(self):
        self.assertEqual(self._cap_usd(5), 5 * self.TEAM_SEAT_AI_BUDGET_USD)


class PaidLadderTest(unittest.TestCase):
    """Every execution limit must grow with the price, read from plans.ts.

    Plus shipped with the Free limits, which made a 20 $ seat strictly less
    capable than the 5 $ one (30 steps against 80). Nothing on the site renders
    these numbers, so the regression was invisible until an agent hit the cap.
    Team is excluded: its limits are derived from the seat count, and
    Enterprise is a "contact sales" card that deliberately behaves like Free.
    """

    LADDER = ("free", "flash", "plus", "pro", "ultra")
    FIELDS = (
        "concurrentAgents",
        "stepsPerTask",
        "outputTokensPerCall",
        "outputTokensPerCallBurst",
        "devices",
    )

    @classmethod
    def setUpClass(cls):
        cls.limits = _parse_plan_limits()

    def test_every_ladder_plan_is_present(self):
        for plan in self.LADDER:
            self.assertIn(plan, self.limits, f"{plan} is missing from plans.ts")

    def test_limits_never_decrease_as_price_grows(self):
        for lower, higher in zip(self.LADDER, self.LADDER[1:]):
            for field in self.FIELDS:
                with self.subTest(field=field, lower=lower, higher=higher):
                    self.assertLessEqual(
                        self.limits[lower][field],
                        self.limits[higher][field],
                        f"{higher} gives less {field} than {lower}",
                    )

    def test_free_safe_fallback_matches_the_site(self):
        """The clamp used when a subscription is refused is the Free plan.

        It lives in Python because it has to work with no answer from the site,
        so nothing but this test keeps the two copies equal.
        """
        self.assertEqual(self.limits["free"]["concurrentAgents"], FREE_CONCURRENT_AGENTS)
        self.assertEqual(self.limits["free"]["stepsPerTask"], FREE_STEPS_PER_TASK)

    def test_the_config_ceiling_can_serve_the_best_plan(self):
        """Plan limits are a cap on the configured value, never a grant.

        A default below the top plan would silently clamp Ultra down to it,
        which is how Plus at 12 would have kept behaving like 8.
        """
        top = max(self.limits[plan]["concurrentAgents"] for plan in self.LADDER)
        self.assertGreaterEqual(Config().agents.defaults.max_concurrent_subagents, top)

    def test_paid_plans_beat_the_free_one(self):
        free = self.limits["free"]
        for plan in ("flash", "plus", "pro", "ultra"):
            with self.subTest(plan=plan):
                self.assertGreater(
                    sum(self.limits[plan][field] for field in self.FIELDS),
                    sum(free[field] for field in self.FIELDS),
                    f"{plan} is not worth paying for over free",
                )


def _parse_plan_limits() -> dict[str, dict[str, int]]:
    """The ``limits`` block of each statically declared plan in plans.ts."""
    import re
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[1] / "site" / "front" / "src" / "lib" / "plans.ts"
    ).read_text(encoding="utf-8")
    out: dict[str, dict[str, int]] = {}
    for plan in PaidLadderTest.LADDER:
        block = re.search(
            rf"\n  {plan}: \{{.*?limits: \{{(.*?)\}},", source, re.DOTALL
        )
        if block is None:
            continue
        out[plan] = {
            key: int(value.replace("_", ""))
            for key, value in re.findall(r"(\w+): ([\d_]+)", block.group(1))
        }
    return out


class TurnIterationCapTest(unittest.TestCase):
    def test_ordinary_turns_are_capped_below_the_license(self) -> None:
        self.assertEqual(effective_turn_iterations(80, goal_active=False), DEFAULT_TURN_ITERATION_CAP)
        self.assertEqual(effective_turn_iterations(8, goal_active=False), 8)

    def test_sustained_goals_keep_the_full_entitlement(self) -> None:
        self.assertEqual(effective_turn_iterations(80, goal_active=True), 80)


if __name__ == "__main__":
    unittest.main()
