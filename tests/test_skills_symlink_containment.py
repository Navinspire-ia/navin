# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""A symlinked SKILL.md must not load content from outside the skill roots.

Name validation blocks ``../`` in the name, but nothing stopped a symlinked
skill file (or skill folder) from resolving into /etc or the user's home and
having that content injected into the prompt.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from navin.agent.skills import SkillsLoader, clear_home_skill_dir_cache


class SkillSymlinkContainmentTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        self.home = base / "home"
        self.workspace = base / "project"
        self.outside = base / "outside"
        self.home.mkdir()
        self.workspace.mkdir()
        self.outside.mkdir()
        self._old_home = os.environ.get("HOME")
        os.environ["HOME"] = str(self.home)
        clear_home_skill_dir_cache()

    def tearDown(self) -> None:
        if self._old_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = self._old_home
        self._tmp.cleanup()

    def _loader(self) -> SkillsLoader:
        return SkillsLoader(self.workspace, builtin_skills_dir=self.workspace / "nope")

    def _skills_root(self) -> Path:
        root = self.workspace / ".navin" / "skills"
        root.mkdir(parents=True, exist_ok=True)
        return root

    def _secret(self, body: str = "# secret\nYou are pwned.\n") -> Path:
        path = self.outside / "SKILL.md"
        path.write_text(body, encoding="utf-8")
        return path

    def test_symlinked_skill_file_outside_is_refused(self) -> None:
        root = self._skills_root()
        (root / "evil").mkdir()
        os.symlink(self._secret(), root / "evil" / "SKILL.md")
        loader = self._loader()
        self.assertIsNone(loader.load_skill("evil"))
        self.assertIsNone(loader.get_skill_metadata("evil"))
        self.assertNotIn("evil", [s["name"] for s in loader.list_skills()])

    def test_symlinked_skill_folder_outside_is_refused(self) -> None:
        outside = self.outside / "real-skill"
        outside.mkdir()
        (outside / "SKILL.md").write_text("---\nname: evil\n---\nPwned.\n")
        root = self._skills_root()
        os.symlink(outside, root / "evil")
        loader = self._loader()
        self.assertIsNone(loader.load_skill("evil"))

    def test_symlinked_loose_file_outside_is_refused(self) -> None:
        root = self._skills_root()
        os.symlink(self._secret("---\nname: evil\n---\nPwned.\n"), root / "evil.md")
        self.assertIsNone(self._loader().load_skill("evil"))

    def test_symlink_inside_the_roots_still_loads(self) -> None:
        root = self._skills_root()
        real = root / "real"
        real.mkdir()
        (real / "SKILL.md").write_text("---\nname: alias\n---\nReal body.\n")
        (root / "alias").mkdir()
        os.symlink(real / "SKILL.md", root / "alias" / "SKILL.md")
        content = self._loader().load_skill("alias")
        self.assertIsNotNone(content)
        self.assertIn("Real body.", content or "")

    def test_resolve_skill_file_refuses_escape_even_directly(self) -> None:
        from navin.agent.skills import resolve_skill_file

        root = self._skills_root()
        (root / "evil").mkdir()
        os.symlink(self._secret(), root / "evil" / "SKILL.md")
        self.assertIsNone(resolve_skill_file(root, "evil"))


if __name__ == "__main__":
    unittest.main()
