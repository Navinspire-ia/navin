# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

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
    list_agents,
    list_project_agents,
    project_agents_summary,
)
from navin.agent.skills import SkillsLoader


def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _agent_md(name: str, description: str = "desc") -> str:
    return f"---\nname: {name}\ndescription: {description}\n---\nInstructions for {name}.\n"


def _fake_home(monkeypatch, home: Path) -> Path:
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    return home


class TestAgentDiscovery:
    @pytest.fixture(autouse=True)
    def _empty_home(self, tmp_path_factory, monkeypatch):
        # Personal agents under the developer's real ~/.navin/agents must not
        # leak into project-only assertions.
        _fake_home(monkeypatch, tmp_path_factory.mktemp("home"))

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


class TestPersonalAgents:
    """User-level agents under $HOME, shared by every workspace."""

    @pytest.fixture
    def home(self, tmp_path_factory, monkeypatch):
        return _fake_home(monkeypatch, tmp_path_factory.mktemp("home"))

    def test_home_navin_agents_are_discovered_as_personal(self, tmp_path, home):
        _write(home / ".navin/agents/tutor.md", _agent_md("tutor", "personal tutor"))
        _write(home / ".claude/agents/scribe.md", _agent_md("scribe"))
        _write(home / ".navin/agents/README.md", "# docs, not an agent\n")
        _write(tmp_path / ".navin/agents/reviewer.md", _agent_md("reviewer"))
        agents = {a.name: a for a in list_project_agents(tmp_path)}
        assert set(agents) == {"reviewer", "tutor", "scribe"}
        assert agents["reviewer"].scope == "project"
        assert agents["tutor"].scope == "personal"
        assert agents["scribe"].scope == "personal"
        # Project agents are listed before personal ones.
        assert list_project_agents(tmp_path)[0].name == "reviewer"

    def test_project_agent_wins_name_clash(self, tmp_path, home):
        _write(home / ".navin/agents/reviewer.md", _agent_md("reviewer", "personal version"))
        _write(tmp_path / ".claude/agents/reviewer.md", _agent_md("reviewer", "project version"))
        agent = find_project_agent(tmp_path, "Reviewer")
        assert agent is not None
        assert agent.description == "project version"
        assert agent.scope == "project"
        assert [a.name for a in list_project_agents(tmp_path)] == ["reviewer"]

    def test_workspace_equal_to_home_is_not_listed_twice(self, home):
        _write(home / ".navin/agents/tutor.md", _agent_md("tutor"))
        agents = list_project_agents(home)
        assert [(a.name, a.scope) for a in agents] == [("tutor", "project")]

    def test_summary_labels_personal_agents(self, tmp_path, home):
        from navin.agent.project_agents import project_agents_summary

        _write(home / ".navin/agents/tutor.md", _agent_md("tutor", "teaches"))
        _write(tmp_path / ".navin/agents/reviewer.md", _agent_md("reviewer", "reviews"))
        summary = project_agents_summary(tmp_path)
        assert "- **reviewer** - reviews" in summary
        assert "- **tutor** (personal) - teaches" in summary

    def test_context_lists_personal_agents(self, tmp_path, home):
        _write(home / ".navin/agents/tutor.md", _agent_md("tutor", "teaches"))
        prompt = ContextBuilder(workspace=tmp_path).build_system_prompt()
        assert "# Project Subagents" in prompt
        assert "**tutor** (personal)" in prompt

    def test_spawn_resolves_personal_agent(self, tmp_path, home):
        from types import SimpleNamespace

        from navin.agent.subagent import SubagentManager

        _write(
            home / ".navin/agents/tutor.md",
            "---\nname: tutor\ndescription: d\n---\nYou are the personal tutor.\n",
        )
        fake = SimpleNamespace(
            workspace=tmp_path,
            disabled_skills=set(),
            trust_workspace_harness_skills=True,
            _parent_context_lines=lambda *a, **kw: [],
        )
        prompt = SubagentManager._build_subagent_prompt(fake, tmp_path, agent_name="tutor")
        assert "# Role: tutor" in prompt
        assert "You are the personal tutor." in prompt


class TestAgentCreatorSkill:
    def test_frontmatter_is_valid(self):
        import yaml

        from navin.agent.skills import BUILTIN_SKILLS_DIR

        path = BUILTIN_SKILLS_DIR / "agent-creator" / "SKILL.md"
        content = path.read_text(encoding="utf-8")
        assert content.startswith("---\n")
        meta = yaml.safe_load(content.split("---", 2)[1])
        assert meta["name"] == "agent-creator"
        assert isinstance(meta["description"], str) and len(meta["description"]) > 40
        assert "spawn" in meta["description"]
        assert "—" not in content and "–" not in content

    def test_skill_is_loadable_and_documents_supported_fields(self, tmp_path):
        loader = SkillsLoader(tmp_path)
        assert "agent-creator" in [s["name"] for s in loader.list_skills(filter_unavailable=False)]
        body = loader.load_skill("agent-creator") or ""
        assert ".navin/agents/" in body and "~/.navin/agents/" in body
        assert 'spawn(agent="' in body


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


