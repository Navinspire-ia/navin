# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Markdown slash commands, user lifecycle hooks and plugin components."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

from navin.agent import custom_commands as cc
from navin.agent import user_hooks as uh
from navin.plugins.manager import PluginManager, expand_plugin_vars


def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


@pytest.fixture
def env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr("navin.plugins.manager.plugins_root", lambda: plugins)
    workspace = tmp_path / "ws"
    workspace.mkdir()
    return {"home": home, "plugins": plugins, "ws": workspace}


def _plugin(plugins: Path, name: str = "kit", manifest: dict | None = None) -> Path:
    root = plugins / name
    _write(root / "plugin.json", json.dumps({"name": name, "version": "0.1.0", **(manifest or {})}))
    return root


# -- commands -----------------------------------------------------------------

class TestCustomCommands:
    def test_discovery_order_and_shadowing(self, env):
        ws, home, plugins = env["ws"], env["home"], env["plugins"]
        _write(ws / ".navin/commands/review.md", "---\ndescription: project\n---\nProject review.\n")
        _write(home / ".navin/commands/review.md", "---\ndescription: personal\n---\nPersonal.\n")
        _write(home / ".navin/commands/git/sync.md", "Sync now.\n")
        _write(_plugin(plugins) / "commands/review.md", "Plugin review.\n")
        _write(ws / ".navin/commands/README.md", "not a command\n")
        _write(ws / ".navin/commands/empty.md", "---\ndescription: x\n---\n")

        names = {c.name: c for c in cc.list_custom_commands(ws)}
        assert names["review"].scope == "project" and names["review"].description == "project"
        assert names["sync"].scope == "personal" and names["sync"].namespace == "git"
        assert names["create-plugin"].scope == "builtin"
        assert "readme" not in names and "empty" not in names
        # /plugin:name always reaches the plugin version.
        assert cc.find_custom_command(ws, "/kit:review").body == "Plugin review."
        assert cc.find_custom_command(ws, "/REVIEW").scope == "project"
        assert cc.find_custom_command(ws, "/nope") is None

    def test_arguments_defaults_and_appended_arguments(self, env):
        ws = env["ws"]
        _write(ws / ".navin/commands/fix.md", "Fix #$1 on ${2:-fix}/issue-$1. All: $ARGUMENTS. Third: [$3]\n")
        _write(ws / ".navin/commands/plain.md", "Draft the standup.\n")
        fix = cc.find_custom_command(ws, "fix")
        assert cc.expand_command(fix, '42 "hot fix"', ws) == (
            'Fix #42 on hot fix/issue-42. All: 42 "hot fix". Third: []'
        )
        assert "on fix/issue-7" in cc.expand_command(fix, "7", ws)
        plain = cc.find_custom_command(ws, "plain")
        assert cc.expand_command(plain, "mention the freeze", ws).endswith("Arguments: mention the freeze")
        assert cc.expand_command(plain, "", ws) == "Draft the standup."

    def test_file_references_stay_inside_allowed_roots(self, env, tmp_path):
        ws = env["ws"]
        _write(ws / "docs/rules.md", "RULE ONE")
        _write(tmp_path / "secret.txt", "SECRET")
        _write(ws / ".navin/commands/check.md", "Apply @docs/rules.md and @$1. Mail me@example.com.\n")
        cmd = cc.find_custom_command(ws, "check")
        text = cc.expand_command(cmd, "../secret.txt", ws)
        assert '<file path="docs/rules.md">' in text and "RULE ONE" in text
        assert "SECRET" not in text and "@../secret.txt" in text
        assert "me@example.com" in text

    def test_shell_snippets_need_the_shell_tool(self, env):
        ws = env["ws"]
        _write(ws / ".navin/commands/denied.md", "Status: !`echo RAN`\n")
        _write(ws / ".navin/commands/granted.md", "---\nallowed-tools: Read, Bash(echo:*)\n---\nStatus: !`echo RAN`\n")
        assert "!`echo RAN`" in cc.expand_command(cc.find_custom_command(ws, "denied"), "", ws)
        granted = cc.find_custom_command(ws, "granted")
        assert granted.allowed_tools == ["exec", "read_file"]
        expanded = cc.expand_command(granted, "", ws)
        assert "RAN" in expanded and "!`" not in expanded

    def test_shell_snippets_run_navin_own_python(self, env, monkeypatch):
        import sys

        ws = env["ws"]
        _write(
            ws / ".navin/commands/py.md",
            "---\nallowed-tools: exec\n---\n!`${NAVIN_PYTHON} -c \"import navin, sys; print('NAVIN-OK', sys.executable)\"`\n",
        )
        expanded = cc.expand_command(cc.find_custom_command(ws, "py"), "", ws)
        assert f"NAVIN-OK {sys.executable}" in expanded
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        assert cc.navin_python_command().endswith(" python")

    def test_plugin_root_is_substituted(self, env):
        root = _plugin(env["plugins"])
        _write(root / "templates/t.md", "TEMPLATE BODY")
        _write(root / "commands/use.md", "Root ${NAVIN_PLUGIN_ROOT}. Template: @${NAVIN_PLUGIN_ROOT}/templates/t.md\n")
        text = cc.expand_command(cc.find_custom_command(env["ws"], "use"), "", env["ws"])
        assert f"Root {root}." in text and "TEMPLATE BODY" in text

    def test_build_turn_metadata_and_palette(self, env):
        from navin.command.builtin import builtin_command_palette

        ws = env["ws"]
        _write(ws / ".navin/commands/ro.md", "---\ndescription: Read only\nargument-hint: <path>\nallowed-tools: Read, Grep\n---\nRead $1.\n")
        _write(ws / ".navin/commands/help.md", "Shadowed by the built-in.\n")
        content, meta = cc.build_command_turn(cc.find_custom_command(ws, "ro"), "a.py", ws)
        assert content.startswith("[Command /ro a.py]\nRead a.py.")
        assert meta["custom_command_allowed_tools"] == ["grep", "read_file", "skill"]
        palette = {row["command"]: row for row in builtin_command_palette(None, ws)}
        assert palette["/ro"]["arg_hint"] == "<path>" and palette["/ro"]["description"] == "Read only"
        assert "/create-plugin" in palette
        assert palette["/help"]["description"] != "Custom command"
        assert "`/ro <path>`" in cc.commands_summary(ws)

    def test_commands_endpoint_lists_the_workspace_commands(self, env):
        from types import MethodType

        from websockets.datastructures import Headers
        from websockets.http11 import Request

        from navin.webui.ws_http import GatewayHTTPHandler

        ws = env["ws"]
        _write(ws / ".navin/commands/ship-it.md", "---\ndescription: Ship\n---\nShip.\n")
        handler = object.__new__(GatewayHTTPHandler)
        handler.check_api_token = MethodType(lambda self, request: True, handler)

        def names(path: str) -> set[str]:
            response = asyncio.run(handler._handle_commands(Request(path, Headers())))
            return {row["command"] for row in json.loads(response.body)["commands"]}

        assert "/ship-it" in names(f"/api/commands?module=code&workspace={ws}")
        assert "/ship-it" not in names("/api/commands?module=code")
        assert "/ship-it" not in names(f"/api/commands?workspace={ws / 'missing'}")

    def test_create_plugin_command_covers_eight_phases(self, env):
        cmd = cc.find_custom_command(env["ws"], "/create-plugin")
        text = cc.expand_command(cmd, "a docs linter", env["ws"])
        assert "Initial request: a docs linter" in text
        for phase in range(1, 9):
            assert f"## Phase {phase}" in text
        assert "plugin-validator" in text and "skill-reviewer" in text


