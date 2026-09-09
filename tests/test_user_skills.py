# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Skills installed once in $HOME must be visible from every workspace.

Reported as "Navin does not see my OMP Skills": the loader only ever looked
inside the workspace, and even there it expected the flat ``<harness>/skills``
layout, never OMP's nested ``<harness>/agent/skills``.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from navin.agent.skills import SkillsLoader, clear_home_skill_dir_cache


def _write_skill(root: Path, name: str, description: str = "d") -> Path:
    skill = root / name
    skill.mkdir(parents=True, exist_ok=True)
    path = skill / "SKILL.md"
    path.write_text(f"---\nname: {name}\ndescription: {description}\n---\nBody.\n")
    return path


class UserSkillsTest(unittest.TestCase):
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

    def _loader(self) -> SkillsLoader:
        return SkillsLoader(self.workspace, builtin_skills_dir=self.workspace / "nope")

    def _names(self) -> list[str]:
        return [skill["name"] for skill in self._loader().list_skills()]

    def test_omp_skills_in_the_home_are_listed(self):
        _write_skill(self.home / ".omp" / "agent" / "skills", "omp-deploy")
        self.assertIn("omp-deploy", self._names())

    def test_the_flat_home_layout_works_too(self):
        _write_skill(self.home / ".claude" / "skills", "claude-review")
        _write_skill(self.home / ".navin" / "skills", "navin-shared")
        names = self._names()
        self.assertIn("claude-review", names)
        self.assertIn("navin-shared", names)

    def test_nested_omp_skills_in_the_project_are_listed(self):
        _write_skill(self.workspace / ".omp" / "agent" / "skills", "omp-local")
        self.assertIn("omp-local", self._names())

    def test_loose_md_in_navin_skill_is_listed(self):
        dest = self.workspace / ".navin" / "skills"
        dest.mkdir(parents=True)
        (dest / "tata.md").write_text("---\nname: tata\ndescription: from navin\n---\nLook.\n")
        self.assertIn("tata", self._names())
        self.assertIn("Look.", self._loader().load_skill("tata") or "")

    def test_a_home_skill_is_tagged_as_user_and_resolvable(self):
        _write_skill(self.home / ".omp" / "agent" / "skills", "omp-deploy")
        loader = self._loader()
        entry = next(s for s in loader.list_skills() if s["name"] == "omp-deploy")
        self.assertEqual(entry["source"], "user")
        self.assertEqual(
            loader.skill_dir("omp-deploy"),
            self.home / ".omp" / "agent" / "skills" / "omp-deploy",
        )

    def test_resolving_every_skill_costs_one_scan_not_one_probe_per_root(self):
        # 40 harness folders in the home = 280 roots. Before the scan cache,
        # each name lookup probed all of them (3 stat() calls each) and a
        # prompt build did that for every skill several times over.
        for index in range(40):
            _write_skill(self.home / f".tool{index}" / "skills", f"tool-skill-{index}")
        _write_skill(self.workspace / ".navin" / "skills", "local-one")
        clear_home_skill_dir_cache()
        loader = self._loader()
        from unittest import mock

        real_stat = os.stat
        calls = {"n": 0}

        def counting_stat(*args, **kwargs):
            calls["n"] += 1
            return real_stat(*args, **kwargs)

        with mock.patch("os.stat", counting_stat):
            names = [entry["name"] for entry in loader.list_skills(filter_unavailable=True)]
            for name in names:
                self.assertIsNotNone(loader.get_skill_metadata(name), name)
            loader.get_always_skills()
            loader.build_skills_index()
        self.assertEqual(len(names), 41)
        # Generous bound: one scan of ~300 roots plus one stat per skill
        # file, well under the ~100 000 the per-root probing needed here.
        self.assertLess(calls["n"], 6_000, calls["n"])

    def test_a_skill_written_after_the_scan_is_still_found_by_name(self):
        loader = self._loader()
        self.assertEqual(loader.list_skills(), [])
        _write_skill(self.workspace / ".navin" / "skills", "late", "late copy")
        # The catalog may lag by the scan TTL; a name lookup never does.
        self.assertIn("late copy", loader.load_skill("late") or "")
        clear_home_skill_dir_cache()  # bumps the scan generation
        self.assertIn("late", [entry["name"] for entry in loader.list_skills()])

    def test_the_workspace_still_wins_over_the_home(self):
        _write_skill(self.home / ".omp" / "agent" / "skills", "deploy", "home copy")
        _write_skill(self.workspace / ".navin" / "skills", "deploy", "project copy")
        loader = self._loader()
        entries = [s for s in loader.list_skills() if s["name"] == "deploy"]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["source"], "workspace")

    def test_any_dot_folder_in_the_home_is_listed(self):
        # Same rule as the workspace: discovery is not a whitelist of OMP /
        # Claude / Cursor. A tool that does not exist yet still works.
        _write_skill(self.home / ".myharness" / "skills", "future-tool")
        _write_skill(self.home / ".weird" / "agent" / "skills", "nested-home")
        _write_skill(self.home / ".other" / "agents" / "skills", "plural-home")
        names = self._names()
        self.assertIn("future-tool", names)
        self.assertIn("nested-home", names)
        self.assertIn("plural-home", names)

    def test_home_cache_and_vcs_folders_are_ignored(self):
        _write_skill(self.home / ".git" / "skills", "from-git")
        _write_skill(self.home / ".cache" / "skills", "from-cache")
        _write_skill(self.home / ".local" / "skills", "from-local")
        names = self._names()
        self.assertNotIn("from-git", names)
        self.assertNotIn("from-cache", names)
        self.assertNotIn("from-local", names)

    def test_any_dot_folder_in_the_project_nested_layout_is_listed(self):
        _write_skill(self.workspace / ".myharness" / "agent" / "skills", "project-nested")
        _write_skill(self.workspace / ".other" / "agents" / "skills", "project-plural")
        names = self._names()
        self.assertIn("project-nested", names)
        self.assertIn("project-plural", names)

    def test_a_missing_home_costs_nothing(self):
        self.assertEqual(self._names(), [])

    def test_singular_skill_folder_is_listed(self):
        _write_skill(self.workspace / ".team" / "skill", "playbook")
        self.assertIn("playbook", self._names())

    def test_owned_skills_are_workspace_user_and_plugin_in_order(self):
        from navin.agent.skills import is_owned_skill_source

        _write_skill(self.workspace / ".navin" / "skills", "team-brief")
        _write_skill(self.home / ".navin" / "skills", "home-note")
        loader = self._loader()
        names = [entry["name"] for entry in loader.owned_skills()]
        self.assertEqual(names, ["team-brief", "home-note"])
        self.assertTrue(is_owned_skill_source("workspace"))
        self.assertTrue(is_owned_skill_source("plugin:demo"))
        self.assertFalse(is_owned_skill_source("builtin"))

    def test_mentioned_skill_names_keep_message_order(self):
        _write_skill(self.workspace / ".navin" / "skills", "alpha")
        _write_skill(self.workspace / ".navin" / "skills", "beta")
        loader = self._loader()
        self.assertEqual(
            loader.mentioned_skill_names("use $beta then $alpha and $beta again"),
            ["beta", "alpha"],
        )


