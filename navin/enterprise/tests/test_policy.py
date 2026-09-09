# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Unit tests for org tool allow/deny policy."""

from __future__ import annotations

import unittest

from navin.enterprise.policy import ToolPolicy, check_tool_allowed


class ToolPolicyTest(unittest.TestCase):
    def test_empty_config_allows_named_tools(self) -> None:
        self.assertTrue(check_tool_allowed("shell", None))
        self.assertTrue(check_tool_allowed("shell", {}))

    def test_empty_tool_name_denied(self) -> None:
        self.assertFalse(check_tool_allowed("", {}))
        self.assertFalse(check_tool_allowed(None, {"allow": ["shell"]}))

    def test_deny_wins(self) -> None:
        cfg = {"allow": ["shell", "browser"], "deny": ["browser"]}
        self.assertTrue(check_tool_allowed("shell", cfg))
        self.assertFalse(check_tool_allowed("browser", cfg))

    def test_allowlist_fail_closed(self) -> None:
        cfg = {"allow": ["read_file"]}
        self.assertTrue(check_tool_allowed("read_file", cfg))
        self.assertFalse(check_tool_allowed("shell", cfg))

    def test_case_insensitive(self) -> None:
        policy = ToolPolicy.from_config({"deny": ["Shell"]})
        self.assertFalse(policy.is_allowed("SHELL"))


if __name__ == "__main__":
    unittest.main()
