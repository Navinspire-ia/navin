"""Project config discovery: subagents, skills mirrors, env vars, @imports.

Covers the real-world layout repos use to configure several coding tools at
once from one committed source:

    .ai/agents/<category>/<name>.md    canonical personas (nested)
    .ai/skills/<name>/SKILL.md         canonical skills
    .agents/skills/<name>              symlink mirrors (cross-tool path)
    .claude/agents/<name>.md           symlink mirrors
    .codex/agents/<name>.toml          generated Codex TOML agents
    .opencode/agent/<name>.md          OpenCode's singular folder
    CLAUDE.md with an @AGENTS.md line  Claude Code import shim
"""

import os
from pathlib import Path

import pytest

from navin.agent.context import ContextBuilder
from navin.agent.project_agents import (
    HARNESS_DIRS,
    find_project_agent,
    harness_dirs,
    home_harness_dirs,
    list_project_agents,
)
from navin.agent.skills import SkillsLoader


def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _agent_md(name: str, description: str = "desc") -> str:
    return f"---\nname: {name}\ndescription: {description}\n---\nInstructions for {name}.\n"


class TestAgentDiscovery:
    def test_flat_markdown_agent_is_discovered(self, tmp_path):
        _write(tmp_path / ".claude/agents/reviewer.md", _agent_md("reviewer"))
        agents = list_project_agents(tmp_path)
        assert [a.name for a in agents] == ["reviewer"]

    def test_nested_category_layout_is_discovered(self, tmp_path):
        # Canonical .ai/agents/<category>/<name>.md layouts must work as-is.
        _write(tmp_path / ".ai/agents/quality/security-auditor.md", _agent_md("security-auditor"))
        _write(tmp_path / ".ai/agents/delivery/release-captain.md", _agent_md("release-captain"))
        names = {a.name for a in list_project_agents(tmp_path)}
        assert names == {"security-auditor", "release-captain"}

    def test_opencode_singular_agent_folder(self, tmp_path):
        _write(tmp_path / ".opencode/agent/planner.md", _agent_md("planner"))
        assert find_project_agent(tmp_path, "planner") is not None

    def test_codex_toml_agent(self, tmp_path):
        _write(
            tmp_path / ".codex/agents/refactorer.toml",
            'name = "refactorer"\n'
            'description = "Refactors safely"\n'
            "developer_instructions = '''\nAlways preserve behavior.\n'''\n",
        )
        agent = find_project_agent(tmp_path, "refactorer")
        assert agent is not None
        assert agent.description == "Refactors safely"
        assert "preserve behavior" in agent.prompt

    def test_readme_is_not_an_agent(self, tmp_path):
        # The .navin scaffold ships .navin/agents/README.md as documentation;
        # it must never surface as a spawnable "README" agent.
        _write(tmp_path / ".navin/agents/README.md", "# Project agents\nDocs.\n")
        _write(tmp_path / ".claude/agents/readme.md", "Docs too.\n")
        assert list_project_agents(tmp_path) == []

    def test_invalid_toml_is_skipped(self, tmp_path):
        _write(tmp_path / ".codex/agents/broken.toml", "name = [unclosed\n")
        assert list_project_agents(tmp_path) == []

    @pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
    def test_symlinked_mirror_dedupes_against_canonical(self, tmp_path):
        canonical = _write(tmp_path / ".ai/agents/core/reviewer.md", _agent_md("reviewer"))
        mirror = tmp_path / ".claude/agents/reviewer.md"
        mirror.parent.mkdir(parents=True)
        mirror.symlink_to(canonical)
        agents = list_project_agents(tmp_path)
        assert [a.name for a in agents] == ["reviewer"]

    def test_first_harness_folder_wins_per_name(self, tmp_path):
        _write(tmp_path / ".navin/agents/reviewer.md", _agent_md("reviewer", "navin version"))
        _write(tmp_path / ".claude/agents/reviewer.md", _agent_md("reviewer", "claude version"))
        agent = find_project_agent(tmp_path, "reviewer")
        assert agent is not None
        assert agent.description == "navin version"

    def test_harness_dirs_cover_the_six_tool_folders(self):
        for folder in (".claude", ".codex", ".opencode", ".omp", ".agents", ".ai", ".cursor", ".navin"):
            assert folder in HARNESS_DIRS

    def test_unknown_dot_folder_is_discovered(self, tmp_path):
        # Discovery must not be limited to a whitelist: a repo may name its
        # config folder after a tool that does not exist yet.
        _write(tmp_path / ".myharness/agents/futurist.md", _agent_md("futurist"))
        assert find_project_agent(tmp_path, "futurist") is not None
        assert ".myharness" in harness_dirs(tmp_path)

    def test_vcs_and_cache_dot_folders_are_ignored(self, tmp_path):
        _write(tmp_path / ".git/agents/fake.md", _agent_md("fake"))
        _write(tmp_path / ".venv/agents/fake2.md", _agent_md("fake2"))
        assert list_project_agents(tmp_path) == []
        dirs = harness_dirs(tmp_path)
        assert ".git" not in dirs and ".venv" not in dirs

    def test_known_folders_keep_priority_over_unknown_ones(self, tmp_path):
        # .aaa sorts before .navin alphabetically, but known folders come
        # first so per-name conflicts stay deterministic.
        _write(tmp_path / ".aaa/agents/reviewer.md", _agent_md("reviewer", "unknown-folder version"))
        _write(tmp_path / ".navin/agents/reviewer.md", _agent_md("reviewer", "navin version"))
        agent = find_project_agent(tmp_path, "reviewer")
        assert agent is not None
        assert agent.description == "navin version"

    def test_home_harness_dirs_see_unknown_dot_folders(self, tmp_path, monkeypatch):
        (tmp_path / ".myharness").mkdir()
        (tmp_path / ".git").mkdir()
        (tmp_path / ".local").mkdir()
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
        dirs = home_harness_dirs()
        assert ".myharness" in dirs
        assert ".git" not in dirs
        assert ".local" not in dirs
        for folder in HARNESS_DIRS:
            assert folder in dirs

    def test_skills_in_unknown_dot_folder_are_listed(self, tmp_path):
        _write(tmp_path / ".weird/skills/deploy-x/SKILL.md", "---\nname: deploy-x\ndescription: d\n---\nBody.\n")
        names = [s["name"] for s in SkillsLoader(tmp_path, builtin_skills_dir=tmp_path / "none").list_skills()]
        assert "deploy-x" in names

    def test_skill_md_directly_in_dot_folder_or_subdir(self, tmp_path):
        _write(tmp_path / ".cursor/SKILL.md", "---\nname: cursor\ndescription: d\n---\nRoot.\n")
        _write(tmp_path / ".claude/review/SKILL.md", "---\nname: review\ndescription: d\n---\nSub.\n")
        _write(tmp_path / ".acme/playbook/SKILL.md", "---\nname: playbook\ndescription: d\n---\nAcme.\n")
        _write(tmp_path / ".navin/custom-note/SKILL.md", "---\nname: custom-note\ndescription: d\n---\nNote.\n")
        _write(tmp_path / ".navin/SKILL.md", "---\nname: navin\ndescription: d\n---\nNavin root.\n")
        loader = SkillsLoader(tmp_path, builtin_skills_dir=tmp_path / "none")
        names = [s["name"] for s in loader.list_skills()]
        assert "cursor" in names
        assert "review" in names
        assert "playbook" in names
        assert "custom-note" in names
        assert "navin" in names
        assert "Root." in (loader.load_skill("cursor") or "")
        assert "Sub." in (loader.load_skill("review") or "")
        assert "Acme." in (loader.load_skill("playbook") or "")
        assert "Note." in (loader.load_skill("custom-note") or "")
        assert "Navin root." in (loader.load_skill("navin") or "")


