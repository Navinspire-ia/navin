# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Origin labels and the trust gate for workspace-discovered skills.

Audit M2: the loader scans every dot-folder in the workspace and $HOME, so
a cloned repo ships .anything/SKILL.md that gets injected ahead of
builtins. The fix: stamp injected bodies with their origin, and optionally
stop reading harness dot-folders from the workspace (agents.defaults.
trust_workspace_harness_skills = false keeps only .navin and plain folders).
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from navin.agent.skills import (
    SkillsLoader,
    clear_home_skill_dir_cache,
    skill_source_label,
)


class SkillOriginLabelsTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        self.home = base / "home"
        self.workspace = base / "project"
        self.home.mkdir()
        self.workspace.mkdir()
        self._old_home = os.environ.get("HOME")
        os.environ["HOME"] = str(self.home)
        clear_home_skill_dir_cache()

    def tearDown(self) -> None:
        if self._old_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = self._old_home
        self._tmp.cleanup()

    def _write(self, root: Path, name: str, body: str = "Body.\n") -> None:
        skill = root / name
        skill.mkdir(parents=True, exist_ok=True)
        (skill / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: d\n---\n{body}", encoding="utf-8"
        )

    def test_source_is_reported_for_every_library(self) -> None:
        self._write(self.workspace / ".claude" / "skills", "repo-shipped")
        self._write(self.home / ".omp" / "agent" / "skills", "home-skill")
        self._write(self.workspace / ".navin" / "skills", "owned")
        loader = SkillsLoader(self.workspace, builtin_skills_dir=self.workspace / "nope")
        self.assertEqual(loader.skill_source("repo-shipped"), "workspace")
        self.assertEqual(loader.skill_source("home-skill"), "user")
        self.assertEqual(loader.skill_source("owned"), "workspace")

    def test_injected_bodies_carry_the_origin(self) -> None:
        self._write(self.workspace / ".claude" / "skills", "repo-shipped")
        loader = SkillsLoader(self.workspace, builtin_skills_dir=self.workspace / "nope")
        body = loader.load_skills_for_context(["repo-shipped"])
        self.assertIn("(Origin: workspace of this project", body)

    def test_labels_cover_every_source(self) -> None:
        self.assertIn("plugin pack 'x'", skill_source_label("plugin:x"))
        self.assertIn("built into navin", skill_source_label("builtin"))
        self.assertIn("user home", skill_source_label("user"))
        self.assertIn("unknown", skill_source_label("mystery"))

    def test_skill_tool_read_stamps_the_origin(self) -> None:
        import asyncio

        from navin.agent.tools.skill_catalog import SkillCatalogTool

        self._write(self.workspace / ".claude" / "skills", "repo-shipped")
        tool = SkillCatalogTool(workspace=self.workspace)
        result = asyncio.run(tool.execute(action="read", name="repo-shipped"))
        self.assertIn("(Origin: workspace of this project", result)


class WorkspaceHarnessTrustGateTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        self.home = base / "home"
        self.workspace = base / "project"
        self.home.mkdir()
        self.workspace.mkdir()
        self._old_home = os.environ.get("HOME")
        os.environ["HOME"] = str(self.home)
        clear_home_skill_dir_cache()

    def tearDown(self) -> None:
        if self._old_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = self._old_home
        self._tmp.cleanup()

    def _write(self, root: Path, name: str) -> None:
        skill = root / name
        skill.mkdir(parents=True, exist_ok=True)
        (skill / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: d\n---\nBody.", encoding="utf-8"
        )

    def test_gate_off_skips_workspace_dot_folders_but_keeps_navin(self) -> None:
        self._write(self.workspace / ".claude" / "skills", "repo-shipped")
        self._write(self.workspace / ".cursor" / "skills", "repo-shipped-2")
        self._write(self.workspace / ".navin" / "skills", "owned")
        self._write(self.workspace / "skills", "plain-folder")
        self._write(self.home / ".claude" / "skills", "home-skill")
        gate_off = SkillsLoader(
            self.workspace,
            builtin_skills_dir=self.workspace / "nope",
            trust_workspace_harness_skills=False,
        )
        gate_on = SkillsLoader(
            self.workspace,
            builtin_skills_dir=self.workspace / "nope",
        )
        self.assertIn("repo-shipped", [s["name"] for s in gate_on.list_skills()])
        off_names = [s["name"] for s in gate_off.list_skills()]
        self.assertNotIn("repo-shipped", off_names)
        self.assertNotIn("repo-shipped-2", off_names)
        self.assertIn("owned", off_names)
        self.assertIn("plain-folder", off_names)
        self.assertIn("home-skill", off_names)
        self.assertIsNone(gate_off.load_skill("repo-shipped"))

    def test_defaults_keep_current_behavior(self) -> None:
        self._write(self.workspace / ".claude" / "skills", "repo-shipped")
        loader = SkillsLoader(self.workspace, builtin_skills_dir=self.workspace / "nope")
        self.assertTrue(loader.trust_workspace_harness_skills)
        self.assertIn("repo-shipped", [s["name"] for s in loader.list_skills()])

    def test_config_field_defaults_to_true(self) -> None:
        from navin.config.schema import AgentDefaults

        defaults = AgentDefaults()
        self.assertTrue(defaults.trust_workspace_harness_skills)


if __name__ == "__main__":
    unittest.main()