class TestExampleDescriptions:
    """Agent descriptions with unquoted <example> dialogue must still parse."""

    AGENT = (
        "---\n"
        "name: plugin-checker\n"
        "description: Use this agent when the user asks to \"validate my plugin\". Examples:\n"
        "\n"
        "<example>\n"
        "Context: The user finished a plugin.\n"
        "user: \"Is it ready?\"\n"
        "assistant: \"I'll use the plugin-checker agent.\"\n"
        "</example>\n"
        "tools: [\"read_file\", \"grep\"]\n"
        "color: yellow\n"
        "---\n"
        "You validate plugins and report a verdict.\n"
    )

    @pytest.fixture(autouse=True)
    def _empty_home(self, tmp_path_factory, monkeypatch):
        _fake_home(monkeypatch, tmp_path_factory.mktemp("home"))

    def test_frontmatter_survives_example_dialogue(self, tmp_path: Path) -> None:
        _write(tmp_path / ".navin" / "agents" / "checker.md", self.AGENT)
        agent = find_project_agent(tmp_path, "plugin-checker")
        assert agent is not None
        assert agent.name == "plugin-checker"
        assert "<example>" in agent.description
        assert "user: \"Is it ready?\"" in agent.description
        assert agent.prompt.startswith("You validate plugins")
        assert "tools:" not in agent.description

    def test_summary_strips_examples(self, tmp_path: Path) -> None:
        from navin.agent.project_agents import project_agents_summary

        _write(tmp_path / ".navin" / "agents" / "checker.md", self.AGENT)
        summary = project_agents_summary(tmp_path)
        assert 'validate my plugin' in summary
        assert "<example>" not in summary and "assistant:" not in summary

    def test_lenient_parser_reads_list_and_scalars(self) -> None:
        from navin.agent.frontmatter import split_frontmatter

        meta, body, error = split_frontmatter(self.AGENT)
        assert error is None and meta is not None
        assert meta["tools"] == ["read_file", "grep"]
        assert meta["color"] == "yellow"
        assert body.startswith("You validate")
        assert split_frontmatter("---\nname: x\nno close")[2] is not None
        assert split_frontmatter("plain body")[0] is None


