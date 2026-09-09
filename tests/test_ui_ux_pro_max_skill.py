# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""ui-ux-pro-max is the default web design skill; briefs preload it + framer-motion policy."""

from __future__ import annotations

import unittest

from navin.agent.skills import BUILTIN_SKILLS_DIR
from navin.command.builtin import _WORKFLOW_BRIEFS


class UiUxProMaxSkillTest(unittest.TestCase):
    def test_skill_and_search_script_exist(self) -> None:
        root = BUILTIN_SKILLS_DIR / "ui-ux-pro-max"
        self.assertTrue((root / "SKILL.md").is_file())
        self.assertTrue((root / "scripts" / "search.py").is_file())
        self.assertTrue((root / "data" / "styles.csv").is_file())

    def test_skill_mandates_framer_motion(self) -> None:
        text = (BUILTIN_SKILLS_DIR / "ui-ux-pro-max" / "SKILL.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("framer-motion", text)
        self.assertIn("npm install framer-motion", text)

    def test_skill_mandates_three_stack_for_dev_and_reports(self) -> None:
        text = (BUILTIN_SKILLS_DIR / "ui-ux-pro-max" / "SKILL.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("npm install three @react-three/fiber @react-three/drei", text)
        self.assertIn("--stack threejs", text)
        self.assertIn("never wallpaper", text.lower())

    def test_fullstack_dev_mandates_framer_motion(self) -> None:
        text = (BUILTIN_SKILLS_DIR / "fullstack-dev" / "SKILL.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("framer-motion", text)
        self.assertIn("ui-ux-pro-max", text)
        self.assertIn("@react-three/fiber", text)
        self.assertIn("@react-three/drei", text)

    def test_code_web_defaults_lock_official_design_systems(self) -> None:
        fullstack = (BUILTIN_SKILLS_DIR / "fullstack-dev" / "SKILL.md").read_text(
            encoding="utf-8"
        )
        ux = (BUILTIN_SKILLS_DIR / "ui-ux-pro-max" / "SKILL.md").read_text(
            encoding="utf-8"
        )
        for text in (fullstack, ux):
            self.assertIn("@mui/material", text)
            self.assertIn("@fluentui/react", text)
            self.assertIn("@carbon/react", text)
            self.assertIn("ask_user", text)
            self.assertNotIn("Prefer Tailwind unless the user specifies otherwise", text)
        for command in ("/blueprint", "/forge", "/cruise", "/mission"):
            brief = _WORKFLOW_BRIEFS[command][2]
            self.assertTrue(
                "MUI" in brief or "@mui/material" in brief,
                command,
            )
            self.assertTrue("Fluent" in brief or "@fluentui/react" in brief, command)
            self.assertTrue("Carbon" in brief or "@carbon/react" in brief, command)
            self.assertIn("ask_user", brief, command)

    def test_web_workflows_preload_ui_ux_pro_max(self) -> None:
        for command in ("/blueprint", "/forge", "/cruise", "/mission"):
            skills = _WORKFLOW_BRIEFS[command][1]
            self.assertIn("ui-ux-pro-max", skills, command)

    def test_forge_brief_requires_framer_motion_install(self) -> None:
        brief = _WORKFLOW_BRIEFS["/forge"][2]
        self.assertIn("framer-motion", brief)
        self.assertIn("ui-ux-pro-max", brief)
        for token in (
            "@mui/material",
            "@fluentui/react",
            "@carbon/react",
            "framer-motion three @react-three/fiber @react-three/drei",
        ):
            self.assertIn(token, brief, token)

    def test_code_briefs_require_three_stack(self) -> None:
        for command in ("/blueprint", "/forge", "/cruise", "/mission"):
            brief = _WORKFLOW_BRIEFS[command][2]
            self.assertIn("@react-three/fiber", brief, command)
            self.assertIn("@react-three/drei", brief, command)
            self.assertIn("threejs", brief, command)

    def test_marketing_and_montage_briefs_require_three_stack(self) -> None:
        campaign = _WORKFLOW_BRIEFS["/campaign"][2]
        montage = _WORKFLOW_BRIEFS["/montage"][2]
        self.assertIn("@react-three/fiber", campaign)
        self.assertIn("threejs", campaign)
        self.assertNotIn("Three.js only if", campaign)
        self.assertIn("three + R3F + drei", montage)
        self.assertIn("threejs", montage)

    def test_design_system_search_smoke(self) -> None:
        import subprocess
        import sys

        script = BUILTIN_SKILLS_DIR / "ui-ux-pro-max" / "scripts" / "search.py"
        result = subprocess.run(
            [sys.executable, str(script), "saas landing minimal", "--design-system", "-p", "Test"],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("DESIGN SYSTEM", result.stdout.upper())
        self.assertIn("COLORS", result.stdout.upper())


if __name__ == "__main__":
    unittest.main()
