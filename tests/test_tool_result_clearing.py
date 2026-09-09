# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Which tool results may be blanked when a request overflows the window.

The mechanism is ``ContextGovernor.compact_inflight_overflow``: when the assembled
prompt no longer fits, it replaces the content of already-completed tool results
with a note saying the call ran, freeing room without dropping whole turns. What
it is allowed to touch, and how far past the target it goes, is the policy under
test here.
"""

from __future__ import annotations

import unittest

from navin.agent.context_governance import (
    INFLIGHT_COMPACT_TARGET_RATIO,
    ContextGovernanceConfig,
    ContextGovernor,
)
from navin.config.schema import (
    DEFAULT_CLEARING_EXCLUDE_TOOLS,
    AgentDefaults,
    ToolResultClearing,
)

# One token per 4 characters, counted only over message content. Real estimation
# goes through tiktoken or the provider; pinning it here keeps the arithmetic in
# these tests legible and independent of tokenizer revisions.
CHARS_PER_TOKEN = 4


class _CountingProvider:
    """Provider whose token count is a readable function of content length."""

    def estimate_prompt_tokens(self, messages, tools, model):  # noqa: ARG002
        chars = sum(len(str(m.get("content") or "")) for m in messages)
        return max(1, chars // CHARS_PER_TOKEN), "test_counter"


class _NoTools:
    def get_definitions(self):
        return []


def _tool_result(call_id: str, name: str, size: int) -> dict[str, object]:
    return {
        "role": "tool",
        "name": name,
        "tool_call_id": call_id,
        "content": "x" * size,
    }


def _config(
    *,
    window: int,
    clearing: ToolResultClearing | None = None,
) -> ContextGovernanceConfig:
    return ContextGovernanceConfig(
        provider=_CountingProvider(),
        model="test-model",
        tools=_NoTools(),
        workspace=None,
        session_key="test",
        max_tool_result_chars=16_000,
        context_window_tokens=window,
        # Pinning the budget directly keeps these cases independent of the output
        # reserve and safety buffer that input_budget would otherwise subtract.
        context_block_limit=window,
        clearing=clearing or ToolResultClearing(),
    )


def _blanked(messages: list[dict[str, object]]) -> list[str]:
    """Names of the tools whose result content was replaced."""
    return [
        str(m.get("name"))
        for m in messages
        if m.get("role") == "tool" and "compacted to fit context" in str(m.get("content"))
    ]


class EligibilityTest(unittest.TestCase):
    """A denylist, so an unrecognised tool is reclaimable rather than protected."""

    def _run(self, names: list[str], **policy) -> list[str]:
        messages = [
            _tool_result(f"call_{i}", name, 4_000) for i, name in enumerate(names)
        ]
        governor = ContextGovernor()
        config = _config(
            window=100,
            clearing=ToolResultClearing(keep_recent=0, **policy),
        )
        updated = governor.compact_inflight_overflow(config, messages, set())
        return _blanked(updated)

    def test_a_tool_nobody_listed_is_still_reclaimable(self) -> None:
        """The point of the change.

        An MCP server's tools are named at runtime and can return the largest
        results in the window. Under the previous roster of known-safe tools they
        were never eligible, so a conversation could sit far over budget with
        megabytes of MCP output that nothing was allowed to touch.
        """
        self.assertIn("mcp_datadog_query_logs", self._run(["mcp_datadog_query_logs"]))

    def test_the_default_policy_protects_irreversible_results(self) -> None:
        """Blanking these strands the model: it cannot re-run to recover them."""
        blanked = self._run(list(DEFAULT_CLEARING_EXCLUDE_TOOLS))
        self.assertEqual(blanked, [])

    def test_an_operator_can_protect_a_tool_of_their_own(self) -> None:
        blanked = self._run(
            ["read_file", "mcp_internal_ledger"],
            exclude_tools=["mcp_internal_ledger"],
        )
        self.assertEqual(blanked, ["read_file"])

    def test_an_operator_can_unprotect_a_default(self) -> None:
        """The default list is a starting point, not a floor."""
        blanked = self._run(["write_file"], exclude_tools=[])
        self.assertEqual(blanked, ["write_file"])

    def test_a_result_too_small_to_be_worth_it_is_left_alone(self) -> None:
        messages = [
            _tool_result("call_0", "read_file", 20),
            _tool_result("call_1", "grep", 8_000),
        ]
        governor = ContextGovernor()
        config = _config(
            window=100,
            clearing=ToolResultClearing(keep_recent=0, min_chars=500),
        )
        blanked = _blanked(governor.compact_inflight_overflow(config, messages, set()))
        self.assertEqual(blanked, ["grep"])


class OverflowGateTest(unittest.TestCase):
    """Hard overflow always clears; soft clear only blanks stale results."""

    def test_a_prompt_far_under_soft_limit_is_left_untouched(self) -> None:
        messages = [_tool_result("call_0", "grep", 4_000)]
        governor = ContextGovernor()
        updated = governor.compact_inflight_overflow(
            _config(window=100_000), messages, set()
        )
        self.assertEqual(_blanked(updated), [])
        self.assertEqual(updated[0]["content"], "x" * 4_000)

    def test_soft_clear_blanks_stale_results_before_hard_overflow(self) -> None:
        # 8 x 1000-token results = 8000 tokens; soft limit at 0.45 * 10000 = 4500.
        messages = [_tool_result(f"call_{i}", "grep", 4_000) for i in range(8)]
        governor = ContextGovernor()
        config = _config(
            window=10_000,
            clearing=ToolResultClearing(
                keep_recent=2, soft_clear_ratio=0.45, clear_at_least=0
            ),
        )
        updated = governor.compact_inflight_overflow(config, messages, set())
        blanked = _blanked(updated)
        self.assertGreaterEqual(len(blanked), 3)
        # Newest keep_recent results stay (soft path has no recent fallback).
        self.assertEqual(updated[-1]["content"], "x" * 4_000)
        self.assertEqual(updated[-2]["content"], "x" * 4_000)

    def test_the_history_is_never_mutated_in_place(self) -> None:
        """The caller's list is session history; only the model copy may change."""
        messages = [_tool_result(f"call_{i}", "grep", 4_000) for i in range(4)]
        original = [dict(m) for m in messages]
        governor = ContextGovernor()
        governor.compact_inflight_overflow(
            _config(window=100, clearing=ToolResultClearing(keep_recent=0)),
            messages,
            set(),
        )
        self.assertEqual(messages, original)

    def test_the_newest_result_survives_once_the_prompt_fits(self) -> None:
        """The result the model is about to reason over is the last to go.

        A window of 2000 tokens against four 1000-token results is over budget, so
        a pass runs; blanking the three older ones brings it back under, and the
        newest is spared.
        """
        messages = [_tool_result(f"call_{i}", "grep", 4_000) for i in range(4)]
        governor = ContextGovernor()
        config = _config(window=2_000, clearing=ToolResultClearing(keep_recent=0))
        updated = governor.compact_inflight_overflow(config, messages, set())
        self.assertEqual(updated[-1]["content"], "x" * 4_000)
        self.assertEqual(len(_blanked(updated)), 3)

    def test_a_hard_overflow_reaches_even_the_newest_result(self) -> None:
        """Protecting it is a preference; not fitting at all is a failed request."""
        messages = [_tool_result(f"call_{i}", "grep", 4_000) for i in range(4)]
        governor = ContextGovernor()
        config = _config(window=10, clearing=ToolResultClearing(keep_recent=0))
        updated = governor.compact_inflight_overflow(config, messages, set())
        self.assertEqual(len(_blanked(updated)), 4)


