# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""The skill metadata cache must be bounded (audit L3).

_SKILL_META_CACHE is module-global, keyed by absolute path. Deleted or
renamed skills used to keep entries forever and every workspace added
more; a FIFO bound now caps it.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import navin.agent.skills as skills_module
from navin.agent.skills import SkillsLoader, clear_home_skill_dir_cache


class SkillMetaCacheBoundTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.workspace = Path(self._tmp.name)
        (self.workspace / ".navin" / "skills").mkdir(parents=True)
        self._old_home = os.environ.get("HOME")
        os.environ["HOME"] = str(self._tmp.name)
        clear_home_skill_dir_cache()
        skills_module._SKILL_META_CACHE.clear()

    def tearDown(self) -> None:
        if self._old_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = self._old_home
        clear_home_skill_dir_cache()
        skills_module._SKILL_META_CACHE.clear()

    def _skill(self, name: str) -> None:
        skill = self.workspace / ".navin" / "skills" / name
        skill.mkdir()
        (skill / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: d\n---\nBody.", encoding="utf-8"
        )

    def test_cache_never_exceeds_the_bound(self) -> None:
        with mock.patch.object(skills_module, "_SKILL_META_CACHE_MAX", 8):
            loader = SkillsLoader(self.workspace, builtin_skills_dir=None)
            for index in range(20):
                self._skill(f"skill-{index}")
                self.assertIsNotNone(loader.get_skill_metadata(f"skill-{index}"))
            self.assertLessEqual(len(skills_module._SKILL_META_CACHE), 8)

    def test_refreshed_skill_stays_readable_after_eviction(self) -> None:
        with mock.patch.object(skills_module, "_SKILL_META_CACHE_MAX", 4):
            loader = SkillsLoader(self.workspace, builtin_skills_dir=None)
            self._skill("kept")
            loader.get_skill_metadata("kept")
            for index in range(10):
                self._skill(f"filler-{index}")
                loader.get_skill_metadata(f"filler-{index}")
            # Re-read "kept" (now stale by mtime): the cache must recover.
            (self.workspace / ".navin" / "skills" / "kept" / "SKILL.md").write_text(
                "---\nname: kept\ndescription: v2\n---\nBody.", encoding="utf-8"
            )
            self.assertIsNotNone(loader.get_skill_metadata("kept"))


if __name__ == "__main__":
    unittest.main()
