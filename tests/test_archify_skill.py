"""archify is the default Navin skill for architecture diagrams, PPT, tenders, plans."""

from __future__ import annotations

import unittest

from navin.agent.skills import BUILTIN_SKILLS_DIR
from navin.command.builtin import _WORKFLOW_BRIEFS
from navin.command.modules import default_preload_skills_for_module
from navin.tenders.stack import TENDERS_SKILLS


class ArchifySkillTest(unittest.TestCase):
    def test_skill_and_cli_exist(self) -> None:
        root = BUILTIN_SKILLS_DIR / "archify"
        self.assertTrue((root / "SKILL.md").is_file())
        self.assertTrue((root / "bin" / "archify.mjs").is_file())
        self.assertTrue((root / "schemas").is_dir())

    def test_skill_is_navin_default_for_architecture(self) -> None:
        text = (BUILTIN_SKILLS_DIR / "archify" / "SKILL.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("default_for: \"architecture\"", text)
        self.assertIn("Navin default for architecture", text)
        self.assertIn("tender", text.lower())
        self.assertNotIn("\u2014", text)
        self.assertNotIn("\u2013", text)

    def test_studio_tenders_and_blueprint_preload_archify(self) -> None:
        for command in ("/studio", "/tenders", "/blueprint"):
            skills = _WORKFLOW_BRIEFS[command][1]
            self.assertIn("archify", skills, command)

    def test_tenders_stack_and_module_preload_match(self) -> None:
        self.assertIn("archify", TENDERS_SKILLS)
        self.assertEqual(
            default_preload_skills_for_module("tenders"),
            list(TENDERS_SKILLS),
        )
        self.assertIn("archify", default_preload_skills_for_module("content"))


if __name__ == "__main__":
    unittest.main()