# -- hooks --------------------------------------------------------------------

_WORKFLOW_COMMANDS = (
    "feature-dev", "commit", "commit-push-pr", "clean_gone", "code-review", "dedupe", "triage-issue",
    "review-pr",
)
_WORKFLOW_AGENTS = (
    "code-explorer", "code-architect", "code-reviewer",
    "comment-analyzer", "pr-test-analyzer", "silent-failure-hunter", "type-design-analyzer",
)
# The one review agent that applies its own suggestions.
_EDITING_AGENTS = ("code-simplifier",)


class TestWorkflowPlugins:
    """Bundled feature-dev, commit-commands, code-review and issue-triage."""

    def test_files_are_bundled_and_brand_clean(self, env):
        names = {c.name: c for c in cc.list_custom_commands(env["ws"])}
        files = []
        for name in _WORKFLOW_COMMANDS:
            assert names[name].scope == "builtin" and names[name].description, name
            files.append(Path(names[name].source))
        from navin.agent.project_agents import BUILTIN_AGENTS_DIR

        files += [BUILTIN_AGENTS_DIR / f"{name}.md" for name in (*_WORKFLOW_AGENTS, *_EDITING_AGENTS)]
        for path in files:
            text = path.read_text(encoding="utf-8")
            assert "claude" not in text.lower(), path
            assert "\u2014" not in text and "\u2013" not in text, path

    def test_every_builtin_plugin_ships_a_readme_with_examples(self):
        root = Path(cc.__file__).resolve().parents[1] / "builtin_plugins"
        plugins = sorted(p for p in root.iterdir() if (p / "plugin.json").is_file())
        assert {p.name for p in plugins} >= {
            "feature-dev", "commit-commands", "code-review", "issue-triage", "plugin-dev",
            "pr-review-toolkit", "frontend-design", "hookify", "security-guidance",
            "ralph-loop", "agent-sdk-dev", "model-migration", "explanatory-output-style", "learning-output-style",
        }
        for plugin in plugins:
            readme = plugin / "README.md"
            assert readme.is_file(), plugin.name
            text = readme.read_text(encoding="utf-8")
            lowered = text.lower()
            assert "claude" not in lowered and "anthropic" not in lowered, readme
            assert "\u2014" not in text and "\u2013" not in text, readme
            assert "example" in lowered and "```" in text, readme
            manifest = json.loads((plugin / "plugin.json").read_text(encoding="utf-8"))
            for command in manifest.get("commands", []):
                assert f"/{command}" in text, (readme, command)
            for agent in manifest.get("agents", []):
                assert agent in text, (readme, agent)

    def test_agents_are_read_only_and_spawnable(self, env):
        from navin.agent.project_agents import find_project_agent

        for name in (*_WORKFLOW_AGENTS, "conversation-analyzer"):
            agent = find_project_agent(env["ws"], name)
            assert agent.scope == "builtin" and "Use when" in agent.description
            assert "read_file" in agent.tools and "grep" in agent.tools
            assert not {"write_file", "edit_file", "apply_patch", "exec", "git"} & set(agent.tools)
        assert "code_review" in find_project_agent(env["ws"], "code-reviewer").tools
        simplifier = find_project_agent(env["ws"], "code-simplifier")
        assert simplifier.scope == "builtin" and "Use when" in simplifier.description
        # It edits, so it must also be able to prove the behaviour did not change.
        assert {"apply_patch", "verify", "test_run"} <= set(simplifier.tools)
        assert not {"exec", "git", "write_file", "manage_files"} & set(simplifier.tools)

    def test_commit_inlines_the_real_git_context(self, env):
        import subprocess

        ws = env["ws"]
        git = ["git", "-C", str(ws), "-c", "user.name=t", "-c", "user.email=t@example.com"]
        subprocess.run([*git, "init", "-q", "-b", "main"], check=True)
        _write(ws / "a.txt", "one\n")
        subprocess.run([*git, "add", "a.txt"], check=True)
        subprocess.run([*git, "commit", "-q", "-m", "feat: first commit"], check=True)
        _write(ws / "a.txt", "one\ntwo-added-line\n")
        content, meta = cc.build_command_turn(cc.find_custom_command(ws, "commit"), "", ws)
        assert "!`" not in content
        assert "+two-added-line" in content and "feat: first commit" in content
        assert "\nmain\n" in content
        assert meta["custom_command_allowed_tools"] == ["exec", "git", "read_file", "skill"]
        push, push_meta = cc.build_command_turn(cc.find_custom_command(ws, "commit-push-pr"), "", ws)
        assert "forge action=pr_create" in push and "+two-added-line" in push
        assert "forge" in push_meta["custom_command_allowed_tools"]

    def test_arguments_reach_the_workflows(self, env):
        ws = env["ws"]
        feature = cc.expand_command(cc.find_custom_command(ws, "feature-dev"), "OAuth login", ws)
        assert "Initial request: OAuth login" in feature
        assert all(f"## Phase {n}" in feature for n in range(1, 8))
        review = cc.expand_command(cc.find_custom_command(ws, "code-review"), "42 --comment", ws)
        assert "Arguments: 42 --comment" in review and "pr_comments action=post" in review
        assert "No issues found. Checked for bugs and AGENTS.md compliance." in review
        triage = cc.expand_command(cc.find_custom_command(ws, "triage-issue"), "7 event: issue_comment", ws)
        assert "7 event: issue_comment" in triage and "forge action=issue_labels" in triage
        assert "Issue: 12" in cc.expand_command(cc.find_custom_command(ws, "dedupe"), "12", ws)
        review_pr = cc.find_custom_command(ws, "review-pr")
        expanded = cc.expand_command(review_pr, "tests errors parallel", ws)
        assert '**Requested aspects (optional):** "tests errors parallel"' in expanded
        for agent in (*_WORKFLOW_AGENTS[2:], *_EDITING_AGENTS):
            assert f"`{agent}`" in expanded, agent
        assert {"spawn", "code_review"} <= set(review_pr.allowed_tools)
        # clean_gone inherits the session tools: it needs the shell for worktrees.
        assert cc.find_custom_command(ws, "clean_gone").allowed_tools is None

    def test_extensions_prompt_points_to_the_catalog(self, env):
        from navin.agent.context import ContextBuilder

        section = ContextBuilder._extensions_section(env["ws"])
        assert "Bundled plugins (catalog `" in section and "marketplace.json" in section
        assert "commit the current changes -> /commit" in section
        # Bundled commands are typed by the user: their list left the prompt,
        # their plugin routes (review a pull request -> /code-review) stay.
        assert "(built-in)" not in section
        assert "review a pull request -> /code-review" in section
        assert "Also bundled" in section


