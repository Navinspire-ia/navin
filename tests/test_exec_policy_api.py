# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Settings > Security confirmation posture (autonomous / risky / always)."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from navin.config.schema import Config
from navin.webui.exec_policy_api import (
    apply_approval_mode,
    exec_policy_payload,
    read_approval_mode,
    update_exec_policy,
)


class ApprovalModeTest(unittest.TestCase):
    def test_factory_reads_as_autonomous(self) -> None:
        self.assertEqual(read_approval_mode(Config()), "autonomous")

    def test_assisted_reads_as_risky(self) -> None:
        config = Config()
        config.tools.approvals.enabled = True
        self.assertEqual(read_approval_mode(config), "risky")

    def test_always_reads_as_always(self) -> None:
        config = Config()
        config.tools.approvals.enabled = True
        config.tools.approvals.exec_ask = "always"
        self.assertEqual(read_approval_mode(config), "always")

    def test_apply_risky_turns_the_gate_on(self) -> None:
        config = Config()
        apply_approval_mode(config, "risky")
        self.assertTrue(config.tools.approvals.enabled)
        self.assertEqual(config.tools.approvals.exec_ask, "destructive")
        self.assertTrue(config.tools.exec.builtin_deny_rules)
        self.assertEqual(config.tools.security_profile, "assisted")
        self.assertEqual(read_approval_mode(config), "risky")

    def test_apply_always_asks_every_command(self) -> None:
        config = Config()
        apply_approval_mode(config, "always")
        self.assertTrue(config.tools.approvals.enabled)
        self.assertEqual(config.tools.approvals.exec_ask, "always")
        self.assertEqual(read_approval_mode(config), "always")

    def test_apply_autonomous_turns_the_gate_off(self) -> None:
        config = Config()
        apply_approval_mode(config, "risky")
        apply_approval_mode(config, "autonomous")
        self.assertFalse(config.tools.approvals.enabled)
        self.assertEqual(config.tools.approvals.exec_ask, "destructive")
        self.assertEqual(config.tools.security_profile, "autonomous")
        self.assertEqual(read_approval_mode(config), "autonomous")

    def test_payload_includes_approval_mode(self) -> None:
        payload = exec_policy_payload(Config())
        self.assertEqual(payload["approval_mode"], "autonomous")

    def test_update_persists_always(self) -> None:
        config = Config()
        with (
            patch("navin.webui.exec_policy_api.load_config", return_value=config),
            patch("navin.webui.exec_policy_api.save_config"),
        ):
            payload = update_exec_policy({"approval_mode": "always"})
        self.assertEqual(payload["approval_mode"], "always")
        self.assertTrue(config.tools.approvals.enabled)
        self.assertEqual(config.tools.approvals.exec_ask, "always")

    def test_unknown_mode_is_rejected(self) -> None:
        from navin.webui.exec_policy_api import ExecPolicyError

        with self.assertRaises(ExecPolicyError):
            apply_approval_mode(Config(), "maybe")


if __name__ == "__main__":
    unittest.main()