class TestSkillMirrors:
    @pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
    def test_symlinked_skill_dir_in_agents_mirror_is_listed(self, tmp_path):
        canonical = tmp_path / ".ai/skills/deploy-check"
        _write(canonical / "SKILL.md", "---\nname: deploy-check\ndescription: d\n---\nBody.\n")
        mirror = tmp_path / ".agents/skills/deploy-check"
        mirror.parent.mkdir(parents=True)
        mirror.symlink_to(canonical, target_is_directory=True)

        names = [s["name"] for s in SkillsLoader(tmp_path, builtin_skills_dir=tmp_path / "none").list_skills()]
        assert names.count("deploy-check") == 1


class TestClaudeMdImports:
    def test_claude_md_at_import_is_resolved(self, tmp_path):
        _write(tmp_path / "shared-rules.md", "Never push to main.")
        _write(tmp_path / "CLAUDE.md", "@shared-rules.md\n")
        content = ContextBuilder(workspace=tmp_path)._load_bootstrap_files()
        assert "Never push to main." in content

    def test_agents_md_wins_over_claude_shim(self, tmp_path):
        # The common shim: CLAUDE.md is just "@AGENTS.md". Instructions must
        # appear exactly once.
        _write(tmp_path / "AGENTS.md", "Real instructions here.")
        _write(tmp_path / "CLAUDE.md", "@AGENTS.md\n")
        content = ContextBuilder(workspace=tmp_path)._load_bootstrap_files()
        assert content.count("Real instructions here.") == 1

    def test_missing_import_resolves_to_nothing(self, tmp_path):
        _write(tmp_path / "CLAUDE.md", "Intro.\n@does-not-exist.md\nOutro.")
        content = ContextBuilder(workspace=tmp_path)._load_bootstrap_files()
        assert "Intro." in content and "Outro." in content
        assert "@does-not-exist.md" not in content

    def test_import_cycle_terminates(self, tmp_path):
        _write(tmp_path / "a.md", "A says:\n@b.md\n")
        _write(tmp_path / "b.md", "B says:\n@a.md\n")
        _write(tmp_path / "CLAUDE.md", "@a.md\n")
        content = ContextBuilder(workspace=tmp_path)._load_bootstrap_files()
        assert "A says:" in content and "B says:" in content

    def test_inline_at_mentions_are_left_alone(self, tmp_path):
        _write(tmp_path / "CLAUDE.md", "Contact @alice or email a@b.co for access.")
        content = ContextBuilder(workspace=tmp_path)._load_bootstrap_files()
        assert "Contact @alice or email a@b.co" in content


class TestEnvVarDocSync:
    def test_every_provider_env_var_is_documented(self):
        """docs/environment-variables.md must list every registry env var."""
        from navin.providers.registry import PROVIDERS

        doc = (
            Path(__file__).parent.parent / "docs" / "environment-variables.md"
        ).read_text(encoding="utf-8")
        missing = [
            var
            for spec in PROVIDERS
            for var in (spec.env_key, *spec.env_key_aliases)
            if var and f"`{var}`" not in doc
        ]
        assert not missing, f"Undocumented provider env vars: {missing}"
