# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Product-module scoping for slash commands and studio skills."""

from __future__ import annotations

import unittest

# Import agent first so command↔agent circular imports resolve like other tests.
import navin.agent.skills  # noqa: F401
from navin.command.builtin import builtin_command_palette
from navin.command.modules import (
    CODE_HIDDEN_COMMANDS,
    disabled_skills_for_module,
    exclusive_studio_skills,
    is_command_allowed_for_module,
    normalize_product_module,
)


class ProductModuleNormalizeTest(unittest.TestCase):
    def test_dev_alias_maps_to_code(self) -> None:
        self.assertEqual(normalize_product_module("dev"), "code")
        self.assertEqual(normalize_product_module("code"), "code")

    def test_unknown_returns_none(self) -> None:
        self.assertIsNone(normalize_product_module("chat"))
        self.assertIsNone(normalize_product_module(""))
        self.assertIsNone(normalize_product_module(None))


class ProductModuleCommandsTest(unittest.TestCase):
    def test_code_hides_commercial_studios_keeps_studio(self) -> None:
        for command in CODE_HIDDEN_COMMANDS:
            self.assertFalse(is_command_allowed_for_module(command, "code"), command)
        self.assertTrue(is_command_allowed_for_module("/studio", "code"))
        self.assertTrue(is_command_allowed_for_module("/forge", "code"))
        self.assertTrue(is_command_allowed_for_module("/ops", "code"))
        self.assertFalse(is_command_allowed_for_module("/scrape", "code"))

    def test_scraping_module_allows_scrape_and_studio(self) -> None:
        self.assertEqual(normalize_product_module("scrape"), "scraping")
        self.assertTrue(is_command_allowed_for_module("/scrape", "scraping"))
        self.assertTrue(is_command_allowed_for_module("/studio", "scraping"))
        self.assertFalse(is_command_allowed_for_module("/seo", "scraping"))
        self.assertFalse(is_command_allowed_for_module("/campaign", "scraping"))

    def test_seo_module_allows_seo_only_among_studios(self) -> None:
        self.assertTrue(is_command_allowed_for_module("/seo", "seo"))
        self.assertFalse(is_command_allowed_for_module("/campaign", "seo"))
        self.assertFalse(is_command_allowed_for_module("/ads", "seo"))
        self.assertFalse(is_command_allowed_for_module("/leads", "seo"))
        self.assertTrue(is_command_allowed_for_module("/forge", "seo"))

    def test_montage_view_aliases_to_marketing(self) -> None:
        self.assertEqual(normalize_product_module("montage"), "marketing")
        self.assertTrue(is_command_allowed_for_module("/montage", "montage"))
        self.assertTrue(is_command_allowed_for_module("/campaign", "montage"))
        self.assertTrue(is_command_allowed_for_module("/marketing", "marketing"))
        self.assertTrue(is_command_allowed_for_module("/marketing", "montage"))
        self.assertFalse(is_command_allowed_for_module("/ads", "montage"))
        self.assertFalse(is_command_allowed_for_module("/seo", "montage"))
        self.assertFalse(is_command_allowed_for_module("/leads", "montage"))
        self.assertTrue(is_command_allowed_for_module("/forge", "montage"))

    def test_ads_module_allows_ads_only_among_studios(self) -> None:
        self.assertEqual(normalize_product_module("ads"), "ads")
        self.assertTrue(is_command_allowed_for_module("/ads", "ads"))
        self.assertFalse(is_command_allowed_for_module("/campaign", "ads"))
        self.assertFalse(is_command_allowed_for_module("/seo", "ads"))
        self.assertFalse(is_command_allowed_for_module("/leads", "ads"))
        self.assertTrue(is_command_allowed_for_module("/forge", "ads"))
        self.assertFalse(is_command_allowed_for_module("/ads", "code"))

    def test_palette_filters_code_module(self) -> None:
        commands = {row["command"] for row in builtin_command_palette("code")}
        self.assertIn("/studio", commands)
        self.assertIn("/forge", commands)
        for command in CODE_HIDDEN_COMMANDS:
            self.assertNotIn(command, commands)
        unscoped = {row["command"] for row in builtin_command_palette()}
        self.assertTrue(CODE_HIDDEN_COMMANDS.issubset(unscoped))


