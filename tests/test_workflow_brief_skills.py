"""Every skill named by a workflow brief must exist under the bundled skills.

The briefs of ``/studio``, ``/campaign``, ``/seo`` and friends list skills as a
plain comma-separated string, so a typo or a renamed skill fails silently: the
agent simply never loads the instructions the brief promised.
"""

import unittest

from navin.agent.skills import BUILTIN_SKILLS_DIR
from navin.command.builtin import _WORKFLOW_BRIEFS


def _skill_names(skills: str) -> list[str]:
    return [name.strip() for name in skills.split(",") if name.strip()]


class WorkflowBriefSkillsTest(unittest.TestCase):
    def test_every_named_skill_is_bundled(self) -> None:
        missing: list[str] = []
        for command, (_title, skills, _brief) in _WORKFLOW_BRIEFS.items():
            for name in _skill_names(skills):
                if not (BUILTIN_SKILLS_DIR / name / "SKILL.md").is_file():
                    missing.append(f"{command}: {name}")
        self.assertEqual(missing, [])

    def test_no_skill_is_listed_twice_in_one_brief(self) -> None:
        for command, (_title, skills, _brief) in _WORKFLOW_BRIEFS.items():
            names = _skill_names(skills)
            self.assertEqual(len(names), len(set(names)), command)

    def test_campaign_brief_loads_super_render_skills(self) -> None:
        names = _skill_names(_WORKFLOW_BRIEFS["/campaign"][1])
        for skill in (
            "digital-marketing",
            "ui-ux-pro-max",
            "presentation-designer",
            "pptx-generator",
            "montage-studio",
        ):
            self.assertIn(skill, names)

    def test_media_capable_briefs_load_the_media_skills(self) -> None:
        """The studio, marketing and seo briefs promise visuals, so they must teach them."""
        for command in ("/studio", "/campaign", "/seo", "/montage"):
            self.assertIn("image-generation", _skill_names(_WORKFLOW_BRIEFS[command][1]), command)

    def test_commercial_studios_load_expert_contract_and_critic(self) -> None:
        for command in ("/campaign", "/seo", "/leads", "/montage"):
            names = _skill_names(_WORKFLOW_BRIEFS[command][1])
            self.assertIn("studio-expert-contract", names, command)
            self.assertIn("critic-reviewer", names, command)

    def test_montage_brief_loads_montage_studio_and_video(self) -> None:
        names = _skill_names(_WORKFLOW_BRIEFS["/montage"][1])
        self.assertIn("montage-studio", names)
        self.assertIn("video-generation", names)

    def test_scrape_brief_loads_scrapling_first(self) -> None:
        names = _skill_names(_WORKFLOW_BRIEFS["/scrape"][1])
        self.assertEqual(names[0], "scrapling")
        self.assertIn("scrape-operator", names)
        brief = _WORKFLOW_BRIEFS["/scrape"][2]
        self.assertIn("Scrapling 0.4.14", brief)
        self.assertIn("scrapling==0.4.14", brief)

    def test_leads_brief_loads_data_quality_and_enrichment(self) -> None:
        names = _skill_names(_WORKFLOW_BRIEFS["/leads"][1])
        self.assertIn("data-quality-agent", names)
        self.assertIn("lead-enrichment", names)
        self.assertIn("crm-update-agent", names)
        self.assertIn("deep-web-research", names)
        self.assertIn("web-extractor", names)

    def test_seo_brief_loads_data_provider(self) -> None:
        names = _skill_names(_WORKFLOW_BRIEFS["/seo"][1])
        self.assertIn("seo-data-provider", names)

    def test_commercial_studios_are_tracked(self) -> None:
        from navin.command.builtin import _TRACKED_WORKFLOWS

        for command in ("/campaign", "/seo", "/leads", "/montage"):
            self.assertIn(command, _TRACKED_WORKFLOWS)

    def test_cruise_and_mission_skills_exist_on_disk(self) -> None:
        for command in ("/cruise", "/mission"):
            names = _skill_names(_WORKFLOW_BRIEFS[command][1])
            self.assertIn("mission-ledger", names, command)
            self.assertIn("project-board", names, command)
            self.assertTrue(
                (BUILTIN_SKILLS_DIR / "mission-ledger" / "SKILL.md").is_file()
            )


if __name__ == "__main__":
    unittest.main()