# -- hooks --------------------------------------------------------------------

def _hook_script(path: Path, body: str) -> str:
    _write(path, "#!/usr/bin/env bash\n" + body)
    return f"bash {path}"


def _hooks_json(path: Path, table: dict) -> Path:
    return _write(path, json.dumps({"hooks": table}))


class TestUserHooks:
    def test_pre_tool_use_blocks_with_exit_2_and_matches_aliases(self, env):
        cmd = _hook_script(env["home"] / "block.sh", 'cat >/dev/null; echo "no shell today" >&2; exit 2\n')
        _hooks_json(env["home"] / ".navin/hooks.json", {
            "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": cmd}]}],
        })
        reason = asyncio.run(uh.pre_tool_use_block(env["ws"], "s", "exec", {"command": "ls"}))
        assert reason == "no shell today"
        assert asyncio.run(uh.pre_tool_use_block(env["ws"], "s", "read_file", {"path": "a"})) is None

    def test_hook_receives_event_json_and_env(self, env):
        out = env["ws"] / "seen.json"
        cmd = _hook_script(env["home"] / "rec.sh", f'cat > {out}; echo "$NAVIN_PROJECT_DIR" >> {out}.dir\n')
        _hooks_json(env["home"] / ".navin/hooks.json", {
            "PreToolUse": [{"matcher": "", "hooks": [{"type": "command", "command": cmd}]}],
        })
        assert asyncio.run(uh.pre_tool_use_block(env["ws"], "sess-1", "grep", {"pattern": "x"})) is None
        event = json.loads(out.read_text())
        assert event["hook_event_name"] == "PreToolUse" and event["session_id"] == "sess-1"
        assert event["tool_name"] == "grep" and event["tool_input"] == {"pattern": "x"}
        assert Path(str(out) + ".dir").read_text().strip() == str(env["ws"])

    def test_json_decisions_and_non_blocking_errors(self, env):
        deny = _hook_script(env["home"] / "deny.sh", "cat >/dev/null; echo '{\"hookSpecificOutput\": {\"permissionDecision\": \"deny\", \"permissionDecisionReason\": \"env is protected\"}}'\n")
        broken = _hook_script(env["home"] / "broken.sh", "cat >/dev/null; exit 1\n")
        lint = _hook_script(env["home"] / "lint.sh", "cat >/dev/null; echo '{\"decision\": \"block\", \"reason\": \"E501 line too long\"}'\n")
        _hooks_json(env["home"] / ".navin/hooks.json", {
            "PreToolUse": [
                {"matcher": "write_file", "hooks": [{"type": "command", "command": deny}]},
                {"matcher": "edit_file", "hooks": [{"type": "command", "command": broken}]},
            ],
            "PostToolUse": [{"matcher": "Write|Edit", "hooks": [{"type": "command", "command": lint}]}],
        })
        assert asyncio.run(uh.pre_tool_use_block(env["ws"], "s", "write_file", {})) == "env is protected"
        assert asyncio.run(uh.pre_tool_use_block(env["ws"], "s", "edit_file", {})) is None
        feedback = asyncio.run(uh.post_tool_use_feedback(env["ws"], "s", "edit_file", {}, "ok"))
        assert feedback == "[PostToolUse hook] E501 line too long"
        assert asyncio.run(uh.post_tool_use_feedback(env["ws"], "s", "grep", {}, "ok")) is None

    def test_timeout_is_a_non_blocking_error(self, env):
        slow = _hook_script(env["home"] / "slow.sh", "sleep 5; exit 2\n")
        _hooks_json(env["home"] / ".navin/hooks.json", {
            "PreToolUse": [{"hooks": [{"type": "command", "command": slow, "timeout": 1}]}],
        })
        assert asyncio.run(uh.pre_tool_use_block(env["ws"], "s", "exec", {})) is None

    def test_prompt_and_session_hooks_add_context_or_refuse(self, env):
        ctx = _hook_script(env["home"] / "ctx.sh", "cat >/dev/null; echo 'Sprint goal: export API'\n")
        guard = _hook_script(
            env["home"] / "guard.sh",
            'input=$(cat); if [[ "$input" == *AKIA* ]]; then echo "remove the key" >&2; exit 2; fi\n',
        )
        _hooks_json(env["home"] / ".navin/hooks.json", {
            "SessionStart": [{"hooks": [{"type": "command", "command": ctx}]}],
            "UserPromptSubmit": [{"hooks": [{"type": "command", "command": guard}]}],
        })
        first = asyncio.run(uh.prompt_hooks(env["ws"], "s", "hello", new_session=True))
        assert not first.blocked and first.additional_context == "Sprint goal: export API"
        later = asyncio.run(uh.prompt_hooks(env["ws"], "s", "hello", new_session=False))
        assert later.additional_context == ""
        refused = asyncio.run(uh.prompt_hooks(env["ws"], "s", "key AKIAABCDEFGHIJKLMNOP", new_session=False))
        assert refused.blocked and refused.reason == "remove the key"

    def test_stop_hook_and_subagent_stop(self, env):
        stop = _hook_script(
            env["home"] / "stop.sh",
            'input=$(cat); if [[ "$input" == *\'"stop_hook_active": true\'* ]]; then exit 0; fi\n'
            "echo '{\"decision\": \"block\", \"reason\": \"run the tests first\"}'\n",
        )
        _hooks_json(env["home"] / ".navin/hooks.json", {
            "Stop": [{"hooks": [{"type": "command", "command": stop}]}],
        })
        assert asyncio.run(uh.stop_hook_reason(env["ws"], "s", "Done.", stop_hook_active=False)) == "run the tests first"
        assert asyncio.run(uh.stop_hook_reason(env["ws"], "s", "Done.", stop_hook_active=True)) is None
        assert asyncio.run(uh.stop_hook_reason(env["ws"], "s", "Done.", stop_hook_active=False, subagent=True)) is None

    def test_system_message_reaches_the_user_not_the_model(self, env):
        note = _hook_script(
            env["home"] / "note.sh",
            "cat >/dev/null; echo '{\"systemMessage\": \"Lint passed\", \"additionalContext\": \"ok\"}'\n",
        )
        _hooks_json(env["home"] / ".navin/hooks.json", {
            "PreToolUse": [{"hooks": [{"type": "command", "command": note}]}],
            "PostToolUse": [{"hooks": [{"type": "command", "command": note}]}],
            "Stop": [{"hooks": [{"type": "command", "command": note}]}],
        })
        seen: list[str] = []

        async def notify(text: str) -> None:
            seen.append(text)

        assert asyncio.run(uh.pre_tool_use_block(env["ws"], "s", "exec", {}, notify=notify)) is None
        feedback = asyncio.run(uh.post_tool_use_feedback(env["ws"], "s", "exec", {}, "r", notify=notify))
        assert feedback == "[PostToolUse hook] ok" and "Lint passed" not in feedback
        assert asyncio.run(uh.stop_hook_reason(env["ws"], "s", "x", stop_hook_active=False, notify=notify)) is None
        assert seen == ["Lint passed"] * 3

        async def broken(text: str) -> None:
            raise RuntimeError("ui gone")

        assert asyncio.run(uh.pre_tool_use_block(env["ws"], "s", "exec", {}, notify=broken)) is None

    def test_announce_hook_messages_makes_one_row_each(self):
        rows: list[dict] = []

        async def on_progress(text, *, tool_hint=False, tool_events=None, **_):
            rows.extend(tool_events or [])

        asyncio.run(uh.announce_hook_messages(on_progress, ["Lint passed", "  ", "2 files formatted"], "t1"))
        assert [r["result"] for r in rows] == ["Lint passed", "2 files formatted"]
        assert {r["name"] for r in rows} == {"hook"} and len({r["call_id"] for r in rows}) == 2
        asyncio.run(uh.announce_hook_messages(None, ["x"]))

    def test_progress_hook_turns_notices_into_rows(self):
        from navin.agent.hook import CompositeHook
        from navin.agent.progress_hook import AgentProgressHook

        rows: list[dict] = []

        async def on_progress(text, *, tool_hint=False, tool_events=None, **_):
            rows.extend(tool_events or [])

        chain = CompositeHook([AgentProgressHook(on_progress=on_progress, session_key="cli:x")])
        asyncio.run(chain.emit_user_notice("Lint passed"))
        assert rows and rows[0]["result"] == "Lint passed"

    def test_project_hooks_need_trust_and_edits_revoke_it(self, env):
        cmd = _hook_script(env["ws"] / "block.sh", "cat >/dev/null; echo blocked >&2; exit 2\n")
        project = _hooks_json(uh.project_hooks_file(env["ws"]), {
            "PreToolUse": [{"hooks": [{"type": "command", "command": cmd}]}],
        })
        assert asyncio.run(uh.pre_tool_use_block(env["ws"], "s", "exec", {})) is None
        assert "not trusted" in uh.hooks_summary(env["ws"])
        assert uh.main(["trust", str(env["ws"])]) == 0
        assert asyncio.run(uh.pre_tool_use_block(env["ws"], "s", "exec", {})) == "blocked"
        assert "- PreToolUse: 1 hook(s)" in uh.hooks_summary(env["ws"])
        project.write_text(project.read_text() + "\n")
        assert asyncio.run(uh.pre_tool_use_block(env["ws"], "s", "exec", {})) is None

    def test_plugin_hooks_use_plugin_root_and_follow_enablement(self, env):
        root = _plugin(env["plugins"])
        _hook_script(root / "hooks/scripts/deny.sh", 'cat >/dev/null; echo "from $NAVIN_PLUGIN_ROOT" >&2; exit 2\n')
        _hooks_json(root / "hooks/hooks.json", {
            "PreToolUse": [{"matcher": "exec", "hooks": [{"type": "command", "command": "bash ${NAVIN_PLUGIN_ROOT}/hooks/scripts/deny.sh"}]}],
        })
        assert asyncio.run(uh.pre_tool_use_block(env["ws"], "s", "exec", {})) == f"from {root}"
        PluginManager(env["plugins"]).set_enabled("kit", False)
        assert asyncio.run(uh.pre_tool_use_block(env["ws"], "s", "exec", {})) is None

    def test_malformed_files_never_raise(self, env):
        _write(env["home"] / ".navin/hooks.json", "{not json")
        assert asyncio.run(uh.run_hooks("PreToolUse", {}, env["ws"], tool_name="exec")).ran == 0
        assert asyncio.run(uh.run_hooks("Unknown", {}, env["ws"])).ran == 0
        assert uh.matches("(", "exec") is False


