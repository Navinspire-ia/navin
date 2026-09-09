# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""The token profiler must attribute cost without ever losing tokens."""

import os
import unittest
from unittest.mock import patch

from loguru import logger

from navin.agent.prompt_profile import (
    BUDGET_GROUPS,
    DEFAULT_BUDGET,
    SECTION_SEPARATOR,
    PromptBudget,
    PromptProfile,
    display_buckets,
    format_profile,
    log_request_profile,
    profile_request,
    profiling_enabled,
    reset_budget_warnings,
)


def _system(*blocks: str) -> dict[str, str]:
    return {"role": "system", "content": SECTION_SEPARATOR.join(blocks)}


IDENTITY = "## Who You Are\n\nYou are Navin, a general-purpose agent."
CONTRACT = "# Tool Usage Notes\n\n" + ("Contract prose. " * 200)
HISTORY = "# Recent History\n\n- [t] the user asked for a deck"
BOOTSTRAP = "## AGENTS.md\n\nProject conventions live here."

TOOL = {
    "type": "function",
    "function": {
        "name": "read_file",
        "description": "Read a file from the workspace.",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
    },
}

#: What an average request looked like on 2026-08-15, from the real profile
#: of this workspace plus the day's recorded 62,126-token average.
_TODAYS_REQUEST = PromptProfile(
    sections={
        "tools": 19_193,
        "system.tool_contract": 6_400,
        "system.bootstrap": 1_734,
        "system.identity": 1_414,
        "system.rules": 164,
        "system.memory": 149,
        "system.skills": 479,
        "system.skills_index": 776,
        "conversation": 16_800,
        "tool_results": 15_000,
    },
    total=62_126,
)


class SystemSectionsTest(unittest.TestCase):
    def test_each_block_lands_in_its_own_bucket(self):
        profile = profile_request([_system(IDENTITY, CONTRACT, HISTORY)])
        self.assertIn("system.identity", profile.sections)
        self.assertIn("system.tool_contract", profile.sections)
        self.assertIn("system.history", profile.sections)
        # The contract is the fat one; that ordering is the whole point.
        self.assertGreater(
            profile.sections["system.tool_contract"],
            profile.sections["system.identity"],
        )

    def test_bootstrap_files_share_one_bucket(self):
        profile = profile_request(
            [_system(BOOTSTRAP, "## USER.md\n\nDurable facts about the user.")]
        )
        self.assertIn("system.bootstrap", profile.sections)
        self.assertNotIn("system.other", profile.sections)

    def test_an_unknown_section_is_still_counted(self):
        """A new section must show up as unattributed, never vanish."""
        known = profile_request([_system(IDENTITY)])
        with_new = profile_request([_system(IDENTITY, "# Brand New Thing\n\n" + "x " * 100)])
        self.assertIn("system.other", with_new.sections)
        self.assertGreater(with_new.total, known.total)

    def test_a_block_without_a_heading_is_not_dropped(self):
        profile = profile_request([_system("no heading at all, just prose " * 50)])
        self.assertIn("system.other", profile.sections)
        self.assertGreater(profile.sections["system.other"], 0)


