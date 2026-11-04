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


class SkillMetaPerScanTest(unittest.TestCase):
    """Metadata is kept for the scan it was read in, not for 2 s.

    The 2 s memo expired between two turns: every turn resolved the path of
    every skill again, 2.5 s per prompt build on Windows (2026-11-04 probe).
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.workspace = Path(self._tmp.name)
        skill = self.workspace / ".navin" / "skills" / "kept"
        skill.mkdir(parents=True)
        self.skill_file = skill / "SKILL.md"
        self.skill_file.write_text("---\nname: kept\ndescription: v1\n---\nBody.", encoding="utf-8")
        self._old_home = os.environ.get("HOME")
        os.environ["HOME"] = str(self._tmp.name)
        self.addCleanup(self._restore_home)
        clear_home_skill_dir_cache()

    def _restore_home(self) -> None:
        if self._old_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = self._old_home
        clear_home_skill_dir_cache()

    def test_an_unchanged_catalog_does_not_resolve_paths_again(self) -> None:
        loader = SkillsLoader(self.workspace, builtin_skills_dir=None)
        self.assertEqual(loader.get_skill_metadata("kept")["description"], "v1")
        # Every TTL of the old memos is long past, the catalog is unchanged.
        with mock.patch.object(skills_module.time, "monotonic", return_value=skills_module.time.monotonic() + 600):
            with mock.patch.object(
                loader, "_get_skill_metadata_uncached", side_effect=AssertionError("read again")
            ):
                self.assertEqual(loader.get_skill_metadata("kept")["description"], "v1")

    def test_an_edited_skill_is_read_again_by_the_next_scan(self) -> None:
        loader = SkillsLoader(self.workspace, builtin_skills_dir=None)
        self.assertEqual(loader.get_skill_metadata("kept")["description"], "v1")
        self.skill_file.write_text("---\nname: kept\ndescription: version two\n---\nBody.", encoding="utf-8")
        stat = self.skill_file.stat()
        os.utime(self.skill_file, ns=(stat.st_atime_ns, stat.st_mtime_ns + 5_000_000_000))
        loader.invalidate_scan()
        self.assertEqual(loader.get_skill_metadata("kept")["description"], "version two")


class FrozenAppSkillsStampTest(unittest.TestCase):
    """An installed app stamps the skills it ships once per process.

    Stamping them stats each SKILL.md on every catalog check: 9 s of a 3-turn
    run on Windows (2026-11-04 probe). A source checkout keeps checking them.
    """

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.bundle = root / "bundle"
        self.shipped = self.bundle / "navin" / "skills"
        (self.shipped / "shipped").mkdir(parents=True)
        (self.shipped / "shipped" / "SKILL.md").write_text(
            "---\nname: shipped\ndescription: d\n---\nBody.", encoding="utf-8"
        )
        self.workspace = root / "ws"
        (self.workspace / ".navin" / "skills").mkdir(parents=True)
        (root / "home").mkdir()
        old_home = os.environ.get("HOME")
        os.environ["HOME"] = str(root / "home")

        def restore() -> None:
            if old_home is None:
                os.environ.pop("HOME", None)
            else:
                os.environ["HOME"] = old_home
            clear_home_skill_dir_cache()

        self.addCleanup(restore)
        clear_home_skill_dir_cache()

    def _stamps(self, *, frozen: bool) -> list[str]:
        calls: list[str] = []
        real = skills_module._dir_stamp

        def spy(path):
            calls.append(str(path))
            return real(path)

        with (
            mock.patch.object(skills_module.sys, "frozen", frozen, create=True),
            mock.patch.object(skills_module.sys, "_MEIPASS", str(self.bundle), create=True),
            mock.patch.object(skills_module, "_dir_stamp", side_effect=spy),
        ):
            loader = SkillsLoader(self.workspace, builtin_skills_dir=self.shipped)
            calls.clear()
            skills_module._FROZEN_ROOT_STAMPS.clear()
            loader._roots_stamp()
            loader._roots_stamp()
            skills_module.clear_skills_index_cache()
            loader._roots_stamp()
        return calls

    def test_a_frozen_app_stamps_its_own_skills_once(self) -> None:
        calls = self._stamps(frozen=True)
        shipped = str(self.shipped)
        self.assertEqual(calls.count(shipped), 2, "once, then again after the catalog was cleared")
        self.assertTrue(any(calls.count(path) == 3 for path in set(calls) if path != shipped))

    def test_a_source_checkout_stamps_them_every_time(self) -> None:
        self.assertEqual(self._stamps(frozen=False).count(str(self.shipped)), 3)