# -- plugin manager and prompt section ---------------------------------------

class TestPluginComponents:
    def test_list_reports_agents_commands_and_hooks(self, env):
        root = _plugin(env["plugins"], manifest={"commands": ["extra"], "hooks": {"Stop": [{"hooks": []}]}})
        _write(root / "agents/a1.md", "---\nname: a1\ndescription: d\n---\nBody.\n")
        _write(root / "commands/c1.md", "Body.\n")
        _write(root / "extra/c2.md", "Body.\n")
        _hooks_json(root / "hooks/hooks.json", {"PreToolUse": [{"hooks": [{"type": "command", "command": "true"}]}]})
        row = PluginManager(env["plugins"]).get("kit")
        assert row["agents"] == ["a1"] and row["commands"] == ["c1", "c2"]
        assert row["hooks"] == ["PreToolUse", "Stop"]
        from types import SimpleNamespace

        from navin.bus.events import InboundMessage
        from navin.command.builtin import cmd_pack, cmd_plugin

        msg = InboundMessage(channel="cli", sender_id="u", chat_id="c", content="/pack")
        out = asyncio.run(cmd_pack(SimpleNamespace(msg=msg, args="list", loop=None)))
        assert "`kit` (enabled)" in out.content
        assert "1 agent(s), 2 command(s), hooks: PreToolUse, Stop" in out.content
        # /plugin is the real name; /pack stays an alias of it.
        same = asyncio.run(cmd_plugin(SimpleNamespace(msg=msg, args="list", loop=None)))
        assert same.content == out.content

    def test_plugin_install_reports_every_component_and_reloads_mcp_live(self, env, monkeypatch):
        """`/plugin install` answers like Claude's: what the plugin brings, and the MCP
        servers it declares are connected in the running loop, not on the next start."""
        from types import SimpleNamespace

        from navin.bus.events import InboundMessage
        from navin.command import builtin

        src = env["home"] / "src-kit"
        _write(src / ".claude-plugin" / "plugin.json", json.dumps({"name": "srckit", "version": "1.0.0", "description": "Kit"}))
        _write(src / "commands" / "ship.md", "Ship.\n")
        _write(src / "agents" / "rev.md", "---\nname: rev\ndescription: d\n---\nBody.\n")
        _write(src / ".mcp.json", json.dumps({"mcpServers": {"srv": {"command": "node", "args": ["${CLAUDE_PLUGIN_ROOT}/s.js"]}}}))

        synced: list[bool] = []
        monkeypatch.setattr(PluginManager, "sync_mcp_servers", lambda self: synced.append(True) or True)
        reloads: list[tuple] = []

        async def fake_reload(state, registry):
            reloads.append((state, registry))
            return {"ok": True, "connected": ["srckit-srv"], "failed": []}

        monkeypatch.setattr("navin.agent.tools.mcp.reload_servers", fake_reload)
        loop = SimpleNamespace(tools=object(), _mcp_servers={}, _mcp_stacks={})
        msg = InboundMessage(channel="cli", sender_id="u", chat_id="c", content="/plugin")
        out = asyncio.run(builtin.cmd_plugin(SimpleNamespace(msg=msg, args=f"install {src}", loop=loop)))
        assert "Plugin installed: `srckit` v1.0.0" in out.content
        assert "- Commands: `/srckit:ship`" in out.content
        assert "- Agents: rev" in out.content
        assert "- MCP servers: `srckit-srv`" in out.content
        assert "MCP servers: connected: srckit-srv." in out.content
        assert synced == [True]
        assert reloads and reloads[0][0] is loop
        # The command is callable right away under its plugin-qualified name.
        assert cc.find_custom_command(env["ws"], "/srckit:ship") is not None

        # Without a live loop (a script), the reply says the servers connect on the next start.
        out = asyncio.run(builtin.cmd_plugin(SimpleNamespace(msg=msg, args="disable srckit", loop=None)))
        assert "Plugin `srckit` disabled." in out.content
        assert "connect on the next start" in out.content

        # A reload that could not read the config is reported as such, not hidden.
        async def unreadable(state, registry):
            return {"ok": False, "requires_restart": True, "message": "Could not reload MCP config."}

        monkeypatch.setattr("navin.agent.tools.mcp.reload_servers", unreadable)
        out = asyncio.run(builtin.cmd_plugin(SimpleNamespace(msg=msg, args="enable srckit", loop=loop)))
        assert "Plugin `srckit` enabled." in out.content
        assert "live reload did not run (Could not reload MCP config.)" in out.content

    def test_plugin_marketplace_round_trip_in_chat(self, env):
        from types import SimpleNamespace

        from navin.bus.events import InboundMessage
        from navin.command import builtin

        market = env["home"] / "market"
        _write(market / ".claude-plugin" / "marketplace.json", json.dumps({
            "name": "acme", "plugins": [{"name": "deploy", "source": "./plugins/deploy", "description": "Deploys"}],
        }))
        _write(market / "plugins" / "deploy" / "skills" / "deploy" / "SKILL.md", "---\nname: deploy\n---\nD.\n")
        msg = InboundMessage(channel="cli", sender_id="u", chat_id="c", content="/plugin")

        def run(args: str) -> str:
            return asyncio.run(builtin.cmd_plugin(SimpleNamespace(msg=msg, args=args, loop=None))).content

        assert "Marketplace added: `acme` (1 plugin(s))" in run(f"marketplace add {market}")
        assert "`deploy@acme` - Deploys" in run("marketplace list")
        installed = run("install deploy@acme")
        assert "Plugin installed: `deploy`" in installed and "- Skills: deploy" in installed
        listing = run("list")
        assert "`deploy` (enabled)" in listing and "[deploy@acme]" in listing and "Marketplaces (1)" in listing
        assert "Plugin error: plugin nope not in marketplace acme" in run("install nope@acme")
        assert "Marketplace removed: `acme`" in run("marketplace remove acme")
        assert "Plugin removed: `deploy`" in run("uninstall deploy")

    def test_expand_plugin_vars(self, env, monkeypatch):
        monkeypatch.setenv("KIT_TOKEN", "t0k")
        conf = {"args": ["${NAVIN_PLUGIN_ROOT}/s.js"], "headers": {"A": "Bearer ${KIT_TOKEN}"}, "env": {"X": "${UNSET_VAR_XYZ}"}, "toolTimeout": 5}
        out = expand_plugin_vars(conf, Path("/p/kit"))
        assert out == {"args": ["/p/kit/s.js"], "headers": {"A": "Bearer t0k"}, "env": {"X": "${UNSET_VAR_XYZ}"}, "toolTimeout": 5}

    def test_extensions_prompt_section(self, env):
        from navin.agent.context import ContextBuilder

        _write(env["ws"] / ".navin/commands/ship.md", "---\ndescription: Ship it\n---\nShip.\n")
        section = ContextBuilder._extensions_section(env["ws"])
        assert section.startswith("# Extensions")
        assert "hook-development" in section and "`/ship` - Ship it" in section
        assert "Active hooks" not in section
        _hooks_json(env["home"] / ".navin/hooks.json", {"Stop": [{"hooks": [{"type": "command", "command": "true"}]}]})
        assert "- Stop: 1 hook(s)" in ContextBuilder._extensions_section(env["ws"])


