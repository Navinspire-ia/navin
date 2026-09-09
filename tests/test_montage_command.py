# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Slash /montage workflow wiring."""

from __future__ import annotations

import unittest

from navin.agent.model_routes import WORKFLOW_ROUTE_ROLES
from navin.agent.skills import BUILTIN_SKILLS_DIR
from navin.agent.tools.set_composer_mode import _ALLOWED_MODES
from navin.command.builtin import (
    _COMPOSER_MODE_BY_COMMAND,
    _DELIVERY_WORKFLOWS,
    _HTML_REPORT_WORKFLOWS,
    _TRACKED_WORKFLOWS,
    _WORKFLOW_BRIEFS,
)
from navin.command.modules import _MODULE_OWNED_COMMANDS, CODE_HIDDEN_COMMANDS


class MontageCommandTest(unittest.TestCase):
    def test_brief_exists_with_montage_studio(self) -> None:
        self.assertIn("/montage", _WORKFLOW_BRIEFS)
        title, skills, brief = _WORKFLOW_BRIEFS["/montage"]
        self.assertEqual(title, "Montage studio")
        names = [n.strip() for n in skills.split(",") if n.strip()]
        self.assertIn("montage-studio", names)
        self.assertIn("playwright-browser", names)
        self.assertIn("image-generation", names)
        self.assertIn("video-generation", names)
        self.assertTrue((BUILTIN_SKILLS_DIR / "montage-studio" / "SKILL.md").is_file())
        # The server tints the composer at workflow start; the brief must not
        # spend a model round-trip re-asking for it.
        self.assertNotIn("set_composer_mode(mode=montage)", brief)
        self.assertIn("The editor already shows Montage mode", brief)
        self.assertIn("record_start", brief)
        self.assertIn("demo_register", brief)
        self.assertIn("NEVER auto-publish", brief)

    def test_composer_mode_and_routes(self) -> None:
        self.assertEqual(_COMPOSER_MODE_BY_COMMAND["/montage"], "montage")
        self.assertEqual(WORKFLOW_ROUTE_ROLES["/montage"], "docs")
        self.assertIn("montage", _ALLOWED_MODES)

    def test_tracked_delivery_html(self) -> None:
        self.assertIn("/montage", _TRACKED_WORKFLOWS)
        self.assertIn("/montage", _DELIVERY_WORKFLOWS)
        self.assertIn("/montage", _HTML_REPORT_WORKFLOWS)

    def test_marketing_module_owns_montage(self) -> None:
        self.assertIn("/montage", _MODULE_OWNED_COMMANDS["marketing"])
        self.assertIn("/montage", CODE_HIDDEN_COMMANDS)

    def test_montage_view_scopes_like_marketing(self) -> None:
        from navin.command.modules import (
            disabled_skills_for_module,
            is_command_allowed_for_module,
            normalize_product_module,
        )

        self.assertEqual(normalize_product_module("montage"), "marketing")
        self.assertTrue(is_command_allowed_for_module("/montage", "montage"))
        self.assertTrue(is_command_allowed_for_module("/campaign", "montage"))
        self.assertFalse(is_command_allowed_for_module("/ads", "montage"))
        self.assertEqual(
            disabled_skills_for_module("montage"),
            disabled_skills_for_module("marketing"),
        )


if __name__ == "__main__":
    unittest.main()
