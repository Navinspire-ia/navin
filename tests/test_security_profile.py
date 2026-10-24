# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Security profiles tighten tools without changing factory autonomy defaults."""

from __future__ import annotations

import sys
import unittest
from unittest.mock import patch

from navin.config.schema import Config
from navin.config.security_profile import apply_security_profile


class FactoryDefaultTest(unittest.TestCase):
    def test_security_profile_starts_unset(self) -> None:
        self.assertIsNone(Config().tools.security_profile)

    def test_apply_autonomous_is_a_noop(self) -> None:
        config = Config()
        apply_security_profile(config.tools, "autonomous")
        self.assertFalse(config.tools.approvals.enabled)
        self.assertFalse(config.tools.exec.builtin_deny_rules)
        self.assertFalse(config.tools.restrict_to_workspace)
        self.assertEqual(config.tools.exec.sandbox, "")


class AssistedProfileTest(unittest.TestCase):
    def test_assisted_enables_ask_on_destructive_only(self) -> None:
        tools = Config().tools
        with patch.object(sys, "platform", "linux"):
            apply_security_profile(tools, "assisted")
        self.assertTrue(tools.approvals.enabled)
        self.assertTrue(tools.approvals.remember)
        self.assertTrue(tools.exec.builtin_deny_rules)
        # Only deletions ask. The workspace fence would put a card on every
        # read or write outside the project, which is ordinary development.
        self.assertFalse(tools.restrict_to_workspace)
        self.assertEqual(tools.exec.sandbox, "native")
        # Autonomy knobs that are not part of the posture stay open.
        self.assertFalse(tools.ssrf_protection)
        self.assertEqual(tools.exec.deny_patterns, [])
        self.assertEqual(tools.exec.allow_patterns, [])

    def test_assisted_skips_sandbox_on_windows(self) -> None:
        tools = Config().tools
        with patch.object(sys, "platform", "win32"):
            apply_security_profile(tools, "assisted")
        self.assertEqual(tools.exec.sandbox, "")
        self.assertTrue(tools.approvals.enabled)

    def test_assisted_does_not_override_an_existing_sandbox(self) -> None:
        tools = Config().tools
        tools.exec.sandbox = "bwrap"
        with patch.object(sys, "platform", "linux"):
            apply_security_profile(tools, "assisted")
        self.assertEqual(tools.exec.sandbox, "bwrap")

    def test_strict_adds_ssrf_protection_and_the_workspace_fence(self) -> None:
        tools = Config().tools
        apply_security_profile(tools, "strict")
        self.assertTrue(tools.ssrf_protection)
        self.assertTrue(tools.restrict_to_workspace)
        self.assertTrue(tools.approvals.enabled)


class FullAccessDefaultTest(unittest.TestCase):
    def test_a_fresh_config_never_asks(self) -> None:
        """Full access is the default everywhere, the WebUI included: the
        composer's permission menu opens on "Full access" until the user picks
        another mode."""
        from navin.webui.exec_policy_api import read_approval_mode

        self.assertEqual(read_approval_mode(Config()), "autonomous")


if __name__ == "__main__":
    unittest.main()
