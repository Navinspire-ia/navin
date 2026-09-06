"""Security profiles tighten tools without changing factory autonomy defaults."""

from __future__ import annotations

import sys
import unittest
from unittest.mock import patch

from navin.config.schema import Config
from navin.config.security_profile import (
    apply_security_profile,
    ensure_webui_assisted_profile,
)


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


class WebUIEnsureTest(unittest.TestCase):
    def test_webui_switches_autonomous_to_assisted(self) -> None:
        config = Config()
        with patch.object(sys, "platform", "linux"):
            changed = ensure_webui_assisted_profile(config)
        self.assertTrue(changed)
        self.assertEqual(config.tools.security_profile, "assisted")
        self.assertTrue(config.tools.approvals.enabled)
        self.assertTrue(config.tools.exec.builtin_deny_rules)

    def test_webui_ensure_is_idempotent_once_assisted(self) -> None:
        config = Config()
        ensure_webui_assisted_profile(config)
        self.assertFalse(ensure_webui_assisted_profile(config))

    def test_explicit_non_autonomous_profile_is_left_alone(self) -> None:
        config = Config()
        config.tools.security_profile = "strict"
        self.assertFalse(ensure_webui_assisted_profile(config))
        self.assertEqual(config.tools.security_profile, "strict")
        self.assertFalse(config.tools.approvals.enabled)

    def test_explicit_autonomous_opts_out_of_webui_soft_switch(self) -> None:
        config = Config()
        config.tools.security_profile = "autonomous"
        self.assertFalse(ensure_webui_assisted_profile(config))
        self.assertEqual(config.tools.security_profile, "autonomous")
        self.assertFalse(config.tools.approvals.enabled)

    def test_unset_profile_still_gets_approvals_if_other_knobs_are_on(self) -> None:
        config = Config()
        config.tools.restrict_to_workspace = True
        self.assertTrue(ensure_webui_assisted_profile(config))
        self.assertEqual(config.tools.security_profile, "assisted")
        self.assertTrue(config.tools.approvals.enabled)


if __name__ == "__main__":
    unittest.main()
