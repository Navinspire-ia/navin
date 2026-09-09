# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""The posture navin ships with: the agent is not fenced in by the product.

Every limit that used to be hardcoded is now a setting, and every one of those
settings starts open. Deciding that an agent may not delete a directory, reach a
private address or leave the project is the operator's call, and they make it in
config.json; making it for them in the source meant nobody could unmake it.

This file is the single place that records those defaults. A change that
re-tightens one fails here, which is the point: the next person to add a guard
has to come and say so out loud rather than land a constant.
"""

from __future__ import annotations

import unittest

from navin.agent.tools.shell import ExecTool
from navin.config.schema import Config


class ExecDefaultTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tools = Config().tools

    def test_no_command_is_denied(self) -> None:
        self.assertEqual(self.tools.exec.deny_patterns, [])
        self.assertFalse(self.tools.exec.builtin_deny_rules)

    def test_no_allowlist_narrows_what_may_run(self) -> None:
        self.assertEqual(self.tools.exec.allow_patterns, [])

    def test_any_shell_the_host_has_may_be_used(self) -> None:
        self.assertEqual(self.tools.exec.allowed_shells, [])

    def test_a_long_running_command_is_not_cut_short(self) -> None:
        self.assertEqual(self.tools.exec.max_timeout, 0)

    def test_nothing_is_sandboxed(self) -> None:
        self.assertEqual(self.tools.exec.sandbox, "")

    def test_the_tool_built_from_defaults_denies_nothing(self) -> None:
        """The config is only half of it; the tool has to read it that way."""
        tool = ExecTool(
            builtin_deny_rules=self.tools.exec.builtin_deny_rules,
            deny_patterns=self.tools.exec.deny_patterns,
            allow_patterns=self.tools.exec.allow_patterns,
        )
        self.assertEqual(tool.deny_patterns, [])
        self.assertEqual(tool.allow_patterns, [])


class ReachDefaultTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tools = Config().tools

    def test_the_agent_may_leave_the_project_directory(self) -> None:
        self.assertFalse(self.tools.restrict_to_workspace)

    def test_a_private_address_is_reachable(self) -> None:
        self.assertFalse(self.tools.ssrf_protection)

    def test_no_turn_stops_to_ask_permission(self) -> None:
        self.assertFalse(self.tools.approvals.enabled)

    def test_security_profile_stays_unset(self) -> None:
        """WebUI may switch a fresh config to assisted; the factory must not."""
        self.assertIsNone(self.tools.security_profile)


class FileDefaultTest(unittest.TestCase):
    def setUp(self) -> None:
        self.file = Config().tools.file

    def test_only_version_control_metadata_is_off_limits(self) -> None:
        """Losing these loses history itself, which no baseline restores."""
        self.assertEqual(
            sorted(self.file.protected_paths), [".checkpoints", ".git", ".hg", ".svn"]
        )

    def test_the_review_ceilings_are_operator_tunable(self) -> None:
        """These bound what stays undoable, and 0 lifts them."""
        self.assertEqual(self.file.max_tracked_files, 200)
        self.assertEqual(self.file.max_tracked_mib, 64)


if __name__ == "__main__":
    unittest.main()