class TestExtensionAgents:
    """Agents shipped with Navin and agents of enabled plugins."""

    @pytest.fixture(autouse=True)
    def _isolated(self, tmp_path_factory, monkeypatch):
        _fake_home(monkeypatch, tmp_path_factory.mktemp("home"))
        self.plugins = tmp_path_factory.mktemp("plugins")
        monkeypatch.setattr("navin.plugins.manager.plugins_root", lambda: self.plugins)

    def test_builtin_agents_are_listed_and_found(self, tmp_path):
        agents = {a.name: a for a in list_agents(tmp_path)}
        for name in ("agent-creator", "plugin-validator", "skill-reviewer"):
            assert agents[name].scope == "builtin"
            assert len(agents[name].prompt) > 500
            assert "<example>" in agents[name].description
        validator = agents["plugin-validator"]
        assert "read_file" in validator.tools and "exec" in validator.tools
        assert "write_file" not in validator.tools
        assert find_project_agent(tmp_path, "Plugin-Validator").scope == "builtin"
        # The project-only listing does not grow with built-ins.
        assert list_project_agents(tmp_path) == []

    def test_project_agent_shadows_builtin(self, tmp_path):
        _write(tmp_path / ".navin/agents/skill-reviewer.md", _agent_md("skill-reviewer", "mine"))
        agent = find_project_agent(tmp_path, "skill-reviewer")
        assert agent.scope == "project" and agent.description == "mine"

    def test_enabled_plugin_agents_are_discovered(self, tmp_path):
        from navin.plugins.manager import PluginManager

        plugin = self.plugins / "docs-kit"
        _write(plugin / "plugin.json", '{"name": "docs-kit", "version": "0.1.0"}')
        _write(
            plugin / "agents/doc-writer.md",
            "---\nname: doc-writer\ndescription: d\ntools: Read, Bash(git:*)\n---\nWrite docs.\n",
        )
        manager = PluginManager(self.plugins)
        agent = find_project_agent(tmp_path, "doc-writer")
        assert agent.scope == "plugin" and agent.plugin == "docs-kit"
        assert set(agent.tools) == {"read_file", "exec"}
        assert "(plugin docs-kit)" in project_agents_summary(tmp_path)
        manager.set_enabled("docs-kit", False)
        assert find_project_agent(tmp_path, "doc-writer") is None

    def test_summary_marks_builtin_agents(self, tmp_path):
        summary = project_agents_summary(tmp_path)
        assert "**plugin-validator** (built-in)" in summary
        assert "<example>" not in summary

    def test_agent_tools_narrow_the_subagent_allowlist(self, tmp_path):
        from navin.agent.subagent import _agent_allowed

        _write(
            tmp_path / ".navin/agents/ro.md",
            '---\nname: ro\ndescription: d\ntools: ["grep", "Read"]\n---\nRead only.\n',
        )
        _write(tmp_path / ".navin/agents/all.md", _agent_md("all"))
        assert _agent_allowed(None, tmp_path, "ro") == frozenset({"grep", "read_file", "skill"})
        assert _agent_allowed(frozenset({"grep", "exec"}), tmp_path, "ro") == frozenset({"grep"})
        assert _agent_allowed(frozenset({"exec"}), tmp_path, "all") == frozenset({"exec"})
        assert _agent_allowed(None, tmp_path, "missing") is None
        assert _agent_allowed(None, tmp_path, None) is None

    def test_disallowed_tools_are_denied_even_without_an_allowlist(self, tmp_path):
        from navin.agent.subagent import _agent_allowed, _agent_denied

        _write(
            tmp_path / ".navin/agents/safe.md",
            "---\nname: safe\ndescription: d\ndisallowedTools: Write, Bash\n---\nNo writes.\n",
        )
        agent = find_project_agent(tmp_path, "safe")
        assert agent is not None and set(agent.disallowed) == {"write_file", "exec"}
        assert _agent_allowed(None, tmp_path, "safe") is None
        assert _agent_denied(None, tmp_path, "safe") == frozenset({"write_file", "exec"})
        assert _agent_denied(frozenset({"web_fetch"}), tmp_path, "safe") == frozenset(
            {"web_fetch", "write_file", "exec"}
        )
        assert _agent_denied(None, tmp_path, "missing") is None

    def test_agent_model_selects_its_preset_and_unknown_models_inherit(self, tmp_path):
        from types import SimpleNamespace

        from navin.agent.tools.spawn import SpawnTool

        _write(tmp_path / ".navin/agents/fast.md", "---\nname: fast\nmodel: quick\n---\nGo.\n")
        _write(tmp_path / ".navin/agents/same.md", "---\nname: same\nmodel: inherit\n---\nGo.\n")
        tool = SpawnTool.__new__(SpawnTool)
        tool._manager = SimpleNamespace(workspace=tmp_path)
        tool._resolve_model_preset = lambda name: name
        assert tool._agent_model_preset("fast") == "quick"
        assert tool._agent_model_preset("same") == ""
        assert tool._agent_model_preset("missing") == ""
        tool._resolve_model_preset = None
        assert tool._agent_model_preset("fast") == ""

    def test_permission_mode_is_read_and_normalized(self, tmp_path):
        from navin.agent.subagent import _agent_permission_mode

        for name, raw, expected in (
            ("ro", "plan", "plan"), ("ed", "acceptEdits", "acceptEdits"),
            ("by", "bypass-permissions", "bypassPermissions"), ("odd", "sometimes", "default"),
        ):
            _write(
                tmp_path / f".navin/agents/{name}.md",
                f"---\nname: {name}\ndescription: d\npermissionMode: {raw}\n---\nWork.\n",
            )
            assert find_project_agent(tmp_path, name).permission_mode == expected
            assert _agent_permission_mode(tmp_path, name) == expected
        assert _agent_permission_mode(tmp_path, "missing") == "default"
        assert _agent_permission_mode(tmp_path, None) == "default"

    def test_an_isolated_run_is_resumable_while_its_checkout_exists(self, tmp_path):
        from navin.agent.subagent import SubagentManager, _saved_checkout

        checkout = tmp_path / "wt"
        checkout.mkdir()
        saved = {"path": str(checkout), "origin": str(tmp_path), "base": "abc"}

        def record(checkpoint):
            return {"metadata": {"done": False}, "descriptor": {"isolate": True},
                    "checkpoint": checkpoint, "owner_pid": 0}

        meta = SubagentManager._stored_metadata(
            object(), record({"phase": "llm", "checkout": saved}), alive=False
        )
        assert meta["recoverable"] and not meta["requires_relaunch"]
        assert _saved_checkout({"checkout": saved}) == saved
        checkout.rmdir()
        assert _saved_checkout({"checkout": saved}) is None
        meta = SubagentManager._stored_metadata(
            object(), record({"phase": "llm", "checkout": saved}), alive=False
        )
        assert not meta["recoverable"] and meta["requires_relaunch"]