class ClearAtLeastTest(unittest.TestCase):
    """How far past the target a pass goes before it stops."""

    def _freed(self, clear_at_least: int) -> tuple[int, list[str]]:
        messages = [_tool_result(f"call_{i}", "grep", 4_000) for i in range(10)]
        governor = ContextGovernor()
        config = _config(
            window=9_000,
            clearing=ToolResultClearing(keep_recent=0, clear_at_least=clear_at_least),
        )
        updated = governor.compact_inflight_overflow(config, messages, set())
        return len(_blanked(updated)), _blanked(updated)

    def test_by_default_a_pass_stops_at_the_target(self) -> None:
        """Zero preserves the behaviour that shipped before the knob existed."""
        count, _ = self._freed(0)
        target = int(9_000 * INFLIGHT_COMPACT_TARGET_RATIO)
        # 10 results of 1000 tokens each; each blanking frees ~1000.
        self.assertEqual(count, 10 - target // 1_000)

    def test_a_floor_keeps_clearing_past_the_target(self) -> None:
        """Buys several turns of headroom for one prompt-cache break.

        Stopping at the target leaves the prompt just under it, so the next turn
        overflows again and rewrites the prefix again. The cache is then cold on
        every turn of a long run, which is the cost this knob exists to amortise.
        """
        at_target, _ = self._freed(0)
        with_floor, _ = self._freed(5_000)
        self.assertGreater(with_floor, at_target)

    def test_an_unsatisfiable_floor_still_spares_the_newest_result(self) -> None:
        """The floor is an optimisation; the newest-result guard outranks it.

        A floor nothing could satisfy must neither loop nor blank the result the
        model is about to reason over, so nine of ten go and the newest stays.
        """
        count, _ = self._freed(10_000_000)
        self.assertEqual(count, 9)


def _user(text: str) -> dict[str, object]:
    return {"role": "user", "content": text}


def _assistant_call(call_id: str, name: str) -> dict[str, object]:
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [{
            "id": call_id,
            "type": "function",
            "function": {"name": name, "arguments": "{}"},
        }],
    }


def _stale_blanked(messages: list[dict[str, object]]) -> list[str]:
    return [
        str(m.get("name"))
        for m in messages
        if m.get("role") == "tool"
        and "cleared from the replayed context" in str(m.get("content"))
    ]