def test_python_is_available_for_hook_scripts() -> None:
    assert sys.executable


# -- runner integration -------------------------------------------------------

def _runner_spec(workspace: Path, responses: list, tool_ran: list[str]):
    from typing import Any

    from navin.agent.hook import AgentHook
    from navin.agent.runner import AgentRunSpec
    from navin.agent.tools.base import Tool
    from navin.agent.tools.registry import ToolRegistry
    from navin.providers.base import GenerationSettings
    from navin.utils.llm_runtime import LLMRuntime

    class Echo(Tool):
        @property
        def name(self) -> str:
            return "echo_tool"

        @property
        def description(self) -> str:
            return "echo"

        @property
        def parameters(self) -> dict[str, Any]:
            return {"type": "object", "properties": {}}

        async def execute(self, **kwargs: Any) -> str:
            tool_ran.append("ran")
            return "ECHO OK"

    class Provider:
        def __init__(self) -> None:
            self.calls = 0

        async def chat_with_retry(self, **_kwargs: Any):
            self.calls += 1
            return responses.pop(0) if len(responses) > 1 else responses[0]

        async def chat_stream_with_retry(self, **kwargs: Any):
            return await self.chat_with_retry(**kwargs)

    registry = ToolRegistry()
    registry.register(Echo())
    provider = Provider()
    spec = AgentRunSpec(
        initial_messages=[{"role": "user", "content": "go"}],
        tools=registry,
        runtime=LLMRuntime(provider=provider, model="m", generation=GenerationSettings(), context_window_tokens=128_000),
        max_iterations=8,
        max_tool_result_chars=5000,
        hook=AgentHook(),
        workspace=workspace,
        session_key="cli:hooks-test",
    )
    return spec, provider