class RequestBucketsTest(unittest.TestCase):
    def test_tool_results_are_separated_from_the_conversation(self):
        profile = profile_request(
            [
                _system(IDENTITY),
                {"role": "user", "content": "generate a deck"},
                {"role": "tool", "tool_call_id": "1", "content": "file contents " * 200},
            ]
        )
        self.assertGreater(profile.sections["tool_results"], profile.sections["conversation"])

    def test_tool_schemas_are_counted_and_flagged_fixed(self):
        without = profile_request([_system(IDENTITY)])
        with_tools = profile_request([_system(IDENTITY)], [TOOL] * 40)
        self.assertEqual(with_tools.tool_count, 40)
        self.assertGreater(with_tools.sections["tools"], 0)
        self.assertGreater(with_tools.fixed, without.fixed)

    def test_mcp_schemas_are_split_from_builtin_tools(self):
        mcp = {
            "type": "function",
            "function": {
                "name": "mcp_github_list_issues",
                "description": "List issues",
                "parameters": {"type": "object", "properties": {}},
            },
        }
        profile = profile_request([_system(IDENTITY)], [TOOL, mcp, mcp])
        self.assertGreater(profile.sections["tools"], 0)
        self.assertGreater(profile.sections["tools.mcp"], 0)
        self.assertGreater(profile.sections["tools.mcp"], profile.sections["tools"])

    def test_the_buckets_sum_to_the_total(self):
        profile = profile_request(
            [
                _system(IDENTITY, CONTRACT),
                {"role": "user", "content": "hello"},
                {"role": "tool", "tool_call_id": "1", "content": "result"},
            ],
            [TOOL],
        )
        self.assertEqual(sum(profile.sections.values()), profile.total)

    def test_the_fixed_ratio_reports_what_repeats_every_turn(self):
        profile = profile_request([_system(IDENTITY, CONTRACT, HISTORY)], [TOOL] * 40)
        # History changes every turn, so it must stay out of the fixed floor.
        self.assertEqual(
            profile.fixed,
            profile.sections["system.identity"]
            + profile.sections["system.tool_contract"]
            + profile.sections["tools"],
        )
        self.assertLess(profile.fixed, profile.total)
        self.assertGreater(profile.fixed_ratio, 0.0)

    def test_an_empty_request_does_not_divide_by_zero(self):
        profile = profile_request([])
        self.assertEqual(profile.fixed_ratio, 0.0)


class DisplayBucketsTest(unittest.TestCase):
    def test_known_sections_roll_up_without_inventing_tokens(self):
        buckets = display_buckets(
            {
                "system.identity": 1_400,
                "system.skills": 2_000,
                "system.skills_index": 100,
                "tools": 8_000,
                "tools.mcp": 500,
                "conversation": 3_000,
                "framing": 40,
            }
        )
        by_id = {row["id"]: row["tokens"] for row in buckets}
        self.assertEqual(by_id["system"], 1_400)
        self.assertEqual(by_id["skills"], 2_100)
        self.assertEqual(by_id["tools"], 8_000)
        self.assertEqual(by_id["mcp"], 500)
        self.assertEqual(by_id["conversation"], 3_000)
        self.assertEqual(by_id["other"], 40)
        self.assertEqual(sum(by_id.values()), 1_400 + 2_100 + 8_000 + 500 + 3_000 + 40)

    def test_unknown_section_is_kept_as_other(self):
        buckets = display_buckets({"system.identity": 10, "brand.new": 90})
        by_id = {row["id"]: row["tokens"] for row in buckets}
        self.assertEqual(by_id["system"], 10)
        self.assertEqual(by_id["other"], 90)