class OwnedSkillPromptTest(unittest.TestCase):
    def test_system_prompt_lists_and_loads_owned_skills_in_order(self):
        from navin.agent.context import ContextBuilder
        from navin.agent.skills import clear_skills_index_cache

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "project"
            empty_builtins = Path(tmp) / "no-builtins"
            workspace.mkdir()
            empty_builtins.mkdir()
            first = workspace / ".navin" / "skills" / "first-playbook"
            second = workspace / ".navin" / "skills" / "second-playbook"
            first.mkdir(parents=True)
            second.mkdir(parents=True)
            (first / "SKILL.md").write_text(
                "---\nname: first-playbook\ndescription: first\n---\nDo first.\n",
                encoding="utf-8",
            )
            (second / "SKILL.md").write_text(
                "---\nname: second-playbook\ndescription: second\n---\nDo second.\n",
                encoding="utf-8",
            )
            clear_skills_index_cache()
            builder = ContextBuilder(workspace=workspace)
            builder.skills = builder.skills.__class__(
                workspace,
                builtin_skills_dir=empty_builtins,
            )
            prompt = builder.build_system_prompt(include_memory_recent_history=False)
            self.assertIn("# Active Skills", prompt)
            self.assertIn("1. first-playbook", prompt)
            self.assertIn("2. second-playbook", prompt)
            self.assertLess(prompt.index("1. first-playbook"), prompt.index("2. second-playbook"))
            # Names only by default: bodies are ~9k tokens of prompt that the
            # turn pays for on every step, and prompt size is wall-clock.
            self.assertNotIn("### Skill: first-playbook", prompt)
            self.assertNotIn("Do first.", prompt)
            self.assertIn("skill action=read", prompt)
            # A named $skill is an explicit request, so its body still loads.
            mention = builder.build_system_prompt(
                include_memory_recent_history=False,
                current_message="please follow $second-playbook",
            )
            self.assertIn("### Skill: second-playbook", mention)
            self.assertIn("Do second.", mention)

    def test_slim_preload_does_not_dump_owned_skill_bodies(self):
        from navin.agent.context import ContextBuilder
        from navin.agent.skills import clear_skills_index_cache

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "project"
            empty_builtins = Path(tmp) / "no-builtins"
            workspace.mkdir()
            empty_builtins.mkdir()
            skill = workspace / ".navin" / "skills" / "first-playbook"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text(
                "---\nname: first-playbook\ndescription: first\n---\nDo first.\n",
                encoding="utf-8",
            )
            clear_skills_index_cache()
            builder = ContextBuilder(workspace=workspace)
            builder.skills = builder.skills.__class__(
                workspace,
                builtin_skills_dir=empty_builtins,
            )
            prompt = builder.build_system_prompt(
                include_memory_recent_history=False,
                slim_skill_preload=True,
            )
            self.assertIn("# Active Skills", prompt)
            self.assertIn("1. first-playbook", prompt)
            self.assertIn("on demand", prompt.lower())
            self.assertIn("`skill action=read", prompt)
            self.assertNotIn("### Skill: first-playbook", prompt)
            self.assertNotIn("Slim preload", prompt)
            self.assertNotIn("Do first.", prompt)

    def test_slim_code_skills_are_names_until_read(self):
        from navin.agent.context import ContextBuilder

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "AGENTS.md").write_text("# Agents\n", encoding="utf-8")
            builder = ContextBuilder(root)
            prompt = builder.build_system_prompt(
                skill_names=["fullstack-dev", "ui-ux-pro-max"],
                slim_skill_preload=True,
                include_memory_recent_history=False,
            )
            self.assertIn("Suggested for this turn:", prompt)
            self.assertIn("fullstack-dev", prompt)
            self.assertIn("ui-ux-pro-max", prompt)
            self.assertIn("`skill action=read", prompt)
            self.assertNotIn("Operating loop", prompt)
            self.assertNotIn("Hard defaults", prompt)
            self.assertNotIn("Capsule", prompt)
            self.assertNotIn("## Scaffolding recipes", prompt)
            self.assertIn("# Tool Usage Notes", prompt)
            self.assertNotIn("## Semantic Code Navigation", prompt)

    def test_slim_loads_only_a_mentioned_skill_body(self):
        from navin.agent.context import ContextBuilder
        from navin.agent.skills import clear_skills_index_cache

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "project"
            empty_builtins = Path(tmp) / "no-builtins"
            workspace.mkdir()
            empty_builtins.mkdir()
            skill = workspace / ".navin" / "skills" / "first-playbook"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text(
                "---\nname: first-playbook\ndescription: first\n---\nDo first.\n",
                encoding="utf-8",
            )
            clear_skills_index_cache()
            builder = ContextBuilder(workspace=workspace)
            builder.skills = builder.skills.__class__(
                workspace,
                builtin_skills_dir=empty_builtins,
            )
            prompt = builder.build_system_prompt(
                include_memory_recent_history=False,
                slim_skill_preload=True,
                skill_names=["fullstack-dev"],
                current_message="please follow $first-playbook",
            )
            self.assertIn("Do first.", prompt)
            self.assertIn("### Skill: first-playbook", prompt)
            self.assertNotIn("### Skill: fullstack-dev", prompt)
            self.assertIn("fullstack-dev", prompt)


