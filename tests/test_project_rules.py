# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Imported project rules discovery (.cursor/rules, .cursorrules, .clinerules,
.windsurfrules, copilot-instructions, .navin/rules).

An imported repository must keep the conventions it already carries for other
coding agents: always-on rules are inlined into the prompt, conditional Cursor
rules are listed by path so the agent can read them when relevant.
"""

from pathlib import Path

from navin.agent.project_rules import (
    list_navin_rules,
    project_rules_summary,
    write_navin_rule,
)
from navin.webui.project_rules_api import (
    ProjectRulesError,
    project_rules_payload,
    save_project_rule_payload,
)


def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


class TestCursorRules:
    def test_always_apply_rule_is_inlined(self, tmp_path):
        _write(
            tmp_path / ".cursor/rules/style.mdc",
            "---\nalwaysApply: true\n---\nNever use em dashes.\n",
        )
        summary = project_rules_summary(tmp_path)
        assert "Never use em dashes." in summary
        assert ".cursor/rules/style.mdc" in summary

    def test_conditional_rule_is_listed_not_inlined(self, tmp_path):
        _write(
            tmp_path / ".cursor/rules/frontend.mdc",
            "---\ndescription: React conventions\nglobs: 'src/**/*.tsx'\n---\nUse hooks.\n",
        )
        summary = project_rules_summary(tmp_path)
        assert "Use hooks." not in summary
        assert "`.cursor/rules/frontend.mdc`" in summary
        assert "React conventions" in summary
        assert "src/**/*.tsx" in summary

    def test_quoted_always_apply_is_inlined_too(self, tmp_path):
        # Hand-written frontmatter sometimes quotes the boolean.
        _write(
            tmp_path / ".cursor/rules/quoted.mdc",
            '---\nalwaysApply: "true"\n---\nQuoted but still always on.\n',
        )
        assert "Quoted but still always on." in project_rules_summary(tmp_path)

    def test_nested_rules_folders_are_scanned(self, tmp_path):
        _write(
            tmp_path / ".cursor/rules/backend/db.mdc",
            "---\nalwaysApply: true\n---\nAlways use migrations.\n",
        )
        assert "Always use migrations." in project_rules_summary(tmp_path)

    def test_legacy_cursorrules_file_is_inlined(self, tmp_path):
        _write(tmp_path / ".cursorrules", "Tabs, not spaces.\n")
        assert "Tabs, not spaces." in project_rules_summary(tmp_path)


class TestOtherHarnessRules:
    def test_clinerules_file_and_folder(self, tmp_path):
        _write(tmp_path / ".clinerules", "Cline says hello.\n")
        assert "Cline says hello." in project_rules_summary(tmp_path)

        folder_project = tmp_path / "other"
        _write(folder_project / ".clinerules/testing.md", "Run pytest before commit.\n")
        assert "Run pytest before commit." in project_rules_summary(folder_project)

    def test_windsurfrules_is_inlined(self, tmp_path):
        _write(tmp_path / ".windsurfrules", "Windsurf conventions.\n")
        assert "Windsurf conventions." in project_rules_summary(tmp_path)

    def test_copilot_instructions_are_inlined(self, tmp_path):
        _write(
            tmp_path / ".github/copilot-instructions.md",
            "This repo uses pnpm, never npm.\n",
        )
        assert "never npm" in project_rules_summary(tmp_path)


class TestNavinRules:
    def test_navin_rules_are_inlined_but_readme_skipped(self, tmp_path):
        _write(tmp_path / ".navin/rules/README.md", "Scaffold placeholder.\n")
        _write(tmp_path / ".navin/rules/architecture.md", "Keep webui and site separate.\n")
        summary = project_rules_summary(tmp_path)
        assert "Keep webui and site separate." in summary
        assert "Scaffold placeholder." not in summary

    def test_list_and_write_navin_rules(self, tmp_path):
        written = write_navin_rule(
            tmp_path, name="style", content="Prefer simple dashes.\n"
        )
        assert written["path"] == ".navin/rules/style.md"
        assert written["name"] == "style"
        rows = list_navin_rules(tmp_path)
        assert len(rows) == 1
        assert rows[0]["content"] == "Prefer simple dashes.\n"
        write_navin_rule(tmp_path, name="style", content="Updated.\n")
        assert list_navin_rules(tmp_path)[0]["content"] == "Updated.\n"

    def test_write_rejects_unsafe_names(self, tmp_path):
        import pytest

        with pytest.raises(ValueError):
            write_navin_rule(tmp_path, name="../escape", content="nope")
        with pytest.raises(ValueError):
            write_navin_rule(tmp_path, name="README", content="nope")


class TestProjectRulesApi:
    class _Scope:
        def __init__(self, root: Path) -> None:
            self.project_path = str(root)
            self.project_name = root.name

    def test_payload_lists_editable_rules(self, tmp_path: Path) -> None:
        write_navin_rule(tmp_path, name="arch", content="Keep modules thin.\n")
        payload = project_rules_payload(self._Scope(tmp_path))  # type: ignore[arg-type]
        assert payload["total"] == 1
        assert payload["rules"][0]["name"] == "arch"
        assert payload["rules_dir"] == ".navin/rules"

    def test_save_payload_writes_rule(self, tmp_path: Path) -> None:
        saved = save_project_rule_payload(
            self._Scope(tmp_path),  # type: ignore[arg-type]
            name="ops",
            content="Run verify after patches.\n",
        )
        assert saved["saved"] is True
        assert (tmp_path / ".navin/rules/ops.md").read_text(encoding="utf-8") == (
            "Run verify after patches.\n"
        )

    def test_save_payload_rejects_bad_name(self, tmp_path: Path) -> None:
        import pytest

        with pytest.raises(ProjectRulesError):
            save_project_rule_payload(
                self._Scope(tmp_path),  # type: ignore[arg-type]
                name="bad name",
                content="x",
            )


class TestBounds:
    def test_project_without_rules_returns_empty(self, tmp_path):
        assert project_rules_summary(tmp_path) == ""

    def test_giant_rule_body_is_clipped(self, tmp_path):
        _write(
            tmp_path / ".cursorrules",
            "x" * 50_000,
        )
        summary = project_rules_summary(tmp_path)
        assert "[... rule truncated ...]" in summary
        assert len(summary) < 20_000

    def test_oversized_file_is_skipped(self, tmp_path):
        _write(tmp_path / ".cursorrules", "y" * 100_000)
        assert project_rules_summary(tmp_path) == ""


class TestSystemPromptIntegration:
    def test_rules_section_lands_in_system_prompt(self, tmp_path):
        from navin.agent.context import ContextBuilder

        _write(
            tmp_path / ".cursor/rules/core.mdc",
            "---\nalwaysApply: true\n---\nRespect the design tokens.\n",
        )
        prompt = ContextBuilder(workspace=tmp_path).build_system_prompt(
            include_memory_recent_history=False,
        )
        assert "# Project Rules" in prompt
        assert "Respect the design tokens." in prompt

    def test_no_rules_no_section(self, tmp_path):
        from navin.agent.context import ContextBuilder

        prompt = ContextBuilder(workspace=tmp_path).build_system_prompt(
            include_memory_recent_history=False,
        )
        assert "# Project Rules" not in prompt