class BudgetTest(unittest.TestCase):
    def test_every_bucket_belongs_to_exactly_one_group(self):
        """Otherwise the groups stop re-summing to the request total."""
        seen: set[str] = set()
        for buckets in BUDGET_GROUPS.values():
            for bucket in buckets:
                self.assertNotIn(bucket, seen, f"{bucket} is in two groups")
                seen.add(bucket)

    def test_groups_account_for_every_section_a_real_request_produces(self):
        profile = profile_request(
            [
                _system(IDENTITY, BOOTSTRAP, CONTRACT, HISTORY),
                {"role": "user", "content": "ship it"},
                {"role": "tool", "tool_call_id": "1", "content": "output"},
            ],
            [TOOL],
        )
        grouped = set()
        for buckets in BUDGET_GROUPS.values():
            grouped.update(buckets)
        unaccounted = set(profile.sections) - grouped - {"framing"}
        self.assertEqual(unaccounted, set(), f"ungrouped buckets: {unaccounted}")

    def test_a_group_over_its_ceiling_is_reported_with_its_ratio(self):
        budget = PromptBudget(limits={"tools": 100}, request_max=10**9)
        overruns = budget.overruns(profile_request([_system(IDENTITY)], [TOOL] * 40))
        self.assertEqual(len(overruns), 1)
        self.assertEqual(overruns[0].group, "tools")
        self.assertGreater(overruns[0].ratio, 1.0)

    def test_a_group_within_its_ceiling_is_silent(self):
        budget = PromptBudget(limits={"tools": 10**6}, request_max=10**9)
        self.assertEqual(budget.overruns(profile_request([_system(IDENTITY)], [TOOL])), [])

    def test_the_whole_request_has_its_own_ceiling(self):
        budget = PromptBudget(limits={}, request_max=10)
        overruns = budget.overruns(profile_request([_system(IDENTITY, CONTRACT)]))
        self.assertEqual([o.group for o in overruns], ["request"])

    def test_the_worst_offender_is_reported_first(self):
        profile = profile_request([_system(IDENTITY, CONTRACT)], [TOOL] * 40)
        groups = profile.groups()
        budget = PromptBudget(
            limits={
                "tools": groups["tools"] // 4,
                "core_system": groups["core_system"] // 2,
            },
            request_max=10**9,
        )
        self.assertEqual(
            [o.group for o in budget.overruns(profile)], ["tools", "core_system"]
        )

    def test_an_ordinary_request_only_flags_the_structural_gap(self):
        """The 2026-08-15 average request, as measured on this workspace.

        core_system and tools are genuinely over their engineering target and
        must be flagged. History, tool results and the request total are what
        ordinary work looks like: flagging those would fire on every call and
        teach everyone to ignore the warning.
        """
        flagged = {o.group for o in DEFAULT_BUDGET.overruns(_TODAYS_REQUEST)}
        self.assertEqual(flagged, {"core_system", "tools"})

    def test_skills_already_fit_their_ceiling(self):
        """Progressive disclosure works: this group needs no chantier."""
        profile = PromptProfile(
            sections={"system.skills": 479, "system.skills_index": 776},
            total=1_255,
        )
        self.assertNotIn(
            "skills", {o.group for o in DEFAULT_BUDGET.overruns(profile)}
        )

    def test_the_structural_targets_sit_below_what_was_measured(self):
        """A budget equal to the current state would never ask for anything."""
        self.assertLess(DEFAULT_BUDGET.limits["tools"], 19_193)
        self.assertLess(DEFAULT_BUDGET.limits["core_system"], 10_017)

    def test_a_runaway_request_is_still_caught(self):
        """The request ceiling must stay useful for the case it exists for."""
        profile = PromptProfile(
            sections={"tool_results": 400_000}, total=400_000
        )
        flagged = {o.group for o in DEFAULT_BUDGET.overruns(profile)}
        self.assertIn("request", flagged)
        self.assertIn("tool_results", flagged)


class BudgetWarningNoiseTest(unittest.TestCase):
    """A structural overrun is identical on every request of a session."""

    def setUp(self) -> None:
        reset_budget_warnings()
        self.addCleanup(reset_budget_warnings)

    def _warnings(self) -> list[str]:
        sunk: list[str] = []
        sink = logger.add(sunk.append, level="WARNING", format="{message}")
        self.addCleanup(logger.remove, sink)
        with patch.dict(os.environ, {"NAVIN_PROMPT_PROFILE": "1"}):
            for _ in range(5):
                log_request_profile(
                    [_system(IDENTITY, CONTRACT)], [TOOL] * 200, model="m"
                )
        return sunk

    def test_the_same_overrun_is_reported_once_not_every_call(self):
        self.assertEqual(len(self._warnings()), 1)

    def test_the_one_warning_still_names_the_group(self):
        self.assertIn("tools=", self._warnings()[0])


class FormattingTest(unittest.TestCase):
    def test_the_log_line_leads_with_the_biggest_bucket(self):
        line = format_profile(profile_request([_system(IDENTITY, CONTRACT)]))
        self.assertIn("system.tool_contract=", line)
        self.assertLess(line.index("system.tool_contract="), line.index("system.identity="))


class EnableSwitchTest(unittest.TestCase):
    def test_profiling_is_off_unless_asked(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(profiling_enabled())
        with patch.dict(os.environ, {"NAVIN_PROMPT_PROFILE": "1"}):
            self.assertTrue(profiling_enabled())
        with patch.dict(os.environ, {"NAVIN_PROMPT_PROFILE": "off"}):
            self.assertFalse(profiling_enabled())

    def test_disabled_profiling_does_no_work(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(log_request_profile([_system(IDENTITY)], [TOOL]))

    def test_a_broken_request_cannot_break_the_turn(self):
        with patch.dict(os.environ, {"NAVIN_PROMPT_PROFILE": "1"}):
            self.assertIsNone(log_request_profile(None, None))
            self.assertIsNotNone(log_request_profile([{"role": "user"}], None))


if __name__ == "__main__":
    unittest.main()
