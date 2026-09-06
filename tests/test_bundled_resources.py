"""Cross-workspace tests for bundled Navin templates and skills."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from navin.agent.context import ContextBuilder
from navin.agent.skills import SkillsLoader
from navin.utils.document_templates import document_template_runtime_lines


class BundledResourceTests(unittest.TestCase):
    def test_document_template_is_materialized_inside_active_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as source_tmp, tempfile.TemporaryDirectory() as workspace_tmp:
            source = Path(source_tmp)
            template = source / "ppt" / "demo"
            template.mkdir(parents=True)
            (template / "metadata.json").write_text(
                json.dumps({"title": "Demo"}),
                encoding="utf-8",
            )
            (template / "slide_01.html").write_text("<html>demo</html>", encoding="utf-8")
            workspace = Path(workspace_tmp)

            with patch.dict(
                os.environ,
                {"NAVIN_PRESENTATION_TEMPLATES_DIR": str(source)},
            ):
                lines = document_template_runtime_lines(
                    {"document_template": {"category": "ppt", "name": "demo"}},
                    workspace=workspace,
                )

            copied = (
                workspace
                / ".navin"
                / "resources"
                / "document-templates"
                / "ppt"
                / "demo"
                / "slide_01.html"
            )
            self.assertTrue(copied.is_file())
            self.assertIn(".navin/resources/document-templates/ppt/demo", lines[0])
            self.assertIn("FILL this template", lines[0])
            self.assertIn("START NOW", lines[0])
            self.assertIn("Do not wait for the user to click Build", lines[0])
            self.assertIn("photos/background.jpg", lines[0])
            self.assertIn("Never replace this template", lines[0])
            self.assertNotIn(source_tmp, lines[0])

    def test_builtin_skill_summary_uses_virtual_workspace_path(self) -> None:
        with tempfile.TemporaryDirectory() as workspace_tmp, tempfile.TemporaryDirectory() as skills_tmp:
            skills = Path(skills_tmp)
            skill_dir = skills / "demo-skill"
            skill_dir.mkdir()
            (skill_dir / "SKILL.md").write_text(
                "---\nname: demo-skill\ndescription: Demo skill\n---\n\nInstructions.\n",
                encoding="utf-8",
            )
            loader = SkillsLoader(
                Path(workspace_tmp),
                builtin_skills_dir=skills,
            )

            summary = loader.build_skills_summary()

            self.assertIn("skills/demo-skill/SKILL.md", summary)
            self.assertNotIn(skills_tmp, summary)

    def test_context_uses_skills_from_the_effective_user_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as default_tmp, tempfile.TemporaryDirectory() as active_tmp:
            skill_dir = Path(active_tmp) / "skills" / "project-conventions"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text(
                "---\nname: project-conventions\ndescription: Active project rules\n---\n\nRules.\n",
                encoding="utf-8",
            )
            agent_skill_dir = Path(active_tmp) / ".agents" / "skills" / "design-system"
            agent_skill_dir.mkdir(parents=True)
            (agent_skill_dir / "SKILL.md").write_text(
                "---\nname: design-system\ndescription: Project design rules\n---\n\nRules.\n",
                encoding="utf-8",
            )
            builder = ContextBuilder(Path(default_tmp))

            prompt = builder.build_system_prompt(
                workspace=Path(active_tmp),
                include_memory_recent_history=False,
            )

            # The prompt carries a compact name-only index; descriptions and
            # paths are served on demand by the `skill` tool.
            self.assertIn("project-conventions", prompt)
            self.assertIn("design-system", prompt)
            self.assertIn("skill action=find", prompt)


if __name__ == "__main__":
    unittest.main()
