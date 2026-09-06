"""Skill setup metadata: GitHub CLI install recipes."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

_SKILL = (
    Path(__file__).resolve().parents[1] / "navin" / "skills" / "github" / "SKILL.md"
)


class GithubSkillPacmanRecipeTest(unittest.TestCase):
    def test_github_skill_has_pacman_github_cli(self):
        text = _SKILL.read_text(encoding="utf-8")
        front = text.split("---", 2)[1]
        meta_line = next(
            line for line in front.splitlines() if line.startswith("metadata:")
        )
        raw = meta_line.split(":", 1)[1].strip()
        metadata = json.loads(raw)
        install = metadata["navin"]["install"]
        pacman = [
            opt
            for opt in install
            if opt.get("kind") == "pacman" and opt.get("package") == "github-cli"
        ]
        self.assertTrue(pacman, "github SKILL.md missing pacman github-cli install")


if __name__ == "__main__":
    unittest.main()