class TestRunnerIntegration:
    def test_loop_expands_commands_and_applies_prompt_hooks(self, env):
        from types import SimpleNamespace

        from navin.agent.loop import AgentLoop
        from navin.bus.events import InboundMessage

        ws = env["ws"]
        _write(ws / ".navin/commands/ro.md", "---\nallowed-tools: Read\n---\nInspect $1 only.\n")
        fake = SimpleNamespace(
            commands=SimpleNamespace(is_dispatchable_command=lambda text: text.startswith("/help")),
            workspace_scopes=SimpleNamespace(for_message=lambda *_: SimpleNamespace(project_path=ws)),
            workspace=ws,
        )

        def turn(text: str):
            msg = InboundMessage(channel="cli", sender_id="u", chat_id="c", content=text)
            ctx = SimpleNamespace(msg=msg, session=None, session_key="cli:c")
            return ctx, asyncio.run(AgentLoop._apply_user_extensions(fake, ctx, text))

        ctx, reply = turn("/ro src/a.py")
        assert reply is None
        assert ctx.msg.content == "[Command /ro src/a.py]\nInspect src/a.py only."
        assert ctx.msg.metadata["custom_command_allowed_tools"] == ["read_file", "skill"]
        assert ctx.msg.metadata["original_content"] == "/ro src/a.py"
        ctx, _ = turn("/unknown thing")
        assert ctx.msg.content == "/unknown thing"

        guard = _hook_script(env["home"] / "g.sh", 'input=$(cat); [[ "$input" == *forbidden* ]] && { echo nope >&2; exit 2; }; echo "ctx line"\n')
        _hooks_json(env["home"] / ".navin/hooks.json", {"UserPromptSubmit": [{"hooks": [{"type": "command", "command": guard}]}]})
        ctx, reply = turn("hello")
        assert reply is None and ctx.msg.content == "hello\n\n[Hook context]\nctx line"
        _, reply = turn("a forbidden word")
        assert reply is not None and "UserPromptSubmit hook: nope" in reply.content

        note = _hook_script(env["home"] / "m.sh", "cat >/dev/null; echo '{\"systemMessage\": \"Spell check on\"}'\n")
        _hooks_json(env["home"] / ".navin/hooks.json", {"UserPromptSubmit": [{"hooks": [{"type": "command", "command": note}]}]})
        ctx, reply = turn("hello")
        assert reply is None and ctx.msg.content == "hello"
        assert ctx.msg.metadata["hook_system_messages"] == ["Spell check on"]

    def test_pre_tool_block_skips_the_tool_and_post_feedback_is_appended(self, env):
        from navin.agent.runner import AgentRunner
        from navin.providers.base import LLMResponse, ToolCallRequest

        block = _hook_script(env["home"] / "b.sh", "cat >/dev/null; echo 'echo is frozen' >&2; exit 2\n")
        _hooks_json(env["home"] / ".navin/hooks.json", {
            "PreToolUse": [{"matcher": "echo_tool", "hooks": [{"type": "command", "command": block}]}],
        })
        ran: list[str] = []
        call = LLMResponse(content=None, tool_calls=[ToolCallRequest(id="c1", name="echo_tool", arguments={})])
        spec, _ = _runner_spec(env["ws"], [call, LLMResponse(content="done", finish_reason="stop")], ran)
        result = asyncio.run(AgentRunner().run(spec))
        tool_msgs = [m for m in result.messages if m.get("role") == "tool"]
        assert ran == []
        assert "PreToolUse hook blocked 'echo_tool': echo is frozen" in str(tool_msgs[0]["content"])

        note = _hook_script(env["home"] / "n.sh", "cat >/dev/null; echo '{\"decision\": \"block\", \"reason\": \"add a test\"}'\n")
        _hooks_json(env["home"] / ".navin/hooks.json", {
            "PostToolUse": [{"hooks": [{"type": "command", "command": note}]}],
        })
        spec, _ = _runner_spec(env["ws"], [call, LLMResponse(content="done", finish_reason="stop")], ran)
        result = asyncio.run(AgentRunner().run(spec))
        tool_msgs = [m for m in result.messages if m.get("role") == "tool"]
        assert ran == ["ran"]
        assert "ECHO OK" in str(tool_msgs[0]["content"])
        assert "[PostToolUse hook] add a test" in str(tool_msgs[0]["content"])

    def test_stop_hook_forces_one_more_round_then_lets_go(self, env):
        from navin.agent.runner import AgentRunner
        from navin.providers.base import LLMResponse

        stop = _hook_script(
            env["home"] / "s.sh",
            'input=$(cat); if [[ "$input" == *\'"stop_hook_active": true\'* ]]; then exit 0; fi\n'
            "echo 'tests are red' >&2; exit 2\n",
        )
        _hooks_json(env["home"] / ".navin/hooks.json", {"Stop": [{"hooks": [{"type": "command", "command": stop}]}]})
        spec, provider = _runner_spec(
            env["ws"],
            [LLMResponse(content="first", finish_reason="stop"), LLMResponse(content="fixed", finish_reason="stop")],
            [],
        )
        result = asyncio.run(AgentRunner().run(spec))
        assert provider.calls == 2
        assert result.final_content == "fixed"
        assert "[Stop hook] tests are red" in str(result.messages)

    def test_stop_hook_rounds_are_capped(self, env):
        from navin.agent.runner import _MAX_STOP_HOOK_ROUNDS, AgentRunner
        from navin.providers.base import LLMResponse

        stop = _hook_script(env["home"] / "s.sh", "cat >/dev/null; echo never >&2; exit 2\n")
        _hooks_json(env["home"] / ".navin/hooks.json", {"Stop": [{"hooks": [{"type": "command", "command": stop}]}]})
        spec, provider = _runner_spec(env["ws"], [LLMResponse(content="answer", finish_reason="stop")], [])
        result = asyncio.run(AgentRunner().run(spec))
        assert provider.calls == _MAX_STOP_HOOK_ROUNDS + 1
        assert result.final_content == "answer"
