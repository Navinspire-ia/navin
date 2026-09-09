# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Soft tool errors must not loop forever: identical failures escalate.

With ``fail_on_tool_error=False`` (the product default) a failing tool call
returns to the model as an error result instead of aborting the run. That
buys autonomy, but only with a bound: the same verbatim call failing over
and over has to be told to stop, or the iteration budget burns on a call
that will never succeed.
"""

from __future__ import annotations

import unittest

from navin.config.schema import AgentDefaults
from navin.utils.runtime import (
    repeated_readonly_tool_error,
    repeated_tool_failure_hint,
    reset_readonly_spin_counts,
)


class RepeatedToolFailureHintTest(unittest.TestCase):
    def test_the_first_failure_gets_no_escalation(self) -> None:
        counts: dict[str, int] = {}
        self.assertIsNone(
            repeated_tool_failure_hint("grep", {"pattern": "x"}, counts)
        )

    def test_the_second_identical_failure_escalates(self) -> None:
        counts: dict[str, int] = {}
        repeated_tool_failure_hint("grep", {"pattern": "x"}, counts)
        hint = repeated_tool_failure_hint("grep", {"pattern": "x"}, counts)
        self.assertIsNotNone(hint)
        self.assertIn("grep", hint)
        self.assertIn("Do not repeat it verbatim", hint)

    def test_changed_arguments_reset_the_budget(self) -> None:
        counts: dict[str, int] = {}
        repeated_tool_failure_hint("grep", {"pattern": "x"}, counts)
        self.assertIsNone(
            repeated_tool_failure_hint("grep", {"pattern": "y"}, counts)
        )

    def test_a_different_tool_with_same_arguments_is_a_fresh_budget(self) -> None:
        counts: dict[str, int] = {}
        repeated_tool_failure_hint("grep", {"pattern": "x"}, counts)
        self.assertIsNone(
            repeated_tool_failure_hint("read_file", {"pattern": "x"}, counts)
        )

    def test_unserializable_arguments_do_not_crash(self) -> None:
        counts: dict[str, int] = {}
        self.assertIsNone(
            repeated_tool_failure_hint("odd", {"obj": object()}, counts)
        )


class FailSoftDefaultTest(unittest.TestCase):
    def test_tool_errors_are_soft_by_default(self) -> None:
        self.assertFalse(AgentDefaults().fail_on_tool_error)


class RepeatedReadonlyToolErrorTest(unittest.TestCase):
    def test_two_identical_reads_are_allowed(self) -> None:
        counts: dict[str, int] = {}
        self.assertIsNone(
            repeated_readonly_tool_error("read_file", {"path": "a.py"}, counts)
        )
        self.assertIsNone(
            repeated_readonly_tool_error("read_file", {"path": "a.py"}, counts)
        )

    def test_the_third_identical_read_is_blocked(self) -> None:
        counts: dict[str, int] = {}
        repeated_readonly_tool_error("read_file", {"path": "a.py"}, counts)
        repeated_readonly_tool_error("read_file", {"path": "a.py"}, counts)
        blocked = repeated_readonly_tool_error("read_file", {"path": "a.py"}, counts)
        self.assertIsNotNone(blocked)
        self.assertIn("read_file", blocked or "")
        self.assertIn("Do not repeat it", blocked or "")

    def test_a_different_path_is_a_fresh_budget(self) -> None:
        counts: dict[str, int] = {}
        repeated_readonly_tool_error("read_file", {"path": "a.py"}, counts)
        repeated_readonly_tool_error("read_file", {"path": "a.py"}, counts)
        self.assertIsNone(
            repeated_readonly_tool_error("read_file", {"path": "b.py"}, counts)
        )

    def test_mutating_tools_are_not_throttled_here(self) -> None:
        counts: dict[str, int] = {}
        for _ in range(4):
            self.assertIsNone(
                repeated_readonly_tool_error("apply_patch", {"path": "a.py"}, counts)
            )

    def test_an_edit_resets_the_read_budget(self) -> None:
        counts: dict[str, int] = {}
        repeated_readonly_tool_error("read_file", {"path": "a.py"}, counts)
        repeated_readonly_tool_error("read_file", {"path": "a.py"}, counts)
        reset_readonly_spin_counts(counts)
        self.assertIsNone(
            repeated_readonly_tool_error("read_file", {"path": "a.py"}, counts)
        )


if __name__ == "__main__":
    unittest.main()