class WorkspaceSkillImportTest(unittest.TestCase):
    def test_discovers_and_imports_dot_folder_skill_trees(self):
        from navin.webui.skills_api import discover_workspace_skills, import_workspace_skills

        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "repo"
            catalog = Path(tmp) / "catalog"
            project.mkdir()
            catalog.mkdir()
            _write_skill(project / ".cursor" / "skill", "review-notes")
            _write_skill(project / ".team" / "skills", "ship-check")
            payload = discover_workspace_skills(project, catalog_workspace=catalog)
            names = [row["name"] for row in payload["skills"]]
            self.assertEqual(names, ["review-notes", "ship-check"])
            self.assertEqual(payload["skills"][0]["origin"], ".cursor/skill/review-notes")
            self.assertFalse(payload["skills"][0]["already"])
            self.assertIn("Body.", payload["skills"][0]["markdown"])
            self.assertIn("name: review-notes", payload["skills"][0]["markdown"])

            imported = import_workspace_skills(
                catalog,
                scan_root=project,
                names=["review-notes"],
            )
            self.assertEqual(imported["imported"], ["review-notes"])
            dest = project / ".navin" / "skills" / "review-notes" / "SKILL.md"
            self.assertTrue(dest.is_file())
            self.assertFalse((catalog / ".navin" / "skills" / "review-notes" / "SKILL.md").exists())
            self.assertEqual(imported["previews"][0]["name"], "review-notes")
            self.assertIn("Body.", imported["previews"][0]["markdown"])

            again = discover_workspace_skills(project, catalog_workspace=catalog, apply_root=project)
            review = next(row for row in again["skills"] if row["name"] == "review-notes")
            self.assertTrue(review["already"])

    def test_discovers_loose_md_and_every_dot_folder(self):
        from navin.webui.skills_api import discover_workspace_skills

        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "repo"
            catalog = Path(tmp) / "catalog"
            project.mkdir()
            catalog.mkdir()
            (project / ".navin" / "skills").mkdir(parents=True)
            (project / ".navin" / "skills" / "tata.md").write_text(
                "---\nname: tata\ndescription: from navin\n---\nLook.\n"
            )
            _write_skill(project / ".cursor" / "skill", "cursor-review")
            _write_skill(project / ".claude" / "skills", "claude-note")
            _write_skill(project / ".codex" / "skills", "codex-check")
            _write_skill(project / ".team" / "skill", "team-ship")
            (project / ".wind" / "review").mkdir(parents=True)
            (project / ".wind" / "SKILL.md").write_text(
                "---\nname: wind\ndescription: root\n---\nRoot.\n"
            )
            (project / ".wind" / "review" / "SKILL.md").write_text(
                "---\nname: wind-review\ndescription: sub\n---\nSub.\n"
            )
            (project / ".navin" / "SKILL.md").write_text(
                "---\nname: navin\ndescription: root navin\n---\nNavin root.\n"
            )
            # Brain files and harness configuration are loose Markdown at the
            # root of a dot folder; they are not playbooks.
            for brain in ("AGENTS.md", "SOUL.md", "USER.md", "HEARTBEAT.md", "WORKSPACE.md"):
                (project / ".navin" / brain).write_text(f"# {brain}\n", encoding="utf-8")
            (project / ".claude" / "CLAUDE.md").write_text("# project notes\n", encoding="utf-8")
            (project / ".qwen").mkdir()
            (project / ".qwen" / "output-language.md").write_text("French\n", encoding="utf-8")
            payload = discover_workspace_skills(project, catalog_workspace=catalog, apply_root=project)
            names = {row["name"] for row in payload["skills"]}
            self.assertEqual(
                names,
                {
                    "tata",
                    "cursor-review",
                    "claude-note",
                    "codex-check",
                    "team-ship",
                    "wind",
                    "wind-review",
                    "navin",
                },
            )
            tata = next(row for row in payload["skills"] if row["name"] == "tata")
            self.assertTrue(tata["already"])
            self.assertEqual(tata["origin"], ".navin/skills/tata.md")
            self.assertFalse(
                next(row for row in payload["skills"] if row["name"] == "cursor-review")["already"]
            )

            loader = SkillsLoader(project, builtin_skills_dir=project / "none")
            listed = {entry["name"] for entry in loader.list_skills()}
            self.assertIn("tata", listed)
            self.assertIn("cursor-review", listed)
            self.assertIn("claude-note", listed)
            self.assertEqual(loader.load_skill("tata"), tata["markdown"])

    def test_import_everywhere_writes_to_home_navin_skill(self):
        from navin.webui.skills_api import import_workspace_skills

        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "repo"
            catalog = Path(tmp) / "catalog"
            home = Path(tmp) / "home"
            project.mkdir()
            catalog.mkdir()
            home.mkdir()
            _write_skill(project / ".cursor" / "skill", "shared-note")
            imported = import_workspace_skills(
                catalog,
                scan_root=project,
                names=["shared-note"],
                apply_root=home,
            )
            self.assertEqual(imported["imported"], ["shared-note"])
            self.assertTrue((home / ".navin" / "skills" / "shared-note" / "SKILL.md").is_file())
            self.assertFalse((project / ".navin" / "skills" / "shared-note" / "SKILL.md").exists())

    def test_new_workspace_skill_dir_is_navin_skill(self):
        from navin import workspace_layout

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            dest = workspace_layout.workspace_skill_dir(root, "fresh")
            self.assertEqual(dest, root / ".navin" / "skills" / "fresh")
            leftover = root / ".navin" / "skill" / "old-one"
            leftover.mkdir(parents=True)
            (leftover / "SKILL.md").write_text("---\nname: old-one\n---\nX.\n")
            merged = workspace_layout.workspace_skill_dir(root, "old-one")
            self.assertEqual(merged, root / ".navin" / "skills" / "old-one")
            self.assertFalse((root / ".navin" / "skill").exists())