class ProductModuleSkillsTest(unittest.TestCase):
    def test_code_disables_seo_and_leads_exclusives(self) -> None:
        exclusive = exclusive_studio_skills()
        disabled = disabled_skills_for_module("code")
        for name in exclusive.get("risklens", ()):
            self.assertIn(name, disabled)
        for name in exclusive.get("seo", ()):
            self.assertIn(name, disabled)
        for name in exclusive.get("leads", ()):
            self.assertIn(name, disabled)
        for name in exclusive.get("marketing", ()):
            self.assertIn(name, disabled)
        for name in exclusive.get("ads", ()):
            self.assertIn(name, disabled)
        for name in exclusive.get("scraping", ()):
            self.assertIn(name, disabled)
        # Document exclusives stay available from Code.
        for name in exclusive.get("content", ()):
            self.assertNotIn(name, disabled)

    def test_code_never_hides_what_its_own_workflows_ask_for(self) -> None:
        """A skill named by /forge or /blueprint cannot be a studio exclusive.

        ``ui-ux-pro-max`` sits in the /forge, /cruise and /blueprint briefs
        and in the /marketing brief. Counting studio briefs alone made it a
        marketing exclusive, so the Code workbench hid it: /forge suggested a
        playbook that ``skill action=read`` then refused, and ``$ui-ux-pro-max``
        in a message loaded nothing.
        """
        from navin.command.builtin import _WORKFLOW_BRIEFS
        from navin.command.modules import _MODULE_OWNED_COMMANDS

        studio_commands = set().union(*_MODULE_OWNED_COMMANDS.values())
        code_side: set[str] = set()
        for command, (_title, skills_csv, _brief) in _WORKFLOW_BRIEFS.items():
            if command not in studio_commands:
                code_side.update(s.strip() for s in skills_csv.split(",") if s.strip())
        self.assertIn("ui-ux-pro-max", code_side)
        disabled = disabled_skills_for_module("code")
        self.assertEqual(sorted(code_side & disabled), [])
        for module, names in exclusive_studio_skills().items():
            self.assertEqual(sorted(names & code_side), [], module)

    def test_a_mentioned_ui_skill_loads_its_body_in_the_code_workbench(self) -> None:
        import tempfile
        from pathlib import Path

        from navin.agent.context import ContextBuilder

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "AGENTS.md").write_text("# Agents\n", encoding="utf-8")
            prompt = ContextBuilder(root).build_system_prompt(
                slim_skill_preload=True,
                include_memory_recent_history=False,
                extra_disabled_skills=disabled_skills_for_module("code"),
                current_message="refais l'ecran settings avec $ui-ux-pro-max",
            )
        self.assertIn("### Skill: ui-ux-pro-max", prompt)

    def test_trading_module_allows_trading_only_among_studios(self) -> None:
        self.assertEqual(normalize_product_module("trading"), "trading")
        self.assertTrue(is_command_allowed_for_module("/trading", "trading"))
        self.assertFalse(is_command_allowed_for_module("/campaign", "trading"))
        self.assertFalse(is_command_allowed_for_module("/trading", "code"))
        self.assertFalse(is_command_allowed_for_module("/trading", "risklens"))

    def test_risklens_module_allows_risklens_only_among_studios(self) -> None:
        self.assertTrue(is_command_allowed_for_module("/risklens", "risklens"))
        self.assertFalse(is_command_allowed_for_module("/campaign", "risklens"))
        self.assertFalse(is_command_allowed_for_module("/seo", "risklens"))
        self.assertTrue(is_command_allowed_for_module("/forge", "risklens"))
        self.assertEqual(
            __import__("navin.command.modules", fromlist=["normalize_product_module"])
            .normalize_product_module("premortem"),
            "risklens",
        )

    def test_montage_keeps_marketing_exclusives_hides_ads(self) -> None:
        exclusive = exclusive_studio_skills()
        disabled = disabled_skills_for_module("montage")
        for name in exclusive.get("marketing", ()):
            self.assertNotIn(name, disabled)
        for name in exclusive.get("ads", ()):
            self.assertIn(name, disabled)

    def test_seo_keeps_seo_exclusives_hides_leads(self) -> None:
        exclusive = exclusive_studio_skills()
        disabled = disabled_skills_for_module("seo")
        for name in exclusive.get("seo", ()):
            self.assertNotIn(name, disabled)
        for name in exclusive.get("leads", ()):
            self.assertIn(name, disabled)

    def test_scrapling_is_preloaded_for_scraping_and_career(self) -> None:
        from navin.command.modules import default_preload_skills_for_module

        exclusive = exclusive_studio_skills()
        # Shared with Career so the scrape tool / Scrapling stack stays usable there.
        self.assertNotIn("scrapling", exclusive.get("scraping", set()))
        self.assertNotIn("scrapling", disabled_skills_for_module("career"))
        self.assertNotIn("scrapling", disabled_skills_for_module("scraping"))
        self.assertEqual(
            default_preload_skills_for_module("scraping"),
            ["scrapling", "scrape-operator"],
        )
        self.assertIn("scrapling", default_preload_skills_for_module("career"))
        self.assertEqual(default_preload_skills_for_module("code"), [])

    def test_leads_module_preloads_prospecting_skills(self) -> None:
        from navin.command.modules import default_preload_skills_for_module

        preloaded = default_preload_skills_for_module("leads")
        self.assertIn("lead-prospector", preloaded)
        self.assertIn("lead-enrichment", preloaded)

    def test_connector_skills_are_module_exclusive(self) -> None:
        exclusive = exclusive_studio_skills()
        self.assertIn("seo-data-provider", exclusive.get("seo", set()))
        self.assertIn("lead-enrichment", exclusive.get("leads", set()))
        # Shared expert contract must not be locked to a single studio.
        self.assertNotIn("studio-expert-contract", exclusive.get("seo", set()))
        self.assertNotIn("studio-expert-contract", exclusive.get("marketing", set()))
        self.assertNotIn("studio-expert-contract", exclusive.get("leads", set()))


if __name__ == "__main__":
    unittest.main()
