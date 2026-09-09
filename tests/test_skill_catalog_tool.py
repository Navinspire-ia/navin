# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""The prompt lists skill names only; the `skill` tool serves the rest.

The full catalog of descriptions cost ~10K tokens on every turn. These tests
pin the contract that replaced it: a compact name index in the prompt, and
find/read/list served on demand.
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from navin.agent.skills import SkillsLoader
from navin.agent.tools.skill_catalog import SkillCatalogTool


def _write_skill(root: Path, name: str, description: str, body: str = "Do the thing.") -> None:
    skill_dir = root / "skills" / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n{body}\n",
        encoding="utf-8",
    )


class CompactIndexTest(unittest.TestCase):
    def test_index_lists_names_without_descriptions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_skill(root, "pdf-report", "Build polished PDF reports with charts")
            _write_skill(root, "deploy-docker", "Ship apps as Docker containers")
            loader = SkillsLoader(root, builtin_skills_dir=root / "nope")

            index = loader.build_skills_index()

            self.assertIn("pdf-report", index)
            self.assertIn("deploy-docker", index)
            self.assertNotIn("polished PDF reports", index)

    def test_index_is_an_order_of_magnitude_smaller_than_the_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for i in range(30):
                _write_skill(
                    root,
                    f"skill-{i:02d}",
                    "A long description sentence that repeats on every turn "
                    "and costs tokens the model rarely needs " * 2,
                )
            loader = SkillsLoader(root, builtin_skills_dir=root / "nope")

            summary = loader.build_skills_summary()
            index = loader.build_skills_index()

            self.assertLess(len(index), len(summary) / 5)

    def test_a_skill_added_after_the_first_index_is_visible(self) -> None:
        """The spawn-wave cache must not hide a skill written a moment later."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_skill(root, "pdf-report", "Build PDF reports")
            loader = SkillsLoader(root, builtin_skills_dir=root / "nope")
            first = loader.build_skills_index()
            self.assertIn("pdf-report", first)
            self.assertNotIn("deploy-docker", first)

            _write_skill(root, "deploy-docker", "Ship apps as Docker containers")
            second = SkillsLoader(root, builtin_skills_dir=root / "nope").build_skills_index()
            self.assertIn("pdf-report", second)
            self.assertIn("deploy-docker", second)


class SearchSkillsTest(unittest.TestCase):
    def test_keywords_match_name_and_description(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_skill(root, "pdf-report", "Build polished PDF reports with charts")
            _write_skill(root, "deploy-docker", "Ship apps as Docker containers")
            loader = SkillsLoader(root, builtin_skills_dir=root / "nope")

            rows = loader.search_skills("pdf charts")

            self.assertEqual(rows[0]["name"], "pdf-report")

    def test_no_tokens_means_no_results(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_skill(root, "pdf-report", "Build PDF reports")
            loader = SkillsLoader(root, builtin_skills_dir=root / "nope")
            self.assertEqual(loader.search_skills("   "), [])


class SkillToolTest(unittest.TestCase):
    def _tool(self, root: Path) -> SkillCatalogTool:
        # Point builtins at an empty dir so only the test's skills exist.
        return SkillCatalogTool(workspace=root, builtin_skills_dir=root / "no-builtins")

    def test_find_returns_descriptions_and_next_step(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_skill(root, "pdf-report", "Build polished PDF reports with charts")
            result = asyncio.run(self._tool(root).execute(action="find", query="pdf report"))
            self.assertIn("pdf-report", result)
            self.assertIn("polished PDF reports", result)
            self.assertIn("action=read", result)

    def test_find_without_query_is_a_clear_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = asyncio.run(self._tool(Path(tmp)).execute(action="find", query=""))
            self.assertIn("query", str(result))

    def test_read_returns_the_full_body(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_skill(root, "pdf-report", "Build PDF reports", body="Step 1: gather data.")
            result = asyncio.run(self._tool(root).execute(action="read", name="pdf-report"))
            self.assertIn("Step 1: gather data.", result)

    def test_read_names_the_skill_folder_for_bundled_scripts(self) -> None:
        # Skills ship helper files (scripts/, data/); without the absolute
        # folder path the model hunts for them with imports or a global find.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_skill(root, "pdf-report", "Build PDF reports")
            result = asyncio.run(self._tool(root).execute(action="read", name="pdf-report"))
            self.assertIn("Skill folder:", result)
            # Paths are shown with forward slashes on every OS (a Windows
            # backslash path echoed into commands reads as escapes).
            self.assertIn((root / "skills" / "pdf-report").as_posix(), result)

    def test_preloaded_full_body_names_the_skill_folder_too(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_skill(root, "pdf-report", "Build PDF reports")
            loader = SkillsLoader(root, builtin_skills_dir=root / "no-builtins")
            content = loader.load_skills_for_context(["pdf-report"])
            self.assertIn("(Skill folder:", content)
            self.assertIn((root / "skills" / "pdf-report").as_posix(), content)

    def test_read_unknown_name_suggests_closest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_skill(root, "pdf-report", "Build PDF reports")
            result = asyncio.run(self._tool(root).execute(action="read", name="pdf-reporting"))
            self.assertIn("pdf-report", str(result))

    def test_list_returns_every_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_skill(root, "alpha", "First")
            _write_skill(root, "beta", "Second")
            result = asyncio.run(self._tool(root).execute(action="list"))
            self.assertIn("alpha", result)
            self.assertIn("beta", result)


if __name__ == "__main__":
    unittest.main()