class HostPathNormalizeTest(unittest.TestCase):
    def test_wsl_translates_a_windows_drive(self):
        from unittest.mock import patch

        from navin.utils.host import normalize_host_path

        with patch("navin.utils.host.host_platform", return_value="wsl"):
            path = normalize_host_path(r"C:\Users\me\proj")
        self.assertEqual(path, Path("/mnt/c/Users/me/proj"))

    def test_wsl_translates_unc(self):
        from unittest.mock import patch

        from navin.utils.host import normalize_host_path

        with patch("navin.utils.host.host_platform", return_value="wsl"):
            path = normalize_host_path(r"\\wsl.localhost\Ubuntu\home\me\app")
        self.assertEqual(path, Path("/home/me/app"))

    def test_linux_keeps_posix_and_accepts_backslashes(self):
        from unittest.mock import patch

        from navin.utils.host import normalize_host_path

        with patch("navin.utils.host.host_platform", return_value="linux"):
            path = normalize_host_path("/tmp/my\\project")
        self.assertEqual(path, Path("/tmp/my/project"))

    def test_apply_root_everywhere_is_home(self):
        from navin.webui.skills_api import resolve_skill_apply_root

        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "repo"
            project.mkdir()
            root = resolve_skill_apply_root(
                scope="everywhere",
                workspace=project,
                fallback=project,
            )
            self.assertEqual(root, Path.home())


if __name__ == "__main__":
    unittest.main()