class StaleTurnClearingTest(unittest.TestCase):
    """Old turns' tool dumps stop being replayed, regardless of pressure.

    This is the Cursor / Claude Code behaviour: replayed tool output from
    finished turns is the main source of dead input tokens. The rule is
    deterministic and monotone, so once a result ages past the boundary its
    stub never changes again and the prompt prefix stays cache-friendly.
    """

    @staticmethod
    def _three_turn_history(size: int = 4_000) -> list[dict[str, object]]:
        return [
            _user("turn one"),
            _assistant_call("call_1", "read_file"),
            _tool_result("call_1", "read_file", size),
            {"role": "assistant", "content": "done one"},
            _user("turn two"),
            _assistant_call("call_2", "grep"),
            _tool_result("call_2", "grep", size),
            {"role": "assistant", "content": "done two"},
            _user("turn three (current)"),
        ]

    def test_results_older_than_the_previous_turn_are_blanked(self) -> None:
        messages = self._three_turn_history()
        governor = ContextGovernor()
        # A huge window: nothing here is under context pressure.
        updated = governor.clear_stale_turn_tool_results(
            _config(window=1_000_000), messages
        )
        self.assertEqual(_stale_blanked(updated), ["read_file"])
        # The previous turn's result stays: the user is often replying to it.
        grep = next(m for m in updated if m.get("name") == "grep")
        self.assertEqual(grep["content"], "x" * 4_000)

    def test_small_results_are_not_worth_a_stub(self) -> None:
        messages = self._three_turn_history(size=100)
        governor = ContextGovernor()
        updated = governor.clear_stale_turn_tool_results(
            _config(window=1_000_000), messages
        )
        self.assertEqual(_stale_blanked(updated), [])

    def test_protected_tools_survive_aging_too(self) -> None:
        messages = self._three_turn_history()
        messages[1] = _assistant_call("call_1", "generate_image")
        messages[2] = _tool_result("call_1", "generate_image", 4_000)
        governor = ContextGovernor()
        updated = governor.clear_stale_turn_tool_results(
            _config(window=1_000_000), messages
        )
        self.assertEqual(_stale_blanked(updated), [])

    def test_zero_disables_the_rule(self) -> None:
        messages = self._three_turn_history()
        governor = ContextGovernor()
        updated = governor.clear_stale_turn_tool_results(
            _config(
                window=1_000_000,
                clearing=ToolResultClearing(stale_after_user_turns=0),
            ),
            messages,
        )
        self.assertEqual(_stale_blanked(updated), [])

    def test_reapplying_the_rule_is_a_fixed_point(self) -> None:
        """Cache stability: a stub replayed next turn is left exactly as is."""
        messages = self._three_turn_history()
        governor = ContextGovernor()
        config = _config(window=1_000_000)
        once = governor.clear_stale_turn_tool_results(config, messages)
        twice = governor.clear_stale_turn_tool_results(config, once)
        self.assertIs(twice, once)

    def test_the_session_history_is_never_mutated(self) -> None:
        messages = self._three_turn_history()
        original = [dict(m) for m in messages]
        governor = ContextGovernor()
        governor.clear_stale_turn_tool_results(_config(window=1_000_000), messages)
        self.assertEqual(messages, original)

    def test_prepare_for_model_applies_the_rule_end_to_end(self) -> None:
        messages = self._three_turn_history()
        governor = ContextGovernor()
        updated = governor.prepare_for_model(
            _config(window=1_000_000), messages, set()
        )
        self.assertEqual(_stale_blanked(updated), ["read_file"])


class ConfigSurfaceTest(unittest.TestCase):
    def test_the_shipped_default_is_the_policy_under_test(self) -> None:
        """Guards against the schema default drifting from these cases."""
        shipped = AgentDefaults().tool_result_clearing
        self.assertEqual(shipped.exclude_tools, list(DEFAULT_CLEARING_EXCLUDE_TOOLS))
        self.assertEqual(shipped.keep_recent, 12)
        self.assertEqual(shipped.clear_at_least, 2_000)
        self.assertAlmostEqual(shipped.soft_clear_ratio, 0.22)

    def test_the_knob_is_settable_in_camel_case(self) -> None:
        """config.json is camelCase; every other agent default accepts both."""
        parsed = ToolResultClearing.model_validate(
            {"excludeTools": ["spawn"], "clearAtLeast": 4_096, "keepRecent": 2}
        )
        self.assertEqual(parsed.exclude_tools, ["spawn"])
        self.assertEqual(parsed.clear_at_least, 4_096)
        self.assertEqual(parsed.keep_recent, 2)

    def test_an_untouched_policy_writes_nothing_to_config(self) -> None:
        """Defaults stay out of config.json so upgrades can move them."""
        dumped = AgentDefaults().model_dump(exclude_defaults=True, by_alias=True)
        self.assertNotIn("toolResultClearing", dumped)


if __name__ == "__main__":
    unittest.main()
