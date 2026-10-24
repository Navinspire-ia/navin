# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Dream edits to the owned skills library must be tracked and diffed.

A Dream run can rewrite any SKILL.md, and those bodies are injected into
future prompts: an untracked edit would be an unaudited prompt change.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from navin.agent.memory import MemoryStore
from navin.utils.gitstore import GitStore


def _isolated_tmp() -> tempfile.TemporaryDirectory:
    """A temp dir with no enclosing git repo: GitStore.init refuses to nest
    inside one, and a stray /tmp/.git on dev hosts made these tests fail."""
    for base in (tempfile.gettempdir(), "/var/tmp", "/dev/shm"):
        root = Path(base).resolve()
        if root.is_dir() and not any((d / ".git").exists() for d in (root, *root.parents)):
            return tempfile.TemporaryDirectory(dir=root)
    return tempfile.TemporaryDirectory()


class GitStoreTrackedDirectoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = _isolated_tmp()
        self.addCleanup(self._tmp.cleanup)
        self.workspace = Path(self._tmp.name).resolve()
        self.store = GitStore(
            self.workspace,
            tracked_files=[".navin/SOUL.md", ".navin/skills/"],
        )
        self.assertTrue(self.store.init())

    def _skill(self, body: str) -> None:
        skill = self.workspace / ".navin" / "skills" / "demo" / "SKILL.md"
        skill.parent.mkdir(parents=True, exist_ok=True)
        skill.write_text(body, encoding="utf-8")

    def test_init_commits_files_under_tracked_directory(self) -> None:
        self._skill("# demo\n")
        sha = self.store.auto_commit("dream: seed")
        self.assertIsNotNone(sha)

    def test_skill_edit_surfaces_in_working_tree_summary(self) -> None:
        self._skill("# demo\n")
        self.store.auto_commit("dream: seed")
        self._skill("# demo v2\n")
        summary = self.store.summarize_working_tree([".navin/skills/"])
        self.assertIn(".navin/skills/demo/SKILL.md: +1 -1", summary)
        self.assertIn("demo v2", summary)

    def test_auto_commit_stages_new_skill_files(self) -> None:
        self.store.auto_commit("dream: seed")
        self._skill("# new skill\n")
        sha = self.store.auto_commit("dream: add skill")
        self.assertIsNotNone(sha)
        # After the commit the working tree is clean again.
        self.assertEqual(self.store.summarize_working_tree([".navin/skills/"]), "")

    def test_auto_commit_never_walks_the_rest_of_the_workspace(self) -> None:
        # The gateway froze ~37s on every Dream commit: the untracked scan
        # recursed node_modules. Huge untracked trees must not be listed.
        from unittest import mock

        from dulwich import porcelain

        self.store.auto_commit("dream: seed")
        seen: list[str] = []
        real = porcelain.status

        def spy(repo, *args, **kwargs):
            seen.append(kwargs.get("untracked_files", "normal"))
            return real(repo, *args, **kwargs)

        with mock.patch.object(porcelain, "status", spy):
            self.assertIsNone(self.store.auto_commit("dream: nothing"))
            self._skill("# fresh\n")
            self.assertIsNotNone(self.store.auto_commit("dream: fresh"))
        self.assertEqual(seen, ["no", "no"])

    def test_deleted_skill_file_is_staged_not_dirtied_forever(self) -> None:
        self._skill("# demo\n")
        self.store.auto_commit("dream: seed")
        (self.workspace / ".navin" / "skills" / "demo" / "SKILL.md").unlink()
        sha = self.store.auto_commit("dream: remove skill")
        self.assertIsNotNone(sha)
        self.assertEqual(self.store.summarize_working_tree([".navin/skills/"]), "")


class MemoryStoreSkillsTrackingTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = _isolated_tmp()
        self.addCleanup(self._tmp.cleanup)
        self.workspace = Path(self._tmp.name).resolve()

    def test_owned_skills_directory_is_tracked(self) -> None:
        store = MemoryStore(self.workspace)
        self.assertEqual(store._skills_prefix, ".navin/skills/")
        self.assertIn(".navin/skills/", store.git._tracked_dirs)

    def test_dream_content_diff_includes_skill_edits(self) -> None:
        store = MemoryStore(self.workspace)
        store.git.init()
        skill = self.workspace / ".navin" / "skills" / "demo" / "SKILL.md"
        skill.parent.mkdir(parents=True, exist_ok=True)
        skill.write_text("# demo\n", encoding="utf-8")
        store.git.auto_commit("dream: seed")
        skill.write_text("# demo v2\n", encoding="utf-8")
        diff = store.dream_content_diff()
        self.assertIn(".navin/skills/demo/SKILL.md", diff)


if __name__ == "__main__":
    unittest.main()
